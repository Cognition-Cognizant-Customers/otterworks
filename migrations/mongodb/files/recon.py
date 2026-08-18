# /// script
# requires-python = ">=3.11"
# dependencies = ["pymongo"]
# ///
"""mongo_files unit reconciliation: recompute counts/checksums from MongoDB.

Everything in the report is recomputed from the target collections at run
time — never from migration-time memory. The checksum folds the seeder's
exact line format (`{id}|{size_bytes}|{s3_key}`) through the same
order-independent md5 sum as testdata/legacy/legacy_common.py:checksum_lines,
so it compares directly against the manifest's dynamodb.file-metadata entry.

Two modes:
  snapshot  — recompute {count, checksum, quarantined} and write them to a
              JSON file (taken after the first migration run).
  report    — recompute again (after the rerun), compare against the manifest
              and against the snapshot (idempotency evidence), and emit a
              schema-valid *.recon.json report.

Usage:
    uv run migrations/mongodb/files/recon.py --ns <ns> --mode snapshot --out snap.json
    uv run migrations/mongodb/files/recon.py --ns <ns> --mode report \
        --prior snap.json --run-mode fixture --out <unit>-<ns>.recon.json
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient

REPO = Path(__file__).resolve().parents[3]
UNIT = "mongo_files"
MANIFEST_TARGET = "dynamodb.file-metadata"
COVERAGE_GAPS = {"missing_hours"}  # declared in the contract, no unit ingests s3 events

UNVERIFIED_PATHS = [
    (
        "per-key S3 HeadObject existence probe: the estate seeds file metadata only "
        "(no file binaries in s3://otterworks-data-lake), so orphaned_metadata is "
        "detected by the estate's orphan marker (a /missing/ path segment), the same "
        "detector testdata/legacy/validate.py uses"
    ),
    (
        "live Atlas run: this phase is fixture-only per the isolation rules; the "
        "parent recomputes recon in the live validation window"
    ),
    (
        "DynamoDB Binary/NULL/unknown-attribute handling: policy implemented per the "
        "contract but the seeded estate plants none, so those branches are untested"
    ),
    (
        "s3.data-lake events prefix (missing_hours anomaly): contractual coverage_gap "
        "— no unit in this track ingests the events prefix"
    ),
]


class Checksum:
    """Order-independent md5 sum (mirrors testdata/legacy/legacy_common.py)."""

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


def recompute(mongo_uri: str, ns: str) -> dict:
    client = MongoClient(mongo_uri)
    files = client[f"ow_tp_mongodb_{ns}"]["files"]
    quarantine = client[f"ow_tp_mongodb_{ns}_quarantine"]["files_quarantine"]

    ck = Checksum()
    flagged = 0
    for doc in files.find(
        {"tenant": ns}, {"_id": 1, "size_bytes": 1, "s3_key": 1, "flags": 1}
    ):
        ck.add(f"{doc['_id']}|{doc['size_bytes']}|{doc['s3_key']}")
        if "orphaned_metadata" in doc.get("flags", []):
            flagged += 1

    quarantined_ids = sorted(
        q["_id"]
        for q in quarantine.find({"tenant": ns, "reason": "orphaned_metadata"}, {"_id": 1})
    )
    return {
        "count": ck.count,
        "checksum": ck.hexdigest(),
        "flagged": flagged,
        "quarantined": len(quarantined_ids),
        "quarantined_ids": quarantined_ids,
    }


def build_report(ns: str, run_mode: str, current: dict, prior: dict) -> dict:
    manifest = json.loads(
        (REPO / "testdata/legacy/manifests" / f"{ns}.json").read_text()
    )
    want = manifest["targets"][MANIFEST_TARGET]
    anomalies = [
        a for a in manifest["planted_anomalies"]
        if a["target"] == MANIFEST_TARGET or a["kind"] in COVERAGE_GAPS
    ]

    checks = [
        {
            "id": "files-count",
            "expected": want["items"],
            "actual": current["count"],
            "source_of_truth": f"manifest {MANIFEST_TARGET}.items vs count "
                               f"recomputed from ow_tp_mongodb_{ns}.files",
            "result": "pass" if current["count"] == want["items"] else "fail",
        },
        {
            "id": "files-checksum",
            "expected": want["checksum"],
            "actual": current["checksum"],
            "source_of_truth": f"manifest {MANIFEST_TARGET}.checksum vs "
                               "order-independent md5 over id|size_bytes|s3_key "
                               f"recomputed from ow_tp_mongodb_{ns}.files",
            "result": "pass" if current["checksum"] == want["checksum"] else "fail",
        },
        {
            "id": "orphaned-metadata-reported",
            "expected": next(
                a["count"] for a in anomalies if a["kind"] == "orphaned_metadata"
            ),
            "actual": {
                "flagged_in_files": current["flagged"],
                "quarantined": current["quarantined"],
            },
            "source_of_truth": "manifest planted_anomalies orphaned_metadata vs "
                               f"flags recomputed from ow_tp_mongodb_{ns}.files and "
                               f"ow_tp_mongodb_{ns}_quarantine.files_quarantine",
            "result": "pass"
            if current["flagged"]
            == current["quarantined"]
            == next(a["count"] for a in anomalies if a["kind"] == "orphaned_metadata")
            else "fail",
        },
    ]

    expected_set = sorted(
        [a["kind"], a["target"], a["count"],
         "coverage_gap" if a["kind"] in COVERAGE_GAPS else "must-detect"]
        for a in anomalies
    )
    actual_set = sorted(
        [
            ["orphaned_metadata", MANIFEST_TARGET, current["quarantined"], "must-detect"],
            *[
                [a["kind"], a["target"], a["count"], "coverage_gap"]
                for a in anomalies
                if a["kind"] in COVERAGE_GAPS
            ],
        ]
    )
    expected_keys = {tuple(a) for a in expected_set}
    actual_keys = {tuple(a) for a in actual_set}

    idempotent = (
        prior["count"] == current["count"]
        and prior["checksum"] == current["checksum"]
        and prior["quarantined"] == current["quarantined"]
    )
    return {
        "kind": "recon-report",
        "unit": UNIT,
        "namespace": ns,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_mode": run_mode,
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": {
            "performed": True,
            "result": "pass" if idempotent else "fail",
            "evidence": (
                f"run 1 vs rerun recomputed from target: count "
                f"{prior['count']}=={current['count']}, checksum "
                f"{prior['checksum']}=={current['checksum']}, quarantined "
                f"{prior['quarantined']}=={current['quarantined']}"
                if idempotent
                else f"rerun diverged: {prior} != "
                     f"{ {k: current[k] for k in ('count', 'checksum', 'quarantined')} }"
            ),
        },
        "planted_anomaly_detections": {
            "expected_set": expected_set,
            "actual_set": actual_set,
            "missing": sorted(list(k) for k in expected_keys - actual_keys),
            "unexpected": sorted(list(k) for k in actual_keys - expected_keys),
        },
        "coverage_gaps": [
            {
                "kind": a["kind"],
                "target": a["target"],
                "count": a["count"],
                "reason": "S3 hourly event objects are not part of the MongoDB "
                          "document model; no unit in this track ingests the events "
                          "prefix (declared in the mongo_files contract)",
            }
            for a in anomalies
            if a["kind"] in COVERAGE_GAPS
        ],
        "quarantine_enumeration": {
            "reason": "orphaned_metadata",
            "collection": f"ow_tp_mongodb_{ns}_quarantine.files_quarantine",
            "ids": current["quarantined_ids"],
        },
        "unverified_paths": UNVERIFIED_PATHS,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument(
        "--mongo-uri",
        default=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    )
    parser.add_argument("--mode", choices=["snapshot", "report"], required=True)
    parser.add_argument("--prior", help="snapshot JSON from the first run (report mode)")
    parser.add_argument("--run-mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    current = recompute(args.mongo_uri, args.ns)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.mode == "snapshot":
        out.write_text(json.dumps(
            {k: current[k] for k in ("count", "checksum", "quarantined")}, indent=2
        ) + "\n")
        print(f"[recon-files] snapshot: {out} "
              f"(count={current['count']} checksum={current['checksum']})")
        return 0

    if not args.prior:
        print("--prior is required in report mode", file=sys.stderr)
        return 2
    prior = json.loads(Path(args.prior).read_text())
    report = build_report(args.ns, args.run_mode, current, prior)
    out.write_text(json.dumps(report, indent=2) + "\n")

    failed = [c["id"] for c in report["checks"] if c["result"] == "fail"]
    detections = report["planted_anomaly_detections"]
    print(f"[recon-files] report: {out}")
    print(f"[recon-files] checks failed: {failed or 'none'}; idempotency: "
          f"{report['idempotency_rerun']['result']}; anomalies missing/unexpected: "
          f"{len(detections['missing'])}/{len(detections['unexpected'])}")
    if failed or report["idempotency_rerun"]["result"] == "fail" \
            or detections["missing"] or detections["unexpected"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
