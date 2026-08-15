# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg2-binary", "pymongo"]
# ///
"""Run the `documents` migration: legacy Postgres -> Atlas `ow_tp_demo`.

Extract (server-side cursor, batched) -> transform (pure) -> load (idempotent
upsert by source primary key). Anomalies are carried through and counted, never
repaired: version gaps stay visible as `declaredVersion != versionCount` plus a
`versionGap` subdocument, and snapshots whose document is missing are written to
`document_snapshots_orphaned` with `quarantine_reason: "missing_document"`.

Usage:
    uv run migrations/mongodb/documents/migrate.py --ns demo [--batch-size 500]
"""

import argparse
import sys
import time
from datetime import datetime, timezone

from extract import (
    DEFAULT_BATCH_SIZE,
    connect,
    iter_document_batches,
    iter_orphaned_snapshots,
)
from load import upsert_documents
from mongo_common import (
    COLL_DOCUMENTS,
    COLL_SNAPSHOTS,
    COLL_SNAPSHOTS_ORPHANED,
    SOURCE_TABLE_DOCUMENTS,
    SOURCE_TABLE_SNAPSHOTS,
    atlas_client,
    atlas_db,
    log,
    pg_config,
    schema_name,
    source_table,
    valid_ns,
)
from transform import (
    QUARANTINE_MISSING_DOCUMENT,
    transform_document,
    transform_snapshot,
)


def migrate(ns: str, batch_size: int) -> dict:
    schema = schema_name(ns)
    docs_source = source_table(ns, SOURCE_TABLE_DOCUMENTS)
    snaps_source = source_table(ns, SOURCE_TABLE_SNAPSHOTS)
    migrated_at = datetime.now(timezone.utc)

    stats = {
        "documents": 0,
        "embedded_versions": 0,
        "snapshots": 0,
        "orphaned_snapshots": 0,
        "version_gaps": 0,
    }

    pg = connect(pg_config())
    client = atlas_client()
    try:
        db = atlas_db(client)
        for batch in iter_document_batches(pg, schema, batch_size):
            mongo_docs = []
            mongo_snaps = []
            for row in batch.documents:
                doc_id = str(row["id"])
                snaps = batch.snapshots_by_document.get(doc_id, [])
                doc = transform_document(
                    row,
                    batch.versions_by_document.get(doc_id, []),
                    snaps,
                    ns,
                    docs_source,
                    migrated_at,
                )
                mongo_docs.append(doc)
                mongo_snaps.extend(
                    transform_snapshot(s, ns, snaps_source, migrated_at) for s in snaps
                )
                stats["embedded_versions"] += doc["versionCount"]
                if "versionGap" in doc:
                    stats["version_gaps"] += 1
            stats["documents"] += upsert_documents(db[COLL_DOCUMENTS], mongo_docs)
            stats["snapshots"] += upsert_documents(db[COLL_SNAPSHOTS], mongo_snaps)
            log(f"documents upserted: {stats['documents']}")

        for orphans in iter_orphaned_snapshots(pg, schema, batch_size):
            quarantined = [
                transform_snapshot(
                    s, ns, snaps_source, migrated_at,
                    quarantine_reason=QUARANTINE_MISSING_DOCUMENT,
                )
                for s in orphans
            ]
            stats["orphaned_snapshots"] += upsert_documents(
                db[COLL_SNAPSHOTS_ORPHANED], quarantined)
    finally:
        pg.close()
        client.close()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()

    if not valid_ns(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2

    if args.batch_size < 1:
        print("--batch-size must be >= 1", file=sys.stderr)
        return 2

    started = time.monotonic()
    stats = migrate(args.ns, args.batch_size)
    log(
        f"{stats['documents']} documents, {stats['embedded_versions']} embedded versions, "
        f"{stats['snapshots']} snapshots, {stats['orphaned_snapshots']} orphaned snapshots "
        f"({stats['version_gaps']} version gaps carried through)"
    )
    log(f"total: {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
