#!/usr/bin/env python3
"""Reconcile the migrated ow_tp_<ns> document estate against the legacy baseline.

Every number in the emitted report is recomputed by reading the target MongoDB
deployment back after the write; nothing is carried over from the migration's own
counters. The expected sides come from the immutable seed manifest and from the
legacy Postgres estate itself.

Usage:
    MONGO_URI=... uv run --no-project --with pymongo==4.10.1 \
        --with psycopg2-binary==2.9.10 python3 scripts/tp_mongo/recon_documents.py \
        --ns demo --run-mode fixture --out docs/tech-partnerships/recon/mongo_documents.recon.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    DOCUMENTS,
    QUARANTINE,
    ROOT,
    SNAPSHOTS,
    legacy_common,
    manifest,
    mongo_client,
    mongo_uri,
    pg_connect,
    redacted_uri,
    source_schema,
    target_db_name,
    validate_namespace,
)
from documents_model import (
    VERSION_ARRAY_BOUND,
    VersionSequenceOverBound,
    missing_versions_for,
)

UNIT = "mongo_documents"

# Emitted verbatim in the report: the single command that recomputes every
# number in it against whatever deployment MONGO_URI points at.
RECOMPUTE_COMMAND = (
    "MONGO_URI='<target uri>' MONGO_DB='<target db>' "
    "make tp-mongo-documents-recon NS={ns} RUN_MODE=live RERUN=1 "
    "OUT=docs/tech-partnerships/recon/mongo_documents.live.recon.json "
    "# complete report; read-only default: omit RERUN=1 and OUT=... "
    "(writes an ignored build/tp-recon report)"
)

UNVERIFIED_FIXTURE = [
    "Writes against the shared MongoDB Atlas cluster: this run targeted the local mongo:7 fixture only (make tp-mongo-fixture-up), so Atlas wire-protocol writes, Atlas-side $jsonSchema validator DDL and Atlas index builds are unverified here. The parent session's single uncontended run against Atlas is the only live proof.",
    "Atlas-specific operational behaviour: M0 free-tier storage headroom for this collection set, Atlas index build time, and read/write performance under Atlas latency.",
    "Atlas alert configuration: the parent's capability preflight reports alert-webhook-config DENIED (HTTP 401 USER_UNAUTHORIZED); nothing in this unit depends on it and it was not exercised.",
    "Atlas access-list and credential handling: this unit never touched the Atlas project access list or Atlas credentials.",
]

UNVERIFIED_LIVE = [
    "Atlas alert configuration: alert-webhook-config is DENIED (HTTP 401 USER_UNAUTHORIZED) per the capability preflight; nothing in this unit depends on it.",
]

# Line formats of the baseline manifest checksums; recomputed here from the
# target documents so a drifted field can never pass unnoticed.
#   documents:          <doc id>|<declared version>|<word count>
#   document_versions:  <doc id>|<version number>
#   document_snapshots: <snapshot id>|<document id>


def epoch_ms(value: datetime) -> int:
    """Instant as milliseconds since the epoch — the BSON date resolution."""
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


def text(value) -> str:
    return "" if value is None else str(value)


# Field-level parity line formats. The manifest checksums cover only a few
# columns, so these fold every migrated field (including both timestamps, as
# UTC milliseconds) into a checksum computed independently on each side.
#   documents:          <id>|<title>|<content>|<content_type>|<owner_id>|<folder_id>|
#                       <is_deleted>|<is_template>|<word_count>|<declared_version>|
#                       <created_at ms>|<updated_at ms>
#   document_versions:  <id>|<document_id>|<version_number>|<title>|<content>|
#                       <created_by>|<created_at ms>
#   document_snapshots: <id>|<document_id>|<state_b64>|<label>|<created_by>|<created_at ms>


def document_parity_line(
    doc_id, title, content, content_type, owner_id, folder_id,
    is_deleted, is_template, word_count, declared_version, created_at, updated_at,
) -> str:
    return "|".join(
        [
            text(doc_id), text(title), text(content), text(content_type),
            text(owner_id), text(folder_id), str(int(bool(is_deleted))),
            str(int(bool(is_template))), str(int(word_count)), str(int(declared_version)),
            str(epoch_ms(created_at)), str(epoch_ms(updated_at)),
        ]
    )


def version_parity_line(
    version_id, doc_id, version_number, title, content, created_by, created_at
) -> str:
    return "|".join(
        [
            text(version_id), text(doc_id), str(int(version_number)), text(title),
            text(content), text(created_by), str(epoch_ms(created_at)),
        ]
    )


def snapshot_parity_line(
    snapshot_id, doc_id, state_b64, label, created_by, created_at
) -> str:
    return "|".join(
        [
            text(snapshot_id), text(doc_id), text(state_b64), text(label),
            text(created_by), str(epoch_ms(created_at)),
        ]
    )


def classify_version_sequence(
    declared: int,
    present: list[int],
) -> tuple[list[int] | None, dict[str, int] | None]:
    try:
        return missing_versions_for(declared, present), None
    except VersionSequenceOverBound as exc:
        return None, {
            "declared": exc.declared,
            "missing_count": exc.missing_count,
        }


def source_expectations(ns: str) -> dict:
    """Anomaly sets and field-level parity checksums re-derived from Postgres."""
    schema = source_schema(ns)
    conn = pg_connect(ns)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT d.id::text, d.version,
                       COALESCE(ARRAY_AGG(v.version_number ORDER BY v.version_number)
                                FILTER (WHERE v.version_number IS NOT NULL), '{{}}')
                  FROM {schema}.documents d
                  LEFT JOIN {schema}.document_versions v ON v.document_id = d.id
                 GROUP BY d.id, d.version
                """
            )
            gaps = {}
            version_sequence_over_bound = {}
            for doc_id, declared, present in cur.fetchall():
                missing, over_bound = classify_version_sequence(
                    declared, list(present)
                )
                if over_bound is not None:
                    version_sequence_over_bound[doc_id] = over_bound
                    continue
                if missing:
                    gaps[doc_id] = missing
            cur.execute(
                f"""
                SELECT s.id::text FROM {schema}.document_snapshots s
                 WHERE NOT EXISTS (
                     SELECT 1 FROM {schema}.documents d WHERE d.id = s.document_id)
                 ORDER BY 1
                """
            )
            orphans = [row[0] for row in cur.fetchall()]

        lc = legacy_common()
        doc_ck, ver_ck, snap_ck = lc.Checksum(), lc.Checksum(), lc.Checksum()
        sub_ms = 0

        def fold(sql: str, line, cursor_name: str) -> None:
            nonlocal sub_ms
            cursor = conn.cursor(name=cursor_name)
            cursor.itersize = 500
            cursor.execute(sql)
            for row in cursor:
                for value in row:
                    if isinstance(value, datetime) and value.microsecond % 1000:
                        sub_ms += 1
                line(row)
            cursor.close()

        fold(
            f"""
            SELECT id::text, title, content, content_type, owner_id::text,
                   folder_id::text, is_deleted, is_template, word_count, version,
                   created_at, updated_at
              FROM {schema}.documents
            """,
            lambda row: doc_ck.add(document_parity_line(*row)),
            "ow_tp_recon_src_documents",
        )
        fold(
            f"""
            SELECT id::text, document_id::text, version_number, title, content,
                   created_by::text, created_at
              FROM {schema}.document_versions
            """,
            lambda row: ver_ck.add(version_parity_line(*row)),
            "ow_tp_recon_src_versions",
        )
        fold(
            f"""
            SELECT id::text, document_id::text, state_b64, label, created_by::text,
                   created_at
              FROM {schema}.document_snapshots
            """,
            lambda row: snap_ck.add(snapshot_parity_line(*row)),
            "ow_tp_recon_src_snapshots",
        )
    finally:
        conn.close()
    return {
        "version_gaps": gaps,
        "version_sequence_over_bound": version_sequence_over_bound,
        "orphaned_snapshots": orphans,
        "documents_parity": doc_ck.hexdigest(),
        "versions_parity": ver_ck.hexdigest(),
        "snapshots_parity": snap_ck.hexdigest(),
        "sub_millisecond_timestamps": sub_ms,
    }


def anomaly_key(doc_id: str, missing: list[int]) -> str:
    return f"{doc_id}:missing={','.join(str(v) for v in missing)}"


def target_facts(ns: str) -> dict:
    """Recompute every target-side number by reading MongoDB back."""
    lc = legacy_common()
    client = mongo_client()
    try:
        db = client[target_db_name(ns)]
        doc_ck, ver_ck, snap_ck = lc.Checksum(), lc.Checksum(), lc.Checksum()
        doc_parity, ver_parity, snap_parity = lc.Checksum(), lc.Checksum(), lc.Checksum()
        documents = 0
        embedded_versions = 0
        gaps: dict[str, list[int]] = {}
        embedded_snapshot_fields = 0
        over_bound = 0
        doc_ids: set[str] = set()
        version_sequence_mismatches: list[dict] = []
        target_version_sequence_over_bound: dict[str, dict] = {}

        for doc in db[DOCUMENTS].find({"ns": ns}):
            documents += 1
            doc_parity.add(
                document_parity_line(
                    doc["_id"], doc["title"], doc["content"], doc["content_type"],
                    doc["owner_id"], doc.get("folder_id"), doc["is_deleted"],
                    doc["is_template"], doc["word_count"], doc["declared_version"],
                    doc["created_at"], doc["updated_at"],
                )
            )
            for version in doc.get("versions", []):
                ver_parity.add(
                    version_parity_line(
                        version["_id"], doc["_id"], version["version_number"],
                        version["title"], version["content"], version["created_by"],
                        version["created_at"],
                    )
                )
            doc_ids.add(doc["_id"])
            doc_ck.add(f"{doc['_id']}|{doc['declared_version']}|{doc['word_count']}")
            numbers = [v["version_number"] for v in doc.get("versions", [])]
            embedded_versions += len(numbers)
            if len(numbers) > VERSION_ARRAY_BOUND:
                over_bound += 1
            for number in numbers:
                ver_ck.add(f"{doc['_id']}|{number}")
            missing, sequence_over_bound = classify_version_sequence(
                doc["declared_version"], numbers
            )
            if sequence_over_bound is not None:
                target_version_sequence_over_bound[doc["_id"]] = sequence_over_bound
                missing = []
            if missing:
                gaps[doc["_id"]] = missing
            sequence = doc.get("version_sequence", {})
            stored_missing = sequence.get("missing", [])
            stored_present = sequence.get("present")
            if (
                set(stored_missing) != set(missing)
                or stored_present != len(numbers)
            ):
                version_sequence_mismatches.append(
                    {
                        "_id": doc["_id"],
                        "expected_missing": missing,
                        "actual_missing": sorted(stored_missing),
                        "expected_present": len(numbers),
                        "actual_present": stored_present,
                    }
                )
            if "snapshots" in doc:
                embedded_snapshot_fields += 1

        snapshots = 0
        orphans: list[str] = []
        attached_to_missing_parent: list[str] = []
        for snap in db[SNAPSHOTS].find({"ns": ns}):
            snapshots += 1
            snap_ck.add(f"{snap['_id']}|{snap['document_id']}")
            snap_parity.add(
                snapshot_parity_line(
                    snap["_id"], snap["document_id"], snap["state_b64"],
                    snap.get("label"), snap["created_by"], snap["created_at"],
                )
            )
            if snap.get("parent_missing"):
                orphans.append(snap["_id"])
            elif snap["document_id"] not in doc_ids:
                attached_to_missing_parent.append(snap["_id"])

        quarantined = db[QUARANTINE].count_documents({"ns": ns})
        version_sequence_over_bound_quarantine = sorted(
            record["source_id"]
            for record in db[QUARANTINE].find(
                {
                    "ns": ns,
                    "source_table": "documents",
                    "reason": "version_sequence_over_bound",
                },
                {"source_id": 1},
            )
        )
        validator_probe = probe_validator(db, ns)
        return {
            "documents": documents,
            "embedded_versions": embedded_versions,
            "snapshots": snapshots,
            "quarantined": quarantined,
            "documents_checksum": doc_ck.hexdigest(),
            "versions_checksum": ver_ck.hexdigest(),
            "snapshots_checksum": snap_ck.hexdigest(),
            "version_gaps": gaps,
            "version_sequence_over_bound": target_version_sequence_over_bound,
            "version_sequence_over_bound_quarantine": version_sequence_over_bound_quarantine,
            "version_sequence_mismatches": sorted(
                version_sequence_mismatches, key=lambda mismatch: mismatch["_id"]
            ),
            "orphaned_snapshots": sorted(orphans),
            "snapshots_with_unresolvable_parent_not_flagged": sorted(attached_to_missing_parent),
            "documents_with_embedded_snapshots": embedded_snapshot_fields,
            "documents_over_version_bound": over_bound,
            "documents_parity": doc_parity.hexdigest(),
            "versions_parity": ver_parity.hexdigest(),
            "snapshots_parity": snap_parity.hexdigest(),
            "validators": validator_probe,
        }
    finally:
        client.close()


def probe_validator(db, ns: str) -> dict:
    """Prove each collection's $jsonSchema validator rejects a bad document.

    Probes are only written when a validator is present. Without one, the
    missing validator fails the check without risking a malformed write.
    """
    from pymongo.errors import DuplicateKeyError, WriteError

    result = {}
    probes = {
        DOCUMENTS: {
            "_id": "ow_tp_validator_probe",
            "ns": ns,
            "declared_version": "not-an-int",
        },
        SNAPSHOTS: {
            "_id": "ow_tp_validator_probe",
            "ns": ns,
            "document_id": 42,
        },
    }
    for name, bad in probes.items():
        scope = {"ns": ns}
        db[name].delete_one({"_id": bad["_id"], "ns": ns})
        before = db[name].count_documents(scope)
        info = next(iter(db.list_collections(filter={"name": name})), {})
        has_validator = "validator" in info.get("options", {})
        rejected = False
        if has_validator:
            try:
                db[name].insert_one(bad)
            except DuplicateKeyError:
                rejected = False
            except WriteError:
                rejected = True
            else:
                db[name].delete_one({"_id": bad["_id"], "ns": ns})
        after = db[name].count_documents(scope)
        probe_absent = (
            db[name].count_documents({"_id": bad["_id"], "ns": ns}) == 0
        )
        result[name] = {
            "validator_present": has_validator,
            "violating_insert_rejected": rejected,
            "count_unchanged": before == after,
            "probe_absent": probe_absent,
        }
    return result


def fingerprint(facts: dict) -> dict:
    return {
        "documents": facts["documents"],
        "embedded_versions": facts["embedded_versions"],
        "snapshots": facts["snapshots"],
        "quarantined": facts["quarantined"],
        "documents_checksum": facts["documents_checksum"],
        "versions_checksum": facts["versions_checksum"],
        "snapshots_checksum": facts["snapshots_checksum"],
        "version_gaps": {k: v for k, v in sorted(facts["version_gaps"].items())},
        "version_sequence_over_bound": facts["version_sequence_over_bound"],
        "version_sequence_over_bound_quarantine": facts[
            "version_sequence_over_bound_quarantine"
        ],
        "version_sequence_mismatches": facts["version_sequence_mismatches"],
        "orphaned_snapshots": facts["orphaned_snapshots"],
        "documents_parity": facts["documents_parity"],
        "versions_parity": facts["versions_parity"],
        "snapshots_parity": facts["snapshots_parity"],
    }


def rerun_migration(ns: str) -> subprocess.CompletedProcess:
    cmd = [
        "uv", "run", "--no-project",
        "--with", "pymongo==4.10.1",
        "--with", "psycopg2-binary==2.9.10",
        "python3", "scripts/tp_mongo/migrate_documents.py", "--ns", ns,
    ]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)


def build_checks(ns: str, mf: dict, expected: dict, facts: dict) -> list[dict]:
    schema = source_schema(ns)
    base = mf["targets"]
    docs_want = base[f"postgres.{schema}.documents"]
    vers_want = base[f"postgres.{schema}.document_versions"]
    snaps_want = base[f"postgres.{schema}.document_snapshots"]
    manifest_src = f"testdata/legacy/manifests/{ns}.json"
    pg_src = f"postgres {schema} (legacy source estate)"
    target_src = f"mongodb {target_db_name(ns)} (recomputed after write)"

    def check(cid, expected_value, actual_value, source):
        return {
            "id": cid,
            "expected": expected_value,
            "actual": actual_value,
            "source_of_truth": source,
            "result": "pass" if expected_value == actual_value else "fail",
        }

    expected_gap_keys = sorted(anomaly_key(d, m) for d, m in expected["version_gaps"].items())
    actual_gap_keys = sorted(anomaly_key(d, m) for d, m in facts["version_gaps"].items())
    expected_sequence_over_bound = sorted(expected["version_sequence_over_bound"])
    actual_sequence_over_bound = facts["version_sequence_over_bound_quarantine"]

    contract_path = ROOT / f"docs/tech-partnerships/contracts/{UNIT}.json"
    contract = json.loads(contract_path.read_text())
    contract_scope = f"ow_tp_{ns}."
    contract_applies = any(
        str(target).startswith(contract_scope)
        for target in contract.get("target_objects", [])
    )
    manifest_md5s = {
        docs_want["checksum"],
        vers_want["checksum"],
    }
    contract_check = {
        "id": "documents.baseline_matches_contract",
        "expected": {"contract_md5s": []},
        "actual": {"manifest_md5s": sorted(manifest_md5s)},
        "source_of_truth": (
            f"contract is demo-scoped, reconciling ns={ns}; "
            "check skipped because the contract does not describe this namespace"
        ),
        "result": "skipped",
    }
    if contract_applies:
        description = next(
            check["description"]
            for check in contract.get("acceptance_checks", [])
            if check.get("id") == "documents.checksums"
        )
        contract_md5s = set(re.findall(r"\b[0-9a-fA-F]{32}\b", description))
        contract_check["expected"] = {"contract_md5s": sorted(contract_md5s)}
        contract_check["actual"] = {"manifest_md5s": sorted(manifest_md5s)}
        contract_check["result"] = (
            "pass" if contract_md5s == manifest_md5s else "fail"
        )
        contract_check["source_of_truth"] = (
            f"docs/tech-partnerships/contracts/{UNIT}.json "
            "acceptance_checks.documents.checksums vs "
            f"{manifest_src} documents+versions checksums"
        )

    checks = [
        check("documents.count", docs_want["rows"], facts["documents"], f"{manifest_src} vs {target_src}"),
        check("documents.version_count", vers_want["rows"], facts["embedded_versions"],
              f"{manifest_src} vs {target_src}"),
        check("documents.checksums",
              {"documents": docs_want["checksum"], "versions": vers_want["checksum"]},
              {"documents": facts["documents_checksum"], "versions": facts["versions_checksum"]},
              f"{manifest_src} vs {target_src}"),
        check("documents.snapshot_count", snaps_want["rows"], facts["snapshots"],
              f"{manifest_src} vs {target_src}"),
        check("documents.snapshot_checksum", snaps_want["checksum"], facts["snapshots_checksum"],
              f"{manifest_src} vs {target_src}"),
        check("documents.snapshots_not_embedded", 0, facts["documents_with_embedded_snapshots"],
              target_src),
        check("documents.field_parity",
              {"documents": expected["documents_parity"], "versions": expected["versions_parity"],
               "snapshots": expected["snapshots_parity"]},
              {"documents": facts["documents_parity"], "versions": facts["versions_parity"],
               "snapshots": facts["snapshots_parity"]},
              f"{pg_src} vs {target_src} (every migrated field incl. UTC timestamps)"),
        check("documents.timestamps_representable_in_bson", 0,
              expected["sub_millisecond_timestamps"], pg_src),
        check("documents.version_gaps_reported", expected_gap_keys, actual_gap_keys,
              f"{pg_src} vs {target_src}"),
        check("documents.version_gaps_count",
              anomaly_count(mf, "version_gaps", f"postgres.{schema}.document_versions"),
              len(facts["version_gaps"]), f"{manifest_src} vs {target_src}"),
        check(
            "documents.version_sequence_over_bound_quarantined",
            expected_sequence_over_bound,
            actual_sequence_over_bound,
            f"{pg_src} expected quarantine vs {target_src}",
        ),
        check("documents.version_sequence_annotations", [], facts["version_sequence_mismatches"],
              f"{target_src} persisted version_sequence vs recomputed embedded versions"),
        check("documents.orphaned_snapshots_reported", expected["orphaned_snapshots"],
              facts["orphaned_snapshots"], f"{pg_src} vs {target_src}"),
        check("documents.orphaned_snapshots_count",
              anomaly_count(mf, "orphaned_snapshots", f"postgres.{schema}.document_snapshots"),
              len(facts["orphaned_snapshots"]), f"{manifest_src} vs {target_src}"),
        check("documents.unflagged_missing_parents", [],
              facts["snapshots_with_unresolvable_parent_not_flagged"], target_src),
        {
            "id": "documents.quarantine_empty",
            "expected": 0 if not expected_sequence_over_bound else "expected source quarantines",
            "actual": facts["quarantined"]
            if not expected_sequence_over_bound
            else {
                "count": facts["quarantined"],
                "version_sequence_over_bound": actual_sequence_over_bound,
            },
            "source_of_truth": target_src,
            "result": (
                "pass"
                if (
                    facts["quarantined"] == 0
                    if not expected_sequence_over_bound
                    else actual_sequence_over_bound == expected_sequence_over_bound
                )
                else "fail"
            ),
        },
        check("documents.no_truncated_version_arrays", 0, facts["documents_over_version_bound"],
              target_src),
        check("documents.validators_reject_invalid",
              {name: {"validator_present": True, "violating_insert_rejected": True,
                      "count_unchanged": True, "probe_absent": True}
              for name in facts["validators"]},
              facts["validators"], target_src),
        contract_check,
    ]
    return checks


def anomaly_count(mf: dict, kind: str, target: str) -> int | None:
    for anomaly in mf.get("planted_anomalies", []):
        if anomaly.get("kind") == kind and anomaly.get("target") == target:
            return int(anomaly["count"])
    return None


def validate_output_path(out: str | None, rerun: bool) -> None:
    if not rerun and out and out.endswith(".recon.json"):
        raise ValueError(
            "--out must not end with .recon.json without --rerun-migration; "
            "use a partial artifact path or pass --rerun-migration"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--run-mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument(
        "--rerun-migration",
        action="store_true",
        help="run the migration again and compare target state",
    )
    parser.add_argument("--out", help="write the recon report to this path")
    args = parser.parse_args()
    try:
        validate_output_path(args.out, args.rerun_migration)
    except ValueError as exc:
        parser.error(str(exc))
    validate_namespace(args.ns)

    mf = manifest(args.ns)
    expected = source_expectations(args.ns)
    facts = target_facts(args.ns)
    before = fingerprint(facts)
    if args.rerun_migration:
        proc = rerun_migration(args.ns)
        if proc.returncode != 0:
            idempotency = {
                "performed": True,
                "result": "fail",
                "evidence": f"rerun exited {proc.returncode}: {proc.stderr.strip()[-500:]}",
            }
        else:
            facts = target_facts(args.ns)
            after = fingerprint(facts)
            idempotency = {
                "performed": True,
                "result": "pass" if after == before else "fail",
                "evidence": (
                    "migration re-run end to end; target re-read before and after: "
                    f"documents={after['documents']} embedded_versions={after['embedded_versions']} "
                    f"snapshots={after['snapshots']} documents_checksum={after['documents_checksum']} "
                    f"versions_checksum={after['versions_checksum']} "
                    f"snapshots_checksum={after['snapshots_checksum']} "
                    f"anomaly sets unchanged={before['version_gaps'] == after['version_gaps'] and before['orphaned_snapshots'] == after['orphaned_snapshots']}"
                ),
            }
    else:
        idempotency = {
            "performed": False,
            "result": "fail",
            "evidence": (
                "migration rerun not performed; --rerun-migration is required "
                "for a complete idempotency report and repeats the migration write"
            ),
        }

    checks = build_checks(args.ns, mf, expected, facts)
    checks.append(
        {
            "id": "documents.idempotent",
            "expected": "pass",
            "actual": idempotency["result"],
            "source_of_truth": (
                "migration rerun is opt-in; --rerun-migration is required to "
                f"compare mongodb {target_db_name(args.ns)} before and after a write"
            ),
            "result": "pass" if idempotency["result"] == "pass" else "fail",
        }
    )
    expected_set = sorted(
        [anomaly_key(d, m) for d, m in expected["version_gaps"].items()]
        + [f"orphaned_snapshot={sid}" for sid in expected["orphaned_snapshots"]]
    )
    actual_set = sorted(
        [anomaly_key(d, m) for d, m in facts["version_gaps"].items()]
        + [f"orphaned_snapshot={sid}" for sid in facts["orphaned_snapshots"]]
    )
    report = {
        "kind": "recon-report" if args.rerun_migration else "recon-report-partial",
        "unit": UNIT,
        "namespace": args.ns,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_mode": args.run_mode,
        "target": {
            "database": target_db_name(args.ns),
            "uri": redacted_uri(mongo_uri()),
            "collections": [DOCUMENTS, SNAPSHOTS, QUARANTINE],
        },
        "checks": checks,
        "values_recomputed_from_target": True,
        "planted_anomaly_detections": {
            "expected_set": expected_set,
            "actual_set": actual_set,
            "missing": sorted(set(expected_set) - set(actual_set)),
            "unexpected": sorted(set(actual_set) - set(expected_set)),
        },
        "unverified_paths": UNVERIFIED_FIXTURE if args.run_mode == "fixture" else UNVERIFIED_LIVE,
        "recompute_command": RECOMPUTE_COMMAND.format(ns=args.ns),
    }
    if not args.rerun_migration:
        report["schema_note"] = (
            "This partial artifact is intentionally not schema-conforming because "
            "the schema mandates a performed rerun; --rerun-migration produces a "
            "conforming report."
        )
    report["idempotency_rerun"] = idempotency

    failures = [c["id"] for c in checks if c["result"] == "fail"]
    report["recon_result"] = (
        "pass"
        if not failures
        and not report["planted_anomaly_detections"]["missing"]
        and not report["planted_anomaly_detections"]["unexpected"]
        else "fail"
    )

    text = json.dumps(report, indent=2, sort_keys=False) + "\n"
    if args.out:
        Path(args.out).write_text(text)
        print(f"[recon] wrote {args.out}")
    else:
        print(text)
    for c in checks:
        print(f"[recon] {c['result']:>4}  {c['id']}")
    print(f"[recon] result: {report['recon_result']}")
    return 0 if report["recon_result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
