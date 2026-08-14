# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg2-binary", "pymongo"]
# ///
"""
Migrate the seeded Postgres document estate (otterworks_<ns>.documents +
document_versions + document_snapshots) into MongoDB Atlas.

Document model (vs the 3-table relational shape):

  ow_tp_<ns>.documents          one document per legacy `documents` row with
                                its version history EMBEDDED as a bounded
                                `versions` array (demo scale: 2-12/doc) —
                                the app's "load doc + history" read collapses
                                to a single document fetch.
  ow_tp_<ns>.document_snapshots snapshots stay a separate, REFERENCED
                                collection (`document_id`): unbounded blobs,
                                no FK in the legacy table, and orphans must
                                survive the move for reconciliation.

Streaming: server-side cursors ordered by document_id merge-join documents
with their versions; nothing is ever fully materialized in memory.
Idempotent: the run wipes and rebuilds only the ow_tp_<ns> collections.

Usage:
    uv run migrations/mongodb/migrate_documents.py --ns <ns>
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import psycopg2
from pymongo import InsertOne

from mongo_common import (
    BATCH,
    DOCUMENTS_COLLECTION,
    SNAPSHOTS_COLLECTION,
    db_name,
    log,
    mongo_client,
    pg_config,
    schema_name,
    valid_ns,
)


def utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def flush(coll, ops: list) -> int:
    if ops:
        coll.bulk_write(ops, ordered=False)
    n = len(ops)
    ops.clear()
    return n


def migrate(ns: str) -> None:
    schema = schema_name(ns)
    client = mongo_client()
    db = client[db_name(ns)]
    docs_coll = db[DOCUMENTS_COLLECTION]
    snaps_coll = db[SNAPSHOTS_COLLECTION]

    # connect and verify the source schema BEFORE wiping the target so an
    # unreachable/missing source leaves previously migrated data intact
    conn = psycopg2.connect(**pg_config())
    started = time.monotonic()
    try:
        with conn.cursor() as probe:
            probe.execute(f"SELECT 1 FROM {schema}.documents LIMIT 1")
        docs_coll.drop()
        snaps_coll.drop()
        # Two ordered server-side cursors, merge-joined on document_id.
        doc_cur = conn.cursor(name="mig_docs")
        doc_cur.itersize = BATCH
        doc_cur.execute(f"""
            SELECT id::text, title, content, content_type, owner_id::text,
                   folder_id::text, is_deleted, is_template, word_count,
                   version, created_at, updated_at
              FROM {schema}.documents ORDER BY id
        """)

        ver_cur = conn.cursor(name="mig_vers")
        ver_cur.itersize = BATCH
        ver_cur.execute(f"""
            SELECT document_id::text, version_number, title, content,
                   created_by::text, created_at
              FROM {schema}.document_versions
             ORDER BY document_id, version_number
        """)
        ver_iter = iter(ver_cur)
        pending = next(ver_iter, None)

        ops: list = []
        n_docs = n_vers = n_orphan_vers = 0
        for row in doc_cur:
            (doc_id, title, content, ctype, owner, folder, deleted,
             template, words, declared_version, created, updated) = row

            # skip version rows whose parent document does not exist so the
            # merge join stays aligned; they are counted, not silently lost
            while pending is not None and pending[0] < doc_id:
                n_orphan_vers += 1
                pending = next(ver_iter, None)

            versions = []
            while pending is not None and pending[0] == doc_id:
                _, vnum, vtitle, vcontent, vby, vcreated = pending
                versions.append({
                    "version": vnum,
                    "title": vtitle,
                    "content": vcontent,
                    "created_by": vby,
                    "created_at": utc(vcreated),
                })
                pending = next(ver_iter, None)
            n_vers += len(versions)

            ops.append(InsertOne({
                "_id": doc_id,
                "title": title,
                "content": content,
                "content_type": ctype,
                "owner_id": owner,
                "folder_id": folder,
                "is_deleted": deleted,
                "is_template": template,
                "word_count": words,
                # declared version from the legacy row; a mismatch with
                # len(versions) is a planted version-gap anomaly.
                "version": declared_version,
                "version_count": len(versions),
                "versions": versions,
                "created_at": utc(created),
                "updated_at": utc(updated),
            }))
            n_docs += 1
            if len(ops) >= BATCH:
                flush(docs_coll, ops)
        flush(docs_coll, ops)
        while pending is not None:
            n_orphan_vers += 1
            pending = next(ver_iter, None)
        if n_orphan_vers:
            log("migrate-documents",
                f"WARNING ns={ns}: {n_orphan_vers} version rows have no "
                "parent document and were not migrated")
        doc_cur.close()
        ver_cur.close()

        snap_cur = conn.cursor(name="mig_snaps")
        snap_cur.itersize = BATCH
        snap_cur.execute(f"""
            SELECT id::text, document_id::text, state_b64, label,
                   created_by::text, created_at
              FROM {schema}.document_snapshots
        """)
        n_snaps = 0
        for snap_id, doc_id, state, label, by, created in snap_cur:
            ops.append(InsertOne({
                "_id": snap_id,
                "document_id": doc_id,
                "state_b64": state,
                "label": label,
                "created_by": by,
                "created_at": utc(created),
            }))
            n_snaps += 1
            if len(ops) >= BATCH:
                flush(snaps_coll, ops)
        flush(snaps_coll, ops)
        snap_cur.close()
    finally:
        conn.close()

    docs_coll.create_index("owner_id")
    snaps_coll.create_index("document_id")
    log("migrate-documents",
        f"ns={ns}: {n_docs} documents ({n_vers} embedded versions), "
        f"{n_snaps} snapshots -> {db_name(ns)} in {time.monotonic() - started:.1f}s")
    client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    args = parser.parse_args()
    if not valid_ns(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2
    migrate(args.ns)
    return 0


if __name__ == "__main__":
    sys.exit(main())
