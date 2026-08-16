# /// script
# requires-python = ">=3.11"
# dependencies = ["pymongo"]
# ///
"""mongo_documents reconciliation: recompute counts/checksums/anomaly sets
FROM the target MongoDB and diff them against the golden baseline manifest.

Nothing here reads migration-time memory: every value is re-derived from
ow_tp_mongodb_<ns>.documents and ow_tp_mongodb_<ns>_quarantine.documents_quarantine.

Checksum line formats mirror testdata/legacy/seed.py exactly:
  documents:  "<doc_id>|<version>|<word_count>"
  versions:   "<doc_id>|<version_number>"       (one line per embedded version)
  snapshots:  "<snap_id>|<document_id>"          (embedded refs + quarantined orphans)

Emits a recon report valid against
docs/tech-partnerships/contracts/schema/recon-report.schema.json.

Usage (two-pass, because the report schema requires a proven rerun):
    uv run migrations/mongodb/mongo_documents/recon.py --ns <ns> --out /tmp/baseline.json
    uv run migrations/mongodb/mongo_documents/migrate.py --ns <ns>   # rerun
    uv run migrations/mongodb/mongo_documents/recon.py --ns <ns> \
        --idempotency-rerun-performed --baseline /tmp/baseline.json

Without --idempotency-rerun-performed the output is a `recon-baseline`
snapshot (not a recon-report): the report schema constrains
`idempotency_rerun.performed` to `true`, so only a rerun-proven pass may emit
the final `*.recon.json` artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient

from common import (
    UNIT,
    Checksum,
    contiguous_gaps,
    mongo_uri,
    pg_schema,
    quarantine_db_name,
    target_db_name,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def log(msg: str) -> None:
    print(f"[{UNIT}-recon] {msg}", flush=True)


def recompute(ns: str) -> dict:
    """Re-derive all recon values from the target MongoDB."""
    client: MongoClient = MongoClient(mongo_uri())
    try:
        target = client[target_db_name(ns)]["documents"]
        quarantine = client[quarantine_db_name(ns)]["documents_quarantine"]

        doc_ck, ver_ck, snap_ck = Checksum(), Checksum(), Checksum()
        doc_count = 0
        version_count = 0
        snapshot_count = 0
        gap_docs: dict[str, list[int]] = {}

        cursor = target.find(
            {"unit": UNIT, "ns": ns},
            {"source_id": 1, "version": 1, "word_count": 1,
             "versions.version_number": 1, "snapshots.source_id": 1},
        )
        for doc in cursor:
            doc_count += 1
            src = doc["source_id"]
            doc_ck.add(f"{src}|{doc['version']}|{doc['word_count']}")
            numbers = [v["version_number"] for v in doc.get("versions", [])]
            for n in numbers:
                ver_ck.add(f"{src}|{n}")
            version_count += len(numbers)
            gaps = contiguous_gaps(numbers, doc["version"])
            if gaps:
                gap_docs[src] = gaps
            for snap in doc.get("snapshots", []):
                snap_ck.add(f"{snap['source_id']}|{src}")
                snapshot_count += 1

        orphaned = sorted(
            q["source"]["id"]
            for q in quarantine.find({"unit": UNIT, "ns": ns,
                                      "kind": "orphaned_snapshot"})
        )
        for q in quarantine.find({"unit": UNIT, "ns": ns,
                                  "kind": "orphaned_snapshot"}):
            snap_ck.add(f"{q['source']['id']}|{q['source']['document_id']}")
            snapshot_count += 1
        policy_violations = quarantine.count_documents(
            {"unit": UNIT, "ns": ns, "kind": "policy_violation"})
    finally:
        client.close()

    return {
        "documents": doc_count,
        "versions_embedded": version_count,
        "snapshots_total": snapshot_count,
        "documents_checksum": doc_ck.hexdigest(),
        "versions_checksum": ver_ck.hexdigest(),
        "snapshots_checksum": snap_ck.hexdigest(),
        "version_gap_documents": dict(sorted(gap_docs.items())),
        "orphaned_snapshot_ids": orphaned,
        "policy_violation_quarantine_count": policy_violations,
    }


def check(check_id: str, expected, actual, source_of_truth: str) -> dict:
    return {
        "id": check_id,
        "expected": expected,
        "actual": actual,
        "source_of_truth": source_of_truth,
        "result": "pass" if expected == actual else "fail",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument("--run-mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--out")
    parser.add_argument("--idempotency-rerun-performed", action="store_true",
                        help="Set after a rerun of migrate.py; compares this "
                             "recompute against --baseline for identity.")
    parser.add_argument("--baseline",
                        help="Prior recon report to prove the rerun reproduced "
                             "identical numbers.")
    args = parser.parse_args()
    ns = args.ns
    schema = pg_schema(ns)

    manifest_path = REPO_ROOT / "testdata" / "legacy" / "manifests" / f"{ns}.json"
    manifest = json.loads(manifest_path.read_text())
    targets = manifest["targets"]
    m_docs = targets[f"postgres.{schema}.documents"]
    m_vers = targets[f"postgres.{schema}.document_versions"]
    m_snaps = targets[f"postgres.{schema}.document_snapshots"]
    expected_anomalies = sorted(
        (
            {"kind": a["kind"], "count": a["count"]}
            for a in manifest["planted_anomalies"]
            if a["target"].startswith(f"postgres.{schema}.")
        ),
        key=lambda a: a["kind"],
    )

    actual = recompute(ns)
    src = f"recomputed from MongoDB {target_db_name(ns)} / {quarantine_db_name(ns)}"
    manifest_ref = f"golden baseline testdata/legacy/manifests/{ns}.json"

    actual_anomalies = sorted(
        [
            {"kind": "orphaned_snapshots", "count": len(actual["orphaned_snapshot_ids"])},
            {"kind": "version_gaps", "count": len(actual["version_gap_documents"])},
        ],
        key=lambda a: a["kind"],
    )
    missing = [a for a in expected_anomalies if a not in actual_anomalies]
    unexpected = [a for a in actual_anomalies if a not in expected_anomalies]

    checks = [
        check("documents-count", m_docs["rows"], actual["documents"],
              f"{manifest_ref} vs {src}"),
        check("versions-embedded", m_vers["rows"], actual["versions_embedded"],
              f"{manifest_ref} vs {src}"),
        check("checksums",
              {"documents": m_docs["checksum"], "versions": m_vers["checksum"],
               "snapshots": m_snaps["checksum"]},
              {"documents": actual["documents_checksum"],
               "versions": actual["versions_checksum"],
               "snapshots": actual["snapshots_checksum"]},
              f"{manifest_ref} vs {src}"),
        check("version-gaps-reported",
              next(a["count"] for a in expected_anomalies if a["kind"] == "version_gaps"),
              len(actual["version_gap_documents"]),
              f"{manifest_ref} vs {src} (enumerated, not repaired)"),
        check("orphaned-snapshots-reported",
              next(a["count"] for a in expected_anomalies if a["kind"] == "orphaned_snapshots"),
              len(actual["orphaned_snapshot_ids"]),
              f"{manifest_ref} vs {src} (quarantined with attribution)"),
        check("snapshots-total", m_snaps["rows"], actual["snapshots_total"],
              f"{manifest_ref} vs {src} (embedded refs + quarantined orphans)"),
        check("policy-violation-quarantine", 0,
              actual["policy_violation_quarantine_count"], src),
    ]

    if args.idempotency_rerun_performed:
        if not args.baseline:
            print("--idempotency-rerun-performed requires --baseline", file=sys.stderr)
            return 2
        baseline = json.loads(Path(args.baseline).read_text())
        identical = baseline.get("recomputed_values") == actual
        idempotency = {
            "performed": True,
            "result": "pass" if identical else "fail",
            "evidence": ("rerun of migrate.py followed by a fresh recompute "
                         "produced " +
                         ("identical" if identical else "DIFFERENT") +
                         " counts, checksums, and anomaly enumerations"),
        }

    else:
        if not args.out:
            print("without --idempotency-rerun-performed this run is a baseline "
                  "snapshot and requires --out (the schema-valid recon report "
                  "needs a proven rerun)", file=sys.stderr)
            return 2
        idempotency = None

    report = {
        "kind": "recon-report" if idempotency else "recon-baseline",
        "unit": UNIT,
        "namespace": ns,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_mode": args.run_mode,
        "checks": checks,
        "values_recomputed_from_target": True,
        **({"idempotency_rerun": idempotency} if idempotency else {}),
        "planted_anomaly_detections": {
            "expected_set": expected_anomalies,
            "actual_set": actual_anomalies,
            "missing": missing,
            "unexpected": unexpected,
        },
        "anomaly_enumeration": {
            "version_gap_documents": actual["version_gap_documents"],
            "orphaned_snapshot_ids": actual["orphaned_snapshot_ids"],
        },
        "unverified_paths": ([
            "live Atlas run (this report is run_mode=fixture; the parent owns the live window)",
        ] if args.run_mode == "fixture" else []) + [
            "invalid-byte quarantine path (not expected from Postgres UTF-8; exercised only by unit policy, no such rows exist in the seeded estate)",
            "full-scale memory behavior (validated only at M0/demo scale)",
        ],
        "recomputed_values": actual,
    }

    out = Path(args.out) if args.out else (
        REPO_ROOT / "docs" / "tech-partnerships" / "recon"
        / f"{UNIT}.{ns}.recon.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    failed = [c["id"] for c in checks if c["result"] != "pass"]
    log(f"report written: {out}")
    log(f"checks: {len(checks) - len(failed)}/{len(checks)} pass"
        + (f" (FAILED: {failed})" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
