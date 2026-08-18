# /// script
# requires-python = ">=3.11"
# dependencies = ["pymongo"]
# ///
"""Reconcile the migrated MongoDB document estate against the golden manifest.

Unit: mongo_documents. Everything is RECOMPUTED from the target MongoDB —
never from migration-time memory: counts, all three order-independent md5
checksums (same line formats as testdata/legacy/seed.py), version-gap
enumeration, and the orphaned-snapshot quarantine set. Planted anomalies are
compared as sets (expected/actual/missing/unexpected).

Checksum line formats (must match the seeders byte-for-byte):
  - documents:          "{doc_id}|{version}|{word_count}"
  - document_versions:  "{document_id}|{version_number}"
  - document_snapshots: "{snapshot_id}|{document_id}"  (embedded + quarantined)

Emits a machine-readable report valid against
docs/tech-partnerships/contracts/schema/recon-report.schema.json. The report's
generated_at is taken from the manifest anchor so the artifact carries no
wall-clock timestamp.

Usage:
    uv run migrations/mongodb/documents/recon.py --ns <ns> \
        [--run-mode fixture|live] [--out <path>] \
        [--idempotency-rerun-performed --idempotency-evidence "..."]

Without --idempotency-rerun-performed the run is check-only: results are
printed but no report file is written, because the report schema requires
idempotency_rerun.performed to be true (proven by an actual migrate rerun).
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[3]


class Checksum:
    """Order-independent md5-sum checksum, identical to testdata/legacy."""

    _MOD = 1 << 128

    def __init__(self) -> None:
        self._total = 0
        self.count = 0

    def add(self, line: str) -> None:
        digest = hashlib.md5(line.encode()).digest()
        self._total = (self._total + int.from_bytes(digest, "big")) % self._MOD
        self.count += 1

    def hexdigest(self) -> str:
        return f"{self._total:032x}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument(
        "--mongodb-uri",
        default=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    )
    parser.add_argument(
        "--db-prefix", default=os.getenv("TP_MONGODB_DB_PREFIX", "ow_tp_mongodb_")
    )
    parser.add_argument("--run-mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--out")
    parser.add_argument("--idempotency-rerun-performed", action="store_true")
    parser.add_argument("--idempotency-evidence", default="")
    args = parser.parse_args()

    ns = args.ns
    schema = f"otterworks_{ns}"
    manifest = json.loads((ROOT / "testdata/legacy/manifests" / f"{ns}.json").read_text())
    m_targets = manifest["targets"]
    m_docs = m_targets[f"postgres.{schema}.documents"]
    m_vers = m_targets[f"postgres.{schema}.document_versions"]
    m_snaps = m_targets[f"postgres.{schema}.document_snapshots"]

    client = MongoClient(args.mongodb_uri)
    documents = client[f"{args.db_prefix}{ns}"]["documents"]
    quarantine = client[f"{args.db_prefix}{ns}_quarantine"]["documents_quarantine"]

    doc_ck, ver_ck, snap_ck = Checksum(), Checksum(), Checksum()
    doc_count = ver_count = snap_count = 0
    gap_docs: list[dict] = []

    for doc in documents.find({"ns": ns}, sort=[("_id", 1)]):
        doc_count += 1
        doc_ck.add(f"{doc['_id']}|{doc['version']}|{doc['word_count']}")
        present = sorted(v["version_number"] for v in doc.get("versions", []))
        for v in present:
            ver_ck.add(f"{doc['_id']}|{v}")
            ver_count += 1
        missing = sorted(set(range(1, doc["version"] + 1)) - set(present))
        if missing:
            gap_docs.append({"document_id": doc["_id"], "missing_versions": missing})
        for snap in doc.get("snapshots", []):
            snap_ck.add(f"{snap['id']}|{doc['_id']}")
            snap_count += 1

    orphaned: list[dict] = []
    for q in quarantine.find({"ns": ns, "reason": "orphaned_snapshot"}, sort=[("_id", 1)]):
        snap_ck.add(f"{q['_id']}|{q['document_id']}")
        snap_count += 1
        orphaned.append({"snapshot_id": q["_id"], "document_id": q["document_id"]})

    expected_set = sorted(
        f"{a['kind']}:{a['count']}"
        for a in manifest["planted_anomalies"]
        if a["target"].startswith(f"postgres.{schema}.")
    )
    actual_set = sorted(
        ([f"version_gaps:{len(gap_docs)}"] if gap_docs else [])
        + ([f"orphaned_snapshots:{len(orphaned)}"] if orphaned else [])
    )
    missing_anoms = sorted(set(expected_set) - set(actual_set))
    unexpected_anoms = sorted(set(actual_set) - set(expected_set))

    def check(check_id, expected, actual, source_of_truth):
        return {
            "id": check_id,
            "expected": expected,
            "actual": actual,
            "source_of_truth": source_of_truth,
            "result": "pass" if expected == actual else "fail",
        }

    manifest_src = f"testdata/legacy/manifests/{ns}.json"
    target_src = f"recomputed from MongoDB ({args.run_mode})"
    checks = [
        check("documents-count", m_docs["rows"], doc_count, f"{manifest_src} vs {target_src}"),
        check("documents-checksum", m_docs["checksum"], doc_ck.hexdigest(), f"{manifest_src} vs {target_src}"),
        check("versions-embedded-count", m_vers["rows"], ver_count, f"{manifest_src} vs {target_src}"),
        check("versions-checksum", m_vers["checksum"], ver_ck.hexdigest(), f"{manifest_src} vs {target_src}"),
        check("snapshots-count", m_snaps["rows"], snap_count, f"{manifest_src} vs {target_src}"),
        check("snapshots-checksum", m_snaps["checksum"], snap_ck.hexdigest(), f"{manifest_src} vs {target_src}"),
        check("version-gaps-reported", next((e for e in expected_set if e.startswith("version_gaps")), None),
              f"version_gaps:{len(gap_docs)}", f"{manifest_src} planted_anomalies vs {target_src}"),
        check("orphaned-snapshots-reported", next((e for e in expected_set if e.startswith("orphaned_snapshots")), None),
              f"orphaned_snapshots:{len(orphaned)}", f"{manifest_src} planted_anomalies vs {target_src}"),
    ]

    report = {
        "kind": "recon-report",
        "unit": "mongo_documents",
        "namespace": ns,
        "generated_at": manifest["generated_at"],
        "run_mode": args.run_mode,
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": {
            "performed": True,
            "result": "pass" if all(c["result"] == "pass" for c in checks) else "fail",
            "evidence": args.idempotency_evidence,
        },
        "planted_anomaly_detections": {
            "expected_set": expected_set,
            "actual_set": actual_set,
            "missing": missing_anoms,
            "unexpected": unexpected_anoms,
        },
        "anomaly_enumeration": {
            "version_gaps": gap_docs,
            "orphaned_snapshots": orphaned,
        },
        "unverified_paths": [
            "live Atlas run (run_mode=live) — parent-owned validation window",
            "invalid-UTF-8 quarantine path — Postgres UTF-8 source cannot produce it; not exercised",
            "SCALE=full volumes — only SCALE=demo exercised",
        ],
    }

    failed = [c["id"] for c in checks if c["result"] != "pass"]
    anomalies_ok = not missing_anoms and not unexpected_anoms
    if args.idempotency_rerun_performed:
        out = Path(args.out) if args.out else (
            ROOT / "docs/tech-partnerships/recon" / f"mongo_documents.{ns}.{args.run_mode}.recon.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"[mongo-documents-recon] report: {out}")
    else:
        print("[mongo-documents-recon] check-only run (no report written); "
              "rerun the migration and pass --idempotency-rerun-performed to emit the report")
    print(f"[mongo-documents-recon] checks: {len(checks) - len(failed)}/{len(checks)} pass; "
          f"anomaly sets {'match' if anomalies_ok else 'MISMATCH'}")
    if failed:
        print(f"[mongo-documents-recon] FAILED checks: {failed}")
    return 0 if not failed and anomalies_ok else 1


if __name__ == "__main__":
    sys.exit(main())
