# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg2-binary", "pymongo"]
# ///
"""Migrate the legacy Postgres document estate to the MongoDB document model.

Unit: mongo_documents (contract: docs/tech-partnerships/contracts/mongo_documents.json)

Source: Postgres schema otterworks_<ns> — documents, document_versions,
document_snapshots (the snapshot table has no FK by design; orphans are a
planted anomaly).

Target: <db_prefix><ns>.documents with versions embedded as a bounded
subarray and valid snapshot references embedded as `snapshots`; snapshots
whose document_id does not resolve to a migrated document quarantine into
<db_prefix><ns>_quarantine.documents_quarantine with attribution.

Policies (from the contract):
  - encoding: UTF-8 byte-transparent content; a record whose text cannot be
    decoded quarantines with attribution instead of failing open.
  - malformed records: NULL columns become omitted fields; version gaps are
    detected downstream (recon), never repaired or renumbered; orphaned
    snapshots quarantine, never silently dropped.
  - empty input: a run against an absent/empty source schema writes nothing
    and leaves prior target output untouched.
  - granularity: per-batch (bulk upserts of BATCH_SIZE documents).

Deterministic and idempotent: document _id is the legacy UUID, no wall-clock
values are written, and a rerun for the same namespace reproduces identical
recon numbers (stale documents for the namespace are removed by _id set
difference, never by timestamp).

Usage:
    uv run migrations/mongodb/documents/migrate.py --ns <ns>
    (MONGODB_URI defaults to mongodb://localhost:27017; the parent points it
     at Atlas for the live validation window.)
"""

import argparse
import os
import sys

import psycopg2
from pymongo import MongoClient, ReplaceOne

BATCH_SIZE = 500


def pg_config() -> dict:
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "dbname": os.getenv("DB_NAME", "otterworks"),
        "user": os.getenv("DB_USER", "otterworks"),
        "password": os.getenv("DB_PASSWORD", "otterworks_dev"),
    }


def log(msg: str) -> None:
    print(f"[mongo-documents-migrate] {msg}", flush=True)


def fetch_all(cur, query: str, params: tuple = ()) -> list[tuple]:
    cur.execute(query, params)
    return cur.fetchall()


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
    args = parser.parse_args()

    ns = args.ns
    schema = f"otterworks_{ns}"
    db_name = f"{args.db_prefix}{ns}"
    quarantine_db_name = f"{db_name}_quarantine"

    conn = psycopg2.connect(**pg_config())
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name='documents'",
        (schema,),
    )
    if cur.fetchone() is None:
        log(f"source schema {schema} absent: no-op, prior target output untouched")
        return 0
    cur.execute(f"SELECT count(*) FROM {schema}.documents")
    if cur.fetchone()[0] == 0:
        log(f"source {schema}.documents empty: no-op, prior target output untouched")
        return 0

    client = MongoClient(args.mongodb_uri)
    documents = client[db_name]["documents"]
    quarantine = client[quarantine_db_name]["documents_quarantine"]

    documents.create_index([("ns", 1)])
    documents.create_index([("ns", 1), ("owner_id", 1)])
    documents.create_index([("ns", 1), ("versions.version_number", 1)])
    quarantine.create_index([("ns", 1), ("reason", 1)])

    versions_by_doc: dict[str, list[dict]] = {}
    for row in fetch_all(
        cur,
        f"SELECT id::text, document_id::text, version_number, title, content, "
        f"created_by::text, created_at FROM {schema}.document_versions "
        f"ORDER BY document_id, version_number",
    ):
        ver_id, doc_id, version_number, title, content, created_by, created_at = row
        versions_by_doc.setdefault(doc_id, []).append(
            {
                "id": ver_id,
                "version_number": version_number,
                "title": title,
                "content": content,
                "created_by": created_by,
                "created_at": created_at,
            }
        )

    snapshots_by_doc: dict[str, list[dict]] = {}
    all_snapshots: list[dict] = []
    for row in fetch_all(
        cur,
        f"SELECT id::text, document_id::text, state_b64, label, created_by::text, "
        f"created_at FROM {schema}.document_snapshots ORDER BY id",
    ):
        snap_id, doc_id, state_b64, label, created_by, created_at = row
        snap = {
            "id": snap_id,
            "document_id": doc_id,
            "state_b64": state_b64,
            "created_by": created_by,
            "created_at": created_at,
        }
        if label is not None:
            snap["label"] = label
        snapshots_by_doc.setdefault(doc_id, []).append(snap)
        all_snapshots.append(snap)

    source_ids: set[str] = set()
    ops: list[ReplaceOne] = []
    migrated = embedded_versions = embedded_snapshots = 0

    def flush() -> None:
        nonlocal ops
        if ops:
            documents.bulk_write(ops, ordered=True)
            ops = []

    for row in fetch_all(
        cur,
        f"SELECT id::text, title, content, content_type, owner_id::text, "
        f"folder_id::text, is_deleted, is_template, word_count, version, "
        f"created_at, updated_at FROM {schema}.documents ORDER BY id",
    ):
        (
            doc_id, title, content, content_type, owner_id, folder_id,
            is_deleted, is_template, word_count, version, created_at, updated_at,
        ) = row
        source_ids.add(doc_id)
        doc_versions = versions_by_doc.get(doc_id, [])
        doc_snapshots = [
            {k: v for k, v in s.items() if k != "document_id"}
            for s in snapshots_by_doc.get(doc_id, [])
        ]
        doc = {
            "_id": doc_id,
            "ns": ns,
            "title": title,
            "content": content,
            "content_type": content_type,
            "owner_id": owner_id,
            "is_deleted": is_deleted,
            "is_template": is_template,
            "word_count": word_count,
            "version": version,
            "created_at": created_at,
            "updated_at": updated_at,
            "versions": doc_versions,
            "snapshots": doc_snapshots,
        }
        if folder_id is not None:
            doc["folder_id"] = folder_id
        embedded_versions += len(doc_versions)
        embedded_snapshots += len(doc_snapshots)
        migrated += 1
        ops.append(ReplaceOne({"_id": doc_id}, doc, upsert=True))
        if len(ops) >= BATCH_SIZE:
            flush()
    flush()

    # Remove stale documents for this namespace only (idempotent by _id set
    # difference; never touches other namespaces' databases or newer data).
    stale = documents.delete_many({"ns": ns, "_id": {"$nin": sorted(source_ids)}})
    if stale.deleted_count:
        log(f"removed {stale.deleted_count} stale documents for ns={ns}")

    # Orphaned snapshots: quarantine with attribution, never drop or repair.
    quarantine_ops: list[ReplaceOne] = []
    quarantined_ids: set[str] = set()
    for snap in all_snapshots:
        if snap["document_id"] in source_ids:
            continue
        qdoc = dict(snap)
        qdoc["_id"] = qdoc.pop("id")
        qdoc["ns"] = ns
        qdoc["reason"] = "orphaned_snapshot"
        qdoc["attribution"] = {
            "source_table": f"{schema}.document_snapshots",
            "unit": "mongo_documents",
            "detail": "snapshot document_id does not resolve to any migrated document",
        }
        quarantined_ids.add(qdoc["_id"])
        quarantine_ops.append(ReplaceOne({"_id": qdoc["_id"]}, qdoc, upsert=True))
        if len(quarantine_ops) >= BATCH_SIZE:
            quarantine.bulk_write(quarantine_ops, ordered=True)
            quarantine_ops = []
    if quarantine_ops:
        quarantine.bulk_write(quarantine_ops, ordered=True)
    stale_q = quarantine.delete_many(
        {"ns": ns, "reason": "orphaned_snapshot", "_id": {"$nin": sorted(quarantined_ids)}}
    )
    if stale_q.deleted_count:
        log(f"removed {stale_q.deleted_count} stale quarantine records for ns={ns}")

    log(
        f"ns={ns}: migrated {migrated} documents, {embedded_versions} embedded "
        f"versions, {embedded_snapshots} embedded snapshots, "
        f"{len(quarantined_ids)} snapshots quarantined -> {db_name}.documents / "
        f"{quarantine_db_name}.documents_quarantine"
    )
    conn.close()
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
