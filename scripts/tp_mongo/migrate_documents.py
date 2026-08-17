#!/usr/bin/env python3
"""Migrate the legacy Postgres document estate into MongoDB."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    DOCUMENTS,
    QUARANTINE,
    SNAPSHOTS,
    mongo_client,
    pg_connect,
    source_schema,
    target_db_name,
    validate_namespace,
)
from documents_model import (
    VALIDATORS,
    document_id_from_row,
    process_document,
    process_snapshot,
    process_version,
)
from pymongo import ASCENDING, ReplaceOne

BATCH_SIZE = 500


def replace_batch(collection, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    collection.bulk_write(
        [ReplaceOne({"_id": record["_id"]}, record, upsert=True) for record in records],
        ordered=False,
    )


def ensure_target(db) -> None:
    existing = set(db.list_collection_names())
    for name, validator in VALIDATORS.items():
        if name not in existing:
            db.create_collection(
                name,
                validator=validator,
                validationLevel="strict",
                validationAction="error",
            )
        else:
            db.command(
                "collMod",
                name,
                validator=validator,
                validationLevel="strict",
                validationAction="error",
            )

    db[DOCUMENTS].create_index([("owner_id", ASCENDING)])
    db[DOCUMENTS].create_index([("ns", ASCENDING)])
    db[SNAPSHOTS].create_index([("document_id", ASCENDING)])
    db[SNAPSHOTS].create_index([("parent_missing", ASCENDING)])


def migrate(ns: str) -> dict[str, int]:
    schema = source_schema(ns)
    conn = pg_connect(ns)
    client = None
    documents_written = 0
    versions_embedded = 0
    snapshots_written = 0
    quarantined = 0
    gaps_detected = 0
    orphans_detected = 0
    try:
        document_cursor = conn.cursor(name="ow_tp_documents")
        document_cursor.itersize = BATCH_SIZE
        document_cursor.execute(
            f"""
            SELECT id, title, content, content_type, owner_id, folder_id,
                   is_deleted, is_template, word_count, version,
                   created_at, updated_at
              FROM {schema}.documents
             ORDER BY id
            """
        )
        first_batch = document_cursor.fetchmany(BATCH_SIZE)
        if not first_batch:
            raise RuntimeError(
                f"source read returned zero documents for {schema}.documents; "
                "no target writes were attempted"
            )

        client = mongo_client()
        client.admin.command("ping")
        db = client[target_db_name(ns)]
        ensure_target(db)

        source_document_ids: set[str] = set()
        quarantined_document_ids: set[str] = set()
        written_document_ids: set[str] = set()
        written_snapshot_ids: set[str] = set()
        document_collection = db[DOCUMENTS]
        quarantine_collection = db[QUARANTINE]

        batch = first_batch
        while batch:
            for row in batch:
                document_id = document_id_from_row(row)
                if document_id is not None:
                    source_document_ids.add(document_id)

            document_ids = [
                document_id_from_row(row)
                for row in batch
                if document_id_from_row(row) is not None
            ]
            versions_by_document: dict[str, list[dict[str, Any] | None]] = defaultdict(list)
            version_quarantine: list[dict[str, Any]] = []
            version_cursor = conn.cursor(name=f"ow_tp_versions_{documents_written}")
            version_cursor.itersize = BATCH_SIZE
            version_cursor.execute(
                f"""
                SELECT id, document_id, version_number, title, content,
                       created_by, created_at
                  FROM {schema}.document_versions
                 WHERE document_id = ANY(%s::uuid[])
                 ORDER BY document_id, version_number
                """,
                (document_ids,),
            )
            for version_row in version_cursor:
                document_id, version, quarantine = process_version(ns, version_row)
                versions_by_document[document_id].append(version)
                if quarantine is not None:
                    version_quarantine.append(quarantine)
            version_cursor.close()

            document_records: list[dict[str, Any]] = []
            document_quarantine: list[dict[str, Any]] = []
            for row in batch:
                document_id = document_id_from_row(row) or "<null>"
                document, quarantine, has_gap = process_document(
                    ns,
                    row,
                    versions_by_document.get(document_id, []),
                )
                if document is not None:
                    document_records.append(document)
                    written_document_ids.add(document["_id"])
                    documents_written += 1
                    versions_embedded += len(document["versions"])
                    gaps_detected += int(has_gap)
                elif quarantine is not None:
                    document_quarantine.append(quarantine)
                    if document_id != "<null>":
                        quarantined_document_ids.add(document_id)

            replace_batch(document_collection, document_records)
            replace_batch(
                quarantine_collection,
                version_quarantine + document_quarantine,
            )
            quarantined += len(version_quarantine) + len(document_quarantine)
            batch = document_cursor.fetchmany(BATCH_SIZE)
        document_cursor.close()

        snapshot_cursor = conn.cursor(name="ow_tp_snapshots")
        snapshot_cursor.itersize = BATCH_SIZE
        snapshot_cursor.execute(
            f"""
            SELECT id, document_id, state_b64, label, created_by, created_at
              FROM {schema}.document_snapshots
             ORDER BY id
            """
        )
        snapshot_records: list[dict[str, Any]] = []
        snapshot_quarantine: list[dict[str, Any]] = []
        for snapshot_row in snapshot_cursor:
            snapshot, quarantine, is_orphan = process_snapshot(
                ns,
                snapshot_row,
                source_document_ids,
                quarantined_document_ids,
            )
            if quarantine is not None:
                snapshot_quarantine.append(quarantine)
                continue
            if snapshot is not None:
                snapshot_records.append(snapshot)
                written_snapshot_ids.add(snapshot["_id"])
                orphans_detected += int(is_orphan)
            if len(snapshot_records) >= BATCH_SIZE:
                replace_batch(db[SNAPSHOTS], snapshot_records)
                replace_batch(quarantine_collection, snapshot_quarantine)
                snapshots_written += len(snapshot_records)
                quarantined += len(snapshot_quarantine)
                snapshot_records = []
                snapshot_quarantine = []

        snapshot_cursor.close()
        replace_batch(db[SNAPSHOTS], snapshot_records)
        replace_batch(quarantine_collection, snapshot_quarantine)
        snapshots_written += len(snapshot_records)
        quarantined += len(snapshot_quarantine)
        db[DOCUMENTS].delete_many(
            {"ns": ns, "_id": {"$nin": list(written_document_ids)}}
        )
        db[SNAPSHOTS].delete_many(
            {"ns": ns, "_id": {"$nin": list(written_snapshot_ids)}}
        )
        return {
            "documents": documents_written,
            "versions": versions_embedded,
            "snapshots": snapshots_written,
            "quarantined": quarantined,
            "gaps": gaps_detected,
            "orphans": orphans_detected,
        }
    finally:
        if client is not None:
            client.close()
        conn.rollback()
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ns", default="demo")
    args = parser.parse_args()
    validate_namespace(args.ns)
    try:
        summary = migrate(args.ns)
    except RuntimeError as exc:
        print(f"[migration] error: {exc}", file=sys.stderr)
        return 1
    print(
        f"[migration] ns={args.ns} target={target_db_name(args.ns)} "
        f"documents written={summary['documents']} "
        f"versions embedded={summary['versions']} "
        f"snapshots written={summary['snapshots']} "
        f"quarantined={summary['quarantined']} "
        f"gaps detected={summary['gaps']} "
        f"orphans detected={summary['orphans']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
