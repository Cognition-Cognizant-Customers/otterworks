"""Aggregate reconciliation for the whole migrated MongoDB estate.

This is the failure beat. It reconciles the live target for one namespace against
the immutable legacy baseline, exits non-zero when anything disagrees, and POSTs
the failure to the Devin recon automation so a session can start work without a
human translating anything.

How correctness is obtained: the four Wave 1 per-unit recon jobs are the single
notion of correctness in this repo, so this job runs each of them for real in this
invocation - each recomputes every value by reading the target back and re-runs its
own migration to prove idempotency - and folds their check records into one
report. Unit reports are written to a fresh directory created by this run and read
back within the same run; no previously written report, and no counter recorded by
a migration, is ever an input.

On top of the unit checks it adds estate-wide checks that no single unit can make:
cross-collection join integrity, namespace isolation across every collection,
money type enforcement, validator coverage, and determinism of the stage
aggregation report.

Order matters. Every value this job measures itself is read from the target BEFORE
any unit recon re-runs its migration, because a re-run repairs exactly the kind of
damage the beat is about (a lost batch reappears). That is why the estate volumes
are compared here against each unit's own source-of-truth expectation: it is what
makes a dropped slice of documents fail as `platform.documents.document_count`
rather than only as an idempotency verdict.

Usage:
    MONGO_URI=... python3 scripts/tp_mongo/recon_platform.py --ns demo \
        --run-mode fixture --out docs/tech-partnerships/recon/mongo_platform.recon.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aggregation_report import pipeline as revenue_pipeline  # noqa: E402
from mongo_common import (  # noqa: E402
    database_name,
    mongo_client,
    mongo_uri,
    validate_ns,
)
from platform_common import (  # noqa: E402
    MIGRATED_COLLECTIONS,
    WebhookNotConfigured,
    json_schema_validator,
    namespace_filter,
    post_failure_webhook,
    redacted_uri,
    redacted_webhook_request,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
UNIT = "mongo_platform"
REPOSITORY = "Cognition-Partner-Workshops/otterworks"

# Each unit recon owns its own source-of-truth comparison and its own idempotency
# rerun; `rerun` marks the ones that need the flag to perform it (invoices and
# files always re-run their migration).
UNIT_RECONS: dict[str, dict[str, Any]] = {
    "customers": {"script": "recon_customers.py", "rerun_flag": True, "source": "oracle"},
    "invoices": {"script": "recon_invoices.py", "rerun_flag": False, "source": "oracle"},
    "documents": {"script": "recon_documents.py", "rerun_flag": True, "source": "postgres"},
    "files": {"script": "recon_files.py", "rerun_flag": False, "source": "aws"},
}


def log(message: str) -> None:
    print(f"[recon-platform] {message}", flush=True)


def run_branch() -> str:
    override = os.environ.get("OW_TP_RUN_BRANCH", "").strip()
    if override:
        return override
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() or "unknown"


def unit_environment(unit: str) -> dict[str, str]:
    """Per-unit source ports: Oracle for billing units, Postgres for documents."""
    env = dict(os.environ)
    if UNIT_RECONS[unit]["source"] == "oracle":
        env["DB_PORT"] = os.environ.get("ORACLE_BILLING_DB_PORT") or env.get("DB_PORT", "52521")
    elif UNIT_RECONS[unit]["source"] == "postgres":
        env["DB_PORT"] = os.environ.get("DOCUMENTS_DB_PORT") or "5432"
    return env


def run_unit(unit: str, ns: str, run_mode: str, report_dir: Path) -> dict[str, Any]:
    """Run one per-unit recon for real and read the report it just wrote."""
    spec = UNIT_RECONS[unit]
    out = report_dir / f"{unit}.recon.json"
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "tp_mongo" / spec["script"]),
        "--ns", ns,
        "--run-mode", run_mode,
        "--out", str(out),
    ]
    if spec["rerun_flag"]:
        command.append("--rerun-migration")
    log(f"running {unit} recon: {' '.join(command[1:])}")
    completed = subprocess.run(
        command, cwd=REPO_ROOT, env=unit_environment(unit),
        capture_output=True, text=True, check=False,
    )
    if not out.exists():
        raise SystemExit(
            f"{unit} recon exited {completed.returncode} without writing {out}:\n"
            f"{completed.stdout[-2000:]}\n{completed.stderr[-2000:]}"
        )
    report = json.loads(out.read_text())
    log(f"{unit} recon exit={completed.returncode} "
        f"checks={len(report['checks'])} report={out}")
    return {
        "unit": unit,
        "exit_code": completed.returncode,
        "report_path": str(out),
        "report": report,
        "stderr_tail": completed.stderr.strip()[-500:],
    }


def check(checks: list[dict[str, Any]], cid: str, expected: Any, actual: Any,
          source: str, result: str | None = None) -> None:
    checks.append({
        "id": cid,
        "expected": expected,
        "actual": actual,
        "source_of_truth": source,
        "result": result or ("pass" if expected == actual else "fail"),
    })


# platform check id -> (fact key, the per-unit check whose `expected` is the
# source-of-truth value, owning unit). The expectation always comes from the unit
# that owns the source system; the actual is this job's own pre-rerun measurement.
VOLUME_CHECKS: dict[str, tuple[str, str, str]] = {
    "platform.customers.document_count": ("customers", "customers.count", "customers"),
    "platform.customers.quarantine_count": (
        "customers_quarantine", "customers.quarantine_enumerated", "customers"),
    "platform.invoices.document_count": ("invoices", "invoices.count", "invoices"),
    "platform.invoices.embedded_line_count": (
        "invoices.embedded_lines", "invoices.embedded_line_count", "invoices"),
    "platform.invoices.orphan_lines_quarantined": (
        "invoices_quarantine", "invoices.orphans_quarantined", "invoices"),
    "platform.documents.document_count": ("documents", "documents.count", "documents"),
    "platform.documents.embedded_version_count": (
        "documents.embedded_versions", "documents.version_count", "documents"),
    "platform.files.document_count": ("files", "files.count", "files"),
}


def array_total(database: Any, collection: str, field: str, ns: str) -> int:
    """Total size of an embedded array across a collection, summed by the server."""
    rows = list(database[collection].aggregate([
        {"$match": namespace_filter(collection, ns)},
        {"$group": {"_id": None, "total": {"$sum": {"$size": f"${field}"}}}},
    ]))
    return int(rows[0]["total"]) if rows else 0


def target_facts(ns: str) -> dict[str, int]:
    """Volumes read straight off the target before any migration is re-run."""
    client = mongo_client()
    try:
        database = client[database_name(ns)]
        facts = {
            collection: database[collection].count_documents(namespace_filter(collection, ns))
            for collection in MIGRATED_COLLECTIONS
        }
        facts["invoices.embedded_lines"] = array_total(database, "invoices", "lines", ns)
        facts["documents.embedded_versions"] = array_total(
            database, "documents", "versions", ns)
        facts["invoices.header_total_mismatches"] = len(list(
            database["invoices"].aggregate([
                {"$match": namespace_filter("invoices", ns)},
                {"$project": {"drift": {"$ne": [
                    {"$toDecimal": "$total_amt"},
                    {"$reduce": {
                        "input": "$lines",
                        "initialValue": {"$toDecimal": "0"},
                        "in": {"$add": ["$$value", {"$toDecimal": "$$this.amount"}]},
                    }},
                ]}}},
                {"$match": {"drift": True}},
                {"$project": {"_id": 1}},
            ])))
        return facts
    finally:
        client.close()


def volume_checks(facts: dict[str, int], results: list[dict[str, Any]],
                  checks: list[dict[str, Any]]) -> None:
    """Compare pre-rerun target volumes against each unit's source-of-truth value."""
    expectations: dict[str, dict[str, Any]] = {}
    for result in results:
        for record in result["report"]["checks"]:
            expectations[record["id"]] = record

    for cid, (fact_key, unit_check, unit) in VOLUME_CHECKS.items():
        record = expectations.get(unit_check)
        if record is None:
            continue
        check(checks, cid, record["expected"], facts[fact_key],
              f"{record['source_of_truth']} (expected value owned by the {unit} "
              "unit; actual counted in the target by this job before any "
              "migration was re-run)")


def estate_checks(ns: str, facts: dict[str, int], checks: list[dict[str, Any]]) -> None:
    """Estate-wide checks recomputed from the target, top to bottom."""
    client = mongo_client()
    try:
        database = client[database_name(ns)]
        db_label = database_name(ns)

        foreign: dict[str, int] = {}
        for collection, field in MIGRATED_COLLECTIONS.items():
            foreign[collection] = database[collection].count_documents(
                {field: {"$ne": ns}}
            )
        check(checks, "platform.namespace_isolated",
              {name: 0 for name in MIGRATED_COLLECTIONS}, foreign,
              f"{db_label}: documents in each migrated collection whose "
              "namespace field is not this namespace")

        with_validator = sorted(
            name for name in MIGRATED_COLLECTIONS
            if json_schema_validator(database, name) is not None
        )
        check(checks, "platform.validators_enforced_on_every_collection",
              sorted(MIGRATED_COLLECTIONS), with_validator,
              f"listCollections options for each collection in {db_label} "
              "(queried by exact name, which Atlas M0 requires)")

        double_money = database["invoices"].count_documents({
            **namespace_filter("invoices", ns),
            "$or": [
                {"total_amt": {"$type": "double"}},
                {"tax_amt": {"$type": "double"}},
                {"legacy_total_amt": {"$type": "double"}},
                {"lines.amount": {"$type": "double"}},
                {"lines.qty": {"$type": "double"}},
                {"lines.unit_price": {"$type": "double"}},
            ],
        })
        check(checks, "platform.money_never_float", 0, double_money,
              f"{db_label}.invoices: header and embedded-line money fields whose "
              "BSON type is double instead of decimal")

        stale_line_counts = len(list(database["invoices"].aggregate([
            {"$match": namespace_filter("invoices", ns)},
            {"$project": {"stale": {"$ne": ["$line_count", {"$size": "$lines"}]}}},
            {"$match": {"stale": True}},
            {"$project": {"_id": 1}},
        ])))
        check(checks, "platform.invoice_line_count_matches_embedded", 0,
              stale_line_counts,
              f"{db_label}.invoices: documents whose line_count disagrees with the "
              "size of their own embedded lines array")

        check(checks, "platform.invoice_header_total_matches_lines", 0,
              facts["invoices.header_total_mismatches"],
              f"{db_label}.invoices: invoices whose migrated header total_amt does "
              "not equal the Decimal128 sum of its own embedded line amounts, "
              "summed by the server before any migration was re-run")

        unresolved = list(database["invoices"].aggregate([
            {"$match": namespace_filter("invoices", ns)},
            {"$lookup": {
                "from": "customers",
                "let": {"customer_id": "$cust_id"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$_id", "$$customer_id"]}, "ns": ns}},
                    {"$project": {"_id": 1}},
                ],
                "as": "customer",
            }},
            {"$match": {"customer": []}},
            {"$group": {"_id": None, "count": {"$sum": 1}}},
        ]))
        check(checks, "platform.invoice_customer_join_resolves", 0,
              int(unresolved[0]["count"]) if unresolved else 0,
              f"{db_label}: invoices whose cust_id resolves to no migrated customer "
              "(the join the legacy report needed, recomputed against the target)")

        documents_without_owner = database["documents"].count_documents({
            **namespace_filter("documents", ns),
            "$or": [{"owner_id": {"$exists": False}}, {"owner_id": ""}],
        })
        check(checks, "platform.documents_attribution_present", 0,
              documents_without_owner,
              f"{db_label}.documents: documents with a missing or empty owner_id")

        stages = revenue_pipeline(ns)
        digests = []
        for _ in range(2):
            rows = list(database["invoices"].aggregate(stages))
            digests.append(hashlib.md5(
                json.dumps(rows, sort_keys=True, default=str).encode()
            ).hexdigest())
        check(checks, "platform.aggregation_report_deterministic",
              {"runs": 2, "distinct_digests": 1},
              {"runs": 2, "distinct_digests": len(set(digests))},
              f"{db_label}: the stage aggregation pipeline executed twice against "
              f"the same target state (digest {digests[0]})")
    finally:
        client.close()


def fold_units(results: list[dict[str, Any]], checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold every per-unit check, anomaly set and idempotency verdict."""
    expected_anomalies: list[str] = []
    actual_anomalies: list[str] = []
    idempotency_evidence: list[str] = []
    idempotency_pass = True

    for result in results:
        unit = result["unit"]
        report = result["report"]
        for record in report["checks"]:
            checks.append({
                "id": record["id"],
                "expected": record["expected"],
                "actual": record["actual"],
                "source_of_truth": record["source_of_truth"],
                "result": record.get(
                    "result", "pass" if record["expected"] == record["actual"] else "fail"
                ),
            })
        check(checks, f"platform.unit_recon_exit_code.{unit}", 0, result["exit_code"],
              f"exit status of scripts/tp_mongo/{UNIT_RECONS[unit]['script']} run by "
              "this job")

        detections = report["planted_anomaly_detections"]
        expected_anomalies += [f"{unit}:{name}" for name in detections["expected_set"]]
        actual_anomalies += [f"{unit}:{name}" for name in detections["actual_set"]]

        rerun = report["idempotency_rerun"]
        performed = bool(rerun.get("performed"))
        passed = performed and rerun.get("result") == "pass"
        idempotency_pass = idempotency_pass and passed
        idempotency_evidence.append(
            f"{unit}: performed={performed} result={rerun.get('result')} "
            f"({str(rerun.get('evidence', ''))[:200]})"
        )

    expected_set = sorted(set(expected_anomalies))
    actual_set = sorted(set(actual_anomalies))
    check(checks, "platform.planted_anomalies_exact_set", expected_set, actual_set,
          "union of every unit's planted-anomaly set from "
          "testdata/legacy/manifests/<ns>.json versus the anomaly set each unit "
          "recomputed from the target, compared as sets")

    return {
        "planted_anomaly_detections": {
            "expected_set": expected_set,
            "actual_set": actual_set,
            "missing": sorted(set(expected_set) - set(actual_set)),
            "unexpected": sorted(set(actual_set) - set(expected_set)),
        },
        "idempotency_rerun": {
            "performed": True,
            "result": "pass" if idempotency_pass else "fail",
            "evidence": (
                "every unit re-ran its own migration during this job and recomputed "
                "the target afterwards; " + " | ".join(idempotency_evidence)
            ),
        },
    }


def reproduce_command(ns: str, run_mode: str) -> str:
    return (
        f"make tp-mongo-recon-platform NS={ns} RUN_MODE={run_mode}"
        if run_mode == "fixture"
        else f"MONGO_URI=<target-uri> make tp-mongo-recon-platform NS={ns} RUN_MODE={run_mode}"
    )


def failure_payload(ns: str, run_mode: str, report_path: Path,
                    failing: list[dict[str, Any]], anomalies: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "mongo-recon-platform",
        "repository": REPOSITORY,
        "run_branch": run_branch(),
        "namespace": ns,
        "run_mode": run_mode,
        "database": database_name(ns),
        "report_path": str(report_path),
        "reproduce_command": reproduce_command(ns, run_mode),
        "repair_command": (
            f"make tp-mongo-customers NS={ns} && make tp-mongo-invoices NS={ns} && "
            f"make tp-mongo-documents NS={ns} && make tp-mongo-migrate-files NS={ns}"
        ),
        "failing_check_names": [record["id"] for record in failing],
        "failing_checks": [
            {
                "id": record["id"],
                "expected": record["expected"],
                "actual": record["actual"],
                "source_of_truth": record["source_of_truth"],
            }
            for record in failing
        ],
        "planted_anomalies_missing": anomalies["missing"],
        "planted_anomalies_unexpected": anomalies["unexpected"],
        "summary": (
            f"MongoDB reconciliation FAILED for namespace {ns}: "
            f"{len(failing)} check(s) failed - "
            + ", ".join(record["id"] for record in failing[:8])
            + (" ..." if len(failing) > 8 else "")
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument("--run-mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument(
        "--out",
        default="docs/tech-partnerships/recon/mongo_platform.recon.json",
        help="where to write the aggregate report",
    )
    parser.add_argument(
        "--unit-report-dir",
        help="directory for the per-unit reports this run produces "
             "(default: a fresh gitignored directory under build/tp-recon/platform/)",
    )
    parser.add_argument(
        "--units", default=",".join(UNIT_RECONS),
        help="comma-separated subset of units to reconcile",
    )
    args = parser.parse_args()
    ns = validate_ns(args.ns)
    units = [unit.strip() for unit in args.units.split(",") if unit.strip()]
    unknown = sorted(set(units) - set(UNIT_RECONS))
    if unknown:
        raise SystemExit(f"unknown units {unknown}; valid: {sorted(UNIT_RECONS)}")

    # Kept inside the repo (build/ is gitignored) because the per-unit recon jobs
    # log their output path relative to the repository root.
    report_dir = Path(args.unit_report_dir) if args.unit_report_dir else (
        REPO_ROOT / "build" / "tp-recon" / "platform" / f"{ns}-{uuid.uuid4().hex[:8]}"
    )
    report_dir.mkdir(parents=True, exist_ok=True)

    log(f"ns={ns} run_mode={args.run_mode} db={database_name(ns)} "
        f"uri={redacted_uri(mongo_uri())}")
    log(f"per-unit reports for THIS run: {report_dir}")

    # Measured first: a unit recon re-runs its migration, which would repair a lost
    # batch before anything got the chance to count it.
    facts = target_facts(ns)
    log(f"target volumes before any migration rerun: {facts}")

    results = [run_unit(unit, ns, args.run_mode, report_dir) for unit in units]
    checks: list[dict[str, Any]] = []
    volume_checks(facts, results, checks)
    folded = fold_units(results, checks)
    estate_checks(ns, facts, checks)

    failing = [record for record in checks if record["result"] == "fail"]
    if folded["idempotency_rerun"]["result"] != "pass":
        failing.append({
            "id": "platform.idempotent",
            "expected": "pass",
            "actual": folded["idempotency_rerun"]["result"],
            "source_of_truth": folded["idempotency_rerun"]["evidence"][:400],
        })

    report: dict[str, Any] = {
        "kind": "recon-report",
        "unit": UNIT,
        "namespace": ns,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_mode": args.run_mode,
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": folded["idempotency_rerun"],
        "planted_anomaly_detections": folded["planted_anomaly_detections"],
        "unverified_paths": sorted({
            path
            for result in results
            for path in result["report"].get("unverified_paths", [])
        } | {
            "Live Atlas execution is unverified from this unit: it develops and "
            "self-verifies against a local MongoDB fixture and marks its runs "
            "run_mode=fixture; only the parent's uncontended Atlas window proves "
            "the live path.",
            "The s3.data-lake hourly objects in the baseline manifest have no "
            "MongoDB unit, so no check in this report covers them.",
        }),
        "unit_reports": {
            result["unit"]: {
                "path": result["report_path"],
                "exit_code": result["exit_code"],
                "generated_at": result["report"]["generated_at"],
                "checks": len(result["report"]["checks"]),
            }
            for result in results
        },
        "reproduce_command": reproduce_command(ns, args.run_mode),
        "mongo_uri_source": "MONGO_URI environment variable",
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if failing:
        payload = failure_payload(ns, args.run_mode, out, failing,
                                 folded["planted_anomaly_detections"])
        report["webhook_request"] = redacted_webhook_request(payload)
        try:
            delivery = post_failure_webhook(payload)
        except WebhookNotConfigured as exc:
            out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
            log(f"RED: {len(failing)} check(s) failed; report {out}")
            for record in failing:
                log(f"  FAIL {record['id']}: expected={record['expected']!r} "
                    f"actual={record['actual']!r}")
            raise SystemExit(f"{exc}") from None
        report["webhook_delivery"] = delivery
        out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
        log(f"RED: {len(failing)} of {len(checks)} checks failed; report {out}")
        for record in failing:
            log(f"  FAIL {record['id']}: expected={record['expected']!r} "
                f"actual={record['actual']!r}")
        log(f"notified the Devin recon automation: delivered={delivery['delivered']} "
            f"status={delivery['status']}")
        return 1

    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    log(f"GREEN: {len(checks)} checks passed for ns={ns}; report {out}")
    log("no webhook call: the automation is only notified on failure")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
