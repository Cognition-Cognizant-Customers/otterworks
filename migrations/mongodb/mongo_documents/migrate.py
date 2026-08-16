# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg2-binary", "pymongo"]
# ///
"""mongo_documents migration: Postgres otterworks_<ns> -> MongoDB.

Source: otterworks_<ns>.documents / document_versions / document_snapshots.
Target: ow_tp_mongodb_<ns>.documents (versions embedded as a bounded subarray,
valid snapshots embedded as references) and
ow_tp_mongodb_<ns>_quarantine.documents_quarantine (orphaned snapshots and any
record that violates the contract's NULL/encoding policy, with attribution).

Contract: docs/tech-partnerships/contracts/mongo_documents.json.

Policies implemented:
  - NULL attribution: nullable columns (folder_id, label) become omitted
    fields; a NULL in a contractually NOT NULL column quarantines the record
    with attribution instead of failing open into a valid-looking document.
  - Byte transparency: document content is carried byte-for-byte as UTF-8
    text, including embedded control characters; undecodable bytes quarantine.
  - Empty input: an absent or empty source schema is a no-op — nothing is
    written and prior target output is left untouched.
  - Anomalies (version gaps, orphaned snapshots) are surfaced, never repaired.
  - Idempotency: upserts keyed on uuid5-derived _ids, per-batch, with no
    embedded wall-clock values, so reruns reproduce identical recon numbers.

Usage:
    uv run migrations/mongodb/mongo_documents/migrate.py --ns <ns>
"""

from __future__ import annotations

import argparse
import re
import sys

import psycopg2
import psycopg2.extras
from pymongo import MongoClient, ReplaceOne

from common import (
    BATCH_SIZE,
    UNIT,
    contiguous_gaps,
    det_id,
    mongo_uri,
    pg_config,
    pg_schema,
    quarantine_db_name,
    target_db_name,
)

NS_PATTERN = re.compile(r"[A-Za-z0-9_]+")

DOC_NOT_NULL = ("id", "title", "content", "content_type", "owner_id",
                "is_deleted", "is_template", "word_count", "version",
                "created_at", "updated_at")
VER_NOT_NULL = ("id", "document_id", "version_number", "title", "content",
                "created_by", "created_at")
SNAP_NOT_NULL = ("id", "document_id", "state_b64", "created_by", "created_at")


def log(msg: str) -> None:
    print(f"[{UNIT}] {msg}", flush=True)


class QuarantineError(Exception):
    def __init__(self, reason: str, field: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.field = field


def null_violations(row: dict, not_null: tuple[str, ...]) -> None:
    for col in not_null:
        if row.get(col) is None:
            raise QuarantineError(f"NULL in NOT NULL column '{col}'", field=col)


def iso(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def quarantine_doc(ns: str, kind: str, source_table: str, row: dict,
                   reason: str) -> dict:
    source = {k: (iso(v) if hasattr(v, "strftime") else v)
              for k, v in row.items() if v is not None}
    return {
        "_id": det_id(ns, f"quarantine:{kind}", str(row["id"])),
        "unit": UNIT,
        "ns": ns,
        "kind": kind,
        "source_table": source_table,
        "source": source,
        "attribution": {"reason": reason},
    }


def schema_exists(cur, schema: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
        (schema,),
    )
    return cur.fetchone() is not None


def fetch_all(cur, schema: str, table: str, order_by: str) -> list[dict]:
    cur.execute(f"SELECT * FROM {schema}.{table} ORDER BY {order_by}")
    return [dict(r) for r in cur.fetchall()]


def build_document(ns: str, row: dict, versions: list[dict],
                   snapshots: list[dict]) -> dict:
    null_violations(row, DOC_NOT_NULL)
    doc_id = str(row["id"])
    doc = {
        "_id": det_id(ns, "document", doc_id),
        "unit": UNIT,
        "ns": ns,
        "source_id": doc_id,
        "title": row["title"],
        "content": row["content"],
        "content_type": row["content_type"],
        "owner_id": str(row["owner_id"]),
        "is_deleted": row["is_deleted"],
        "is_template": row["is_template"],
        "word_count": row["word_count"],
        "version": row["version"],
        "created_at": iso(row["created_at"]),
        "updated_at": iso(row["updated_at"]),
        "versions": [],
        "snapshots": [],
    }
    if row.get("folder_id") is not None:  # NULL columns become omitted fields
        doc["folder_id"] = str(row["folder_id"])

    for ver in sorted(versions, key=lambda v: v["version_number"]):
        null_violations(ver, VER_NOT_NULL)
        doc["versions"].append({
            "source_id": str(ver["id"]),
            "version_number": ver["version_number"],
            "title": ver["title"],
            "content": ver["content"],
            "created_by": str(ver["created_by"]),
            "created_at": iso(ver["created_at"]),
        })

    for snap in sorted(snapshots, key=lambda s: str(s["id"])):
        null_violations(snap, SNAP_NOT_NULL)
        ref = {
            "source_id": str(snap["id"]),
            "state_b64": snap["state_b64"],
            "created_by": str(snap["created_by"]),
            "created_at": iso(snap["created_at"]),
        }
        if snap.get("label") is not None:
            ref["label"] = snap["label"]
        doc["snapshots"].append(ref)

    return doc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    args = parser.parse_args()
    ns = args.ns
    if not NS_PATTERN.fullmatch(ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2

    schema = pg_schema(ns)
    conn = psycopg2.connect(**pg_config())
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if not schema_exists(cur, schema):
            log(f"source schema {schema} absent: no-op, target untouched")
            return 0
        docs = fetch_all(cur, schema, "documents", "id")
        if not docs:
            log(f"source schema {schema} empty: no-op, target untouched")
            return 0
        versions = fetch_all(cur, schema, "document_versions", "document_id, version_number")
        snapshots = fetch_all(cur, schema, "document_snapshots", "id")
    finally:
        conn.close()

    versions_by_doc: dict[str, list[dict]] = {}
    for ver in versions:
        versions_by_doc.setdefault(str(ver["document_id"]), []).append(ver)
    snaps_by_doc: dict[str, list[dict]] = {}
    for snap in snapshots:
        snaps_by_doc.setdefault(str(snap["document_id"]), []).append(snap)

    doc_ids = {str(row["id"]) for row in docs}
    quarantine_ops: list[ReplaceOne] = []
    doc_ops: list[ReplaceOne] = []
    embedded_versions = 0
    gap_docs: list[str] = []
    orphan_snaps: list[str] = []

    for row in docs:
        doc_id = str(row["id"])
        try:
            doc = build_document(
                ns, row, versions_by_doc.get(doc_id, []),
                snaps_by_doc.get(doc_id, []),
            )
        except QuarantineError as exc:
            q = quarantine_doc(ns, "policy_violation", f"{schema}.documents",
                               row, exc.reason)
            quarantine_ops.append(ReplaceOne({"_id": q["_id"]}, q, upsert=True))
            continue
        embedded_versions += len(doc["versions"])
        gaps = contiguous_gaps([v["version_number"] for v in doc["versions"]],
                               doc["version"])
        if gaps:
            gap_docs.append(doc_id)
        doc_ops.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))

    for snap in snapshots:  # snapshots referencing missing documents quarantine
        if str(snap["document_id"]) not in doc_ids:
            q = quarantine_doc(
                ns, "orphaned_snapshot", f"{schema}.document_snapshots", snap,
                f"document_id {snap['document_id']} not found in {schema}.documents",
            )
            quarantine_ops.append(ReplaceOne({"_id": q["_id"]}, q, upsert=True))
            orphan_snaps.append(str(snap["id"]))

    client: MongoClient = MongoClient(mongo_uri())
    try:
        target = client[target_db_name(ns)]["documents"]
        quarantine = client[quarantine_db_name(ns)]["documents_quarantine"]
        for i in range(0, len(doc_ops), BATCH_SIZE):  # per-batch trigger
            target.bulk_write(doc_ops[i:i + BATCH_SIZE], ordered=False)
        for i in range(0, len(quarantine_ops), BATCH_SIZE):
            quarantine.bulk_write(quarantine_ops[i:i + BATCH_SIZE], ordered=False)
    finally:
        client.close()

    log(f"migrated {len(doc_ops)} documents with {embedded_versions} embedded "
        f"versions into {target_db_name(ns)}.documents")
    log(f"quarantined {len(quarantine_ops)} records into "
        f"{quarantine_db_name(ns)}.documents_quarantine")
    log(f"version gaps detected (reported, not repaired) on {len(gap_docs)} "
        f"documents: {sorted(gap_docs)}")
    log(f"orphaned snapshots quarantined: {sorted(orphan_snaps)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
