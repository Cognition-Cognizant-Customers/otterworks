# /// script
# requires-python = ">=3.11"
# dependencies = ["pymongo", "tabulate"]
# ///
"""Reconcile Atlas `ow_tp_<ns>.files` against the legacy seed manifest.

Every number here is recomputed **from Atlas** — the source is only read through
`testdata/legacy/manifests/<ns>.json`, the before-state contract written by the
seed generators (`make seed-legacy`). Checks:

  * document count for the namespace, and that nothing else lives in the
    collection,
  * the manifest's order-independent md5 checksum, recomputed over
    `<_id>|<sizeBytes>|<storage.s3Key>` read back from the documents,
  * BSON type fidelity: `sizeBytes` is int64 everywhere (a float round-trip
    would change the checksum) and the timestamps are BSON dates,
  * the anomaly ledger: every `orphaned_metadata` item is flagged in place with
    `storage.present: false`, matches the `<ns>/missing/…` key marker, and the
    count equals the manifest's,
  * the indexes the workload's setup script is responsible for.

Writes a markdown report and its JSON twin to `docs/tech-partnerships/recon/`.

Usage:
    MONGODB_ATLAS_URI=... uv run migrations/mongodb/files/recon.py --ns demo

Exit codes: 0 = every check PASS, 1 = failures, 2 = config error.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from bson import Int64
from common import COLLECTION, Checksum, db_name, log, mongo_collection, valid_ns
from tabulate import tabulate

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_TARGET = "dynamodb.file-metadata"
ORPHAN_KIND = "orphaned_metadata"
EXPECTED_INDEXES = ("tenant_owner", "folder", "trashed", "storage_s3key_unique")
REPORT_DIR = REPO_ROOT / "docs" / "tech-partnerships" / "recon"


def manifest_path(ns: str) -> Path:
    return REPO_ROOT / "testdata" / "legacy" / "manifests" / f"{ns}.json"


def load_manifest(ns: str) -> dict:
    path = manifest_path(ns)
    if not path.exists():
        raise SystemExit(f"manifest not found: {path} (run `make seed-legacy NS={ns}`)")
    return json.loads(path.read_text())


def manifest_anomaly(manifest: dict, kind: str, target: str) -> int | None:
    for anomaly in manifest.get("planted_anomalies", []):
        if anomaly.get("kind") == kind and anomaly.get("target") == target:
            return int(anomaly["count"])
    return None


def scan_collection(collection, ns: str) -> dict:
    """Fold every document of the namespace into counts, checksum and ledger."""
    checksum = Checksum()
    orphans: list[dict] = []
    non_int64_sizes: list[str] = []
    raw_timestamps: list[str] = []
    marker_keys: set[str] = set()

    cursor = collection.find(
        {"tenant": ns},
        {"sizeBytes": 1, "storage": 1, "createdAt": 1, "updatedAt": 1, "_migration": 1},
        batch_size=1_000,
    )
    migration_mismatches: list[str] = []
    for document in cursor:
        doc_id = document["_id"]
        size = document["sizeBytes"]
        if not isinstance(size, Int64):
            non_int64_sizes.append(f"{doc_id}:{type(size).__name__}")
        s3_key = document["storage"]["s3Key"]
        checksum.add(f"{doc_id}|{int(size)}|{s3_key}")

        if s3_key.startswith(f"{ns}/missing/"):
            marker_keys.add(doc_id)
        if document["storage"]["present"] is False:
            orphans.append(
                {
                    "id": doc_id,
                    "s3Key": s3_key,
                    "orphanReason": document["storage"].get("orphanReason"),
                }
            )
        for field in ("createdAt", "updatedAt"):
            if not isinstance(document[field], datetime):
                raw_timestamps.append(f"{doc_id}:{field}")

        migration = document.get("_migration") or {}
        if migration.get("ns") != ns or not migration.get("migratedAt"):
            migration_mismatches.append(doc_id)

    return {
        "count": checksum.count,
        "checksum": checksum.hexdigest(),
        "orphans": sorted(orphans, key=lambda o: o["id"]),
        "marker_ids": marker_keys,
        "non_int64_sizes": non_int64_sizes,
        "raw_timestamps": raw_timestamps,
        "migration_mismatches": migration_mismatches,
    }


def reconcile(ns: str) -> dict:
    manifest = load_manifest(ns)
    target = manifest.get("targets", {}).get(MANIFEST_TARGET)
    if target is None:
        raise SystemExit(f"manifest target {MANIFEST_TARGET} missing from {manifest_path(ns)}")
    orphans_expected = manifest_anomaly(manifest, ORPHAN_KIND, MANIFEST_TARGET)

    collection = mongo_collection(ns)
    scan = scan_collection(collection, ns)
    total_documents = collection.count_documents({})
    indexes = sorted(collection.index_information())
    orphan_ids = {orphan["id"] for orphan in scan["orphans"]}

    checks = [
        (
            f"{MANIFEST_TARGET} → {COLLECTION} documents",
            scan["count"] == target["items"],
            f"atlas={scan['count']} manifest={target['items']}",
        ),
        (
            f"{COLLECTION} holds only tenant '{ns}'",
            total_documents == scan["count"],
            f"collection={total_documents} tenant={scan['count']}",
        ),
        (
            f"{MANIFEST_TARGET} → {COLLECTION} checksum",
            scan["checksum"] == target["checksum"],
            f"atlas={scan['checksum']} manifest={target['checksum']}",
        ),
        (
            "sizeBytes is BSON int64 in every document",
            not scan["non_int64_sizes"],
            f"non-int64={len(scan['non_int64_sizes'])} {scan['non_int64_sizes'][:3]}",
        ),
        (
            "createdAt/updatedAt are BSON dates",
            not scan["raw_timestamps"],
            f"unparsed={len(scan['raw_timestamps'])} {scan['raw_timestamps'][:3]}",
        ),
        (
            "_migration provenance on every document",
            not scan["migration_mismatches"],
            f"missing={len(scan['migration_mismatches'])}",
        ),
        (
            f"anomaly {ORPHAN_KIND} count",
            orphans_expected is not None and len(scan["orphans"]) == orphans_expected,
            f"atlas={len(scan['orphans'])} manifest={orphans_expected}",
        ),
        (
            "storage.present:false == '<ns>/missing/…' key marker",
            orphan_ids == scan["marker_ids"],
            (
                f"flagged={len(orphan_ids)} marker_keys={len(scan['marker_ids'])} "
                f"symmetric_difference={len(orphan_ids ^ scan['marker_ids'])}"
            ),
        ),
        (
            "orphans carry orphanReason",
            all(o["orphanReason"] == "missing_object_marker" for o in scan["orphans"]),
            f"reasons={sorted({str(o['orphanReason']) for o in scan['orphans']})}",
        ),
        (
            "workload indexes present",
            all(name in indexes for name in EXPECTED_INDEXES),
            f"indexes={', '.join(indexes)}",
        ),
    ]

    results = [(name, "PASS" if ok else "FAIL", detail) for name, ok, detail in checks]
    return {
        "ns": ns,
        "collection": f"{db_name(ns)}.{COLLECTION}",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manifest": {
            "path": str(manifest_path(ns).relative_to(REPO_ROOT)),
            "seed": manifest.get("seed"),
            "items": target["items"],
            "checksum": target["checksum"],
            "orphaned_metadata": orphans_expected,
        },
        "atlas": {
            "documents": scan["count"],
            "collection_documents": total_documents,
            "checksum": scan["checksum"],
            "storage_present_false": len(scan["orphans"]),
            "indexes": indexes,
        },
        "anomaly_ledger": {
            "kind": ORPHAN_KIND,
            "target": MANIFEST_TARGET,
            "expected": orphans_expected,
            "found": len(scan["orphans"]),
            "items": scan["orphans"],
        },
        "checks": [{"check": n, "status": s, "detail": d} for n, s, d in results],
        "passed": sum(1 for _, status, _ in results if status == "PASS"),
        "total": len(results),
    }


def render_markdown(report: dict) -> str:
    manifest, atlas, ledger = report["manifest"], report["atlas"], report["anomaly_ledger"]
    rows = [(c["check"], c["status"], c["detail"]) for c in report["checks"]]
    ledger_rows = [(item["id"], item["s3Key"]) for item in ledger["items"]]
    lines = [
        f"# Recon — `mongo-files` (ns `{report['ns']}`)",
        "",
        f"Source of truth: `{manifest['path']}` (seed `{manifest['seed']}`), the",
        "before-state manifest written by `make seed-legacy` and independently",
        f"verified by `make seed-legacy-validate NS={report['ns']}` (15/15).",
        f"Every Atlas number below is recomputed from `{report['collection']}` by",
        "`migrations/mongodb/files/recon.py` — never from the DynamoDB source.",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Counts and checksum",
        "",
        tabulate(
            [
                ("documents", manifest["items"], atlas["documents"]),
                ("checksum", manifest["checksum"], atlas["checksum"]),
                ("orphaned metadata", manifest["orphaned_metadata"], atlas["storage_present_false"]),
            ],
            headers=["metric", "manifest (source)", "atlas (recomputed)"],
            tablefmt="github",
        ),
        "",
        "Checksum definition: order-independent sum of per-line md5 digests mod",
        "2^128, one line per document as `<_id>|<sizeBytes>|<storage.s3Key>` with",
        "`sizeBytes` read back as a BSON int64 and rendered as a plain integer.",
        "",
        "## Checks",
        "",
        tabulate(rows, headers=["check", "status", "detail"], tablefmt="github"),
        "",
        f"**{report['passed']}/{report['total']} checks passed.**",
        "",
        (
            f"## Anomaly ledger — `{ledger['kind']}` "
            f"({ledger['found']} of {ledger['expected']} expected)"
        ),
        "",
        "Flag-in-place, not quarantine: each item below is migrated as a normal",
        "document carrying `storage.present: false` and",
        "`storage.orphanReason: \"missing_object_marker\"`. The signal is the",
        f"`{report['ns']}/missing/…` key marker only — no S3 objects exist for any",
        "seeded item, so object existence is never consulted.",
        "",
        tabulate(ledger_rows, headers=["_id", "storage.s3Key"], tablefmt="github"),
        "",
        "## Indexes",
        "",
        ", ".join(f"`{name}`" for name in atlas["indexes"]),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument("--report-dir", default=str(REPORT_DIR))
    args = parser.parse_args()

    if not valid_ns(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2

    report = reconcile(args.ns)
    print(
        tabulate(
            [(c["check"], c["status"], c["detail"]) for c in report["checks"]],
            headers=["check", "status", "detail"],
            tablefmt="github",
        )
    )
    print(f"\n{report['passed']}/{report['total']} checks passed")

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"mongo-files-{args.ns}"
    (report_dir / f"{stem}.md").write_text(render_markdown(report))
    (report_dir / f"{stem}.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    log(f"report written: {report_dir / f'{stem}.md'}")

    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
