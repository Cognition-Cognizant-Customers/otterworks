# /// script
# requires-python = ">=3.11"
# dependencies = ["pymongo"]
# ///
"""mongo_files recon: recompute counts/checksums/anomaly sets FROM MongoDB.

Contract: docs/tech-partnerships/contracts/mongo_files.json

Every "actual" value in the emitted report is recomputed from the target
MongoDB collections at generation time — never from migration-time memory.
The expected side comes from the immutable golden baseline manifest
(testdata/legacy/manifests/<ns>.json).

Checks (ids match the contract's acceptance_checks):
- files-count: ow_tp_mongodb_<ns>.files document count for the tenant equals
  the manifest dynamodb.file-metadata item count.
- files-checksum: order-independent md5 over "id|size_bytes|s3_key" lines
  (same line format the seeder and testdata/legacy/validate.py use),
  recomputed from the target collection, equals the manifest checksum.
- orphaned-metadata-reported: quarantine records with reason
  orphaned_s3_key match the manifest orphaned_metadata anomaly count, and
  the report enumerates the quarantined source ids.

Idempotency: run migrate -> recon (baseline), migrate again -> recon with
--compare <baseline>; the second report embeds the rerun result and fails if
any recomputed value differs from the baseline.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient

UNIT = "mongo_files"
NS_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
MANIFEST_TARGET = "dynamodb.file-metadata"

COMPARED_CHECK_IDS = ("files-count", "files-checksum", "orphaned-metadata-reported")


class Checksum:
    """Order-independent md5 sum (same construction as testdata/legacy)."""

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
    parser.add_argument("--mongodb-uri", required=True)
    parser.add_argument("--manifest", default=None,
                        help="golden baseline manifest "
                             "(default testdata/legacy/manifests/<ns>.json)")
    parser.add_argument("--run-mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--out", required=True)
    parser.add_argument("--compare", default=None,
                        help="previous recon report from before an identical "
                             "rerun; embeds the idempotency evidence")
    args = parser.parse_args()

    if not NS_PATTERN.fullmatch(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2
    ns = args.ns

    repo_root = Path(__file__).resolve().parents[2]
    manifest_path = Path(args.manifest) if args.manifest else (
        repo_root / "testdata" / "legacy" / "manifests" / f"{ns}.json")
    manifest = json.loads(manifest_path.read_text())
    want = manifest["targets"][MANIFEST_TARGET]
    orphan_want = next(
        a["count"] for a in manifest["planted_anomalies"]
        if a["kind"] == "orphaned_metadata" and a["target"] == MANIFEST_TARGET)

    client = MongoClient(args.mongodb_uri)
    files = client[f"ow_tp_mongodb_{ns}"]["files"]
    quarantine = client[f"ow_tp_mongodb_{ns}_quarantine"]["files_quarantine"]

    # recomputed from the target: count + checksum over the seeder line format
    ck = Checksum()
    for doc in files.find({"tenant": ns}, {"size_bytes": 1, "s3_key": 1}):
        ck.add(f"{doc['_id']}|{doc['size_bytes']}|{doc['s3_key']}")

    # recomputed from the target: quarantined orphan enumeration
    orphan_ids = sorted(
        q["source_id"]
        for q in quarantine.find({"tenant": ns, "reason": "orphaned_s3_key"},
                                 {"source_id": 1}))
    other_quarantine = sorted(
        q["reason"] for q in quarantine.find(
            {"tenant": ns, "reason": {"$ne": "orphaned_s3_key"}}, {"reason": 1}))
    client.close()

    checks = [
        {
            "id": "files-count",
            "expected": want["items"],
            "actual": ck.count,
            "result": "pass" if ck.count == want["items"] else "fail",
            "source_of_truth": f"manifest {MANIFEST_TARGET}.items vs "
                               f"count recomputed from ow_tp_mongodb_{ns}.files",
        },
        {
            "id": "files-checksum",
            "expected": want["checksum"],
            "actual": ck.hexdigest(),
            "result": "pass" if ck.hexdigest() == want["checksum"] else "fail",
            "source_of_truth": f"manifest {MANIFEST_TARGET}.checksum vs "
                               f"order-independent md5 of 'id|size_bytes|s3_key' "
                               f"recomputed from ow_tp_mongodb_{ns}.files",
        },
        {
            "id": "orphaned-metadata-reported",
            "expected": orphan_want,
            "actual": len(orphan_ids),
            "result": "pass" if len(orphan_ids) == orphan_want else "fail",
            "source_of_truth": f"manifest orphaned_metadata count vs quarantine "
                               f"records recomputed from ow_tp_mongodb_{ns}_quarantine"
                               f".files_quarantine (reason=orphaned_s3_key)",
        },
    ]

    detected = []
    if orphan_ids:
        detected.append("orphaned_metadata")
    detected.extend(sorted(set(other_quarantine)))

    idempotency = {"performed": False, "result": "fail",
                   "evidence": "baseline run: no --compare supplied; rerun "
                               "comparison pending (this intermediate report "
                               "is not schema-valid and must not be committed)"}
    if args.compare:
        baseline = json.loads(Path(args.compare).read_text())
        base_actuals = {c["id"]: c["actual"] for c in baseline["checks"]}
        drift = {
            c["id"]: {"baseline": base_actuals.get(c["id"]), "rerun": c["actual"]}
            for c in checks if base_actuals.get(c["id"]) != c["actual"]
        }
        base_orphans = baseline.get("orphaned_metadata_enumeration")
        if base_orphans != orphan_ids:
            drift["orphaned_metadata_enumeration"] = {
                "baseline_len": len(base_orphans or []), "rerun_len": len(orphan_ids)}
        idempotency = {
            "performed": True,
            "result": "pass" if not drift else "fail",
            "evidence": ("identical values recomputed from the target after a "
                         "full rerun of migrate_files.py: "
                         + ", ".join(f"{c['id']}={c['actual']}" for c in checks))
                        if not drift else f"drift after rerun: {drift}",
        }

    report = {
        "kind": "recon-report",
        "unit": UNIT,
        "namespace": ns,
        "generated_at": datetime.now(timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_mode": args.run_mode,
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": idempotency,
        "planted_anomaly_detections": {
            "expected_set": ["orphaned_metadata"],
            "actual_set": detected,
            "missing": sorted({"orphaned_metadata"} - set(detected)),
            "unexpected": sorted(set(detected) - {"orphaned_metadata"}),
        },
        "orphaned_metadata_enumeration": orphan_ids,
        "coverage_gaps": [
            {
                "id": "missing_hours",
                "reason": "declared in the unit contract: S3 hourly event "
                          "objects under s3.data-lake/events/<ns>/ are not "
                          "part of the MongoDB document model; no unit in "
                          "this track ingests the events prefix.",
            },
        ],
        "unverified_paths": [
            "live Atlas run (this report is run_mode=fixture; the parent owns "
            "the live validation window)",
            "s3.data-lake/events/<ns>/ missing_hours anomaly (contractual "
            "coverage gap, not ingested by this unit)",
        ],
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")

    failed = [c["id"] for c in checks if c["result"] != "pass"]
    anomaly_gap = (report["planted_anomaly_detections"]["missing"]
                   or report["planted_anomaly_detections"]["unexpected"])
    print(f"[{UNIT}-recon] wrote {out}")
    for c in checks:
        print(f"[{UNIT}-recon] {c['id']}: {c['result']} "
              f"(expected={c['expected']} actual={c['actual']})")
    print(f"[{UNIT}-recon] idempotency_rerun: "
          f"performed={idempotency['performed']} result={idempotency['result']}")
    if failed or anomaly_gap or (args.compare and idempotency["result"] != "pass"):
        print(f"[{UNIT}-recon] FAIL: checks={failed} anomaly_gap={anomaly_gap} "
              f"idempotency={idempotency['result']}",
              file=sys.stderr)
        return 1
    if not args.compare:
        print(f"[{UNIT}-recon] baseline only — rerun migrate_files.py and "
              f"regenerate with --compare {out} for a committable report")
        return 0
    print(f"[{UNIT}-recon] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
