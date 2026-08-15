# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3", "pymongo"]
# ///
"""Migrate one namespace's DynamoDB file metadata into Atlas `ow_tp_<ns>.files`.

Wires the three stages together: paginated scan (`extract`) -> pure per-item
transform (`transform`) -> idempotent bulk upsert (`load`). Runs in bounded
memory: one batch of items is in flight at a time.

Anomalies (orphan storage markers, unparseable timestamps) are counted and
reported here; they are migrated in place, never repaired or dropped. The
authoritative ledger is produced by `recon.py` against Atlas.

Usage:
    MONGODB_ATLAS_URI=... uv run migrations/mongodb/files/migrate.py --ns demo
"""

import argparse
import sys
from datetime import datetime, timezone

from common import COLLECTION, db_name, log, mongo_collection, valid_ns
from extract import batched, scan_items
from load import LoadStats, upsert_documents
from transform import transform_item


def migrate(ns: str, batch_size: int) -> dict:
    collection = mongo_collection(ns)
    migrated_at = datetime.now(timezone.utc)
    stats = LoadStats()
    extracted = orphans = 0
    unparsed_timestamps: list[str] = []

    for batch in batched(scan_items(ns), batch_size):
        documents = []
        for item in batch:
            result = transform_item(item, ns, migrated_at)
            documents.append(result.document)
            if result.orphan:
                orphans += 1
            for target_field in result.unparsed_timestamps:
                unparsed_timestamps.append(f"{result.document['_id']}:{target_field}")
        extracted += len(batch)
        batch_stats = upsert_documents(collection, documents, ns)
        stats.matched += batch_stats.matched
        stats.upserted += batch_stats.upserted
        stats.modified += batch_stats.modified
        log(f"progress: {extracted} items extracted, {stats.written} documents written")

    summary = {
        "ns": ns,
        "collection": f"{db_name(ns)}.{COLLECTION}",
        "extracted": extracted,
        "written": stats.written,
        "inserted": stats.upserted,
        "replaced": stats.matched,
        "orphan_markers": orphans,
        "unparsed_timestamps": unparsed_timestamps,
    }
    log(
        f"done: extracted={extracted} written={stats.written} "
        f"inserted={stats.upserted} replaced={stats.matched} "
        f"orphan_markers={orphans} unparsed_timestamps={len(unparsed_timestamps)}"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument("--batch-size", type=int, default=1_000)
    args = parser.parse_args()

    if not valid_ns(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2

    summary = migrate(args.ns, args.batch_size)
    return 0 if summary["extracted"] == summary["written"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
