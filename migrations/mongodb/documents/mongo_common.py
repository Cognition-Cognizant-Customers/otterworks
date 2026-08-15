"""Shared helpers for the `documents` workload of the MongoDB Atlas migration.

Holds the Atlas connection, the collection names this workload owns, and a
bridge to the legacy seed helpers (`testdata/legacy/legacy_common.py`) so the
checksum definition and the Postgres connection config are never re-implemented
here — the recon must use the same definitions the seed manifest was built with.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LEGACY_DIR = REPO_ROOT / "testdata" / "legacy"
if str(LEGACY_DIR) not in sys.path:
    sys.path.insert(0, str(LEGACY_DIR))

from legacy_common import (  # noqa: E402  (path bridge above must run first)
    Checksum,
    load_manifest,
    pg_config,
    schema_name,
    valid_ns,
)

__all__ = [
    "Checksum",
    "load_manifest",
    "pg_config",
    "schema_name",
    "valid_ns",
    "ATLAS_DB",
    "COLL_DOCUMENTS",
    "COLL_SNAPSHOTS",
    "COLL_SNAPSHOTS_ORPHANED",
    "OWNED_COLLECTIONS",
    "QUARANTINE_MISSING_DOCUMENT",
    "SOURCE_TABLE_DOCUMENTS",
    "SOURCE_TABLE_SNAPSHOTS",
    "atlas_client",
    "atlas_db",
    "log",
]

ATLAS_DB = "ow_tp_demo"

COLL_DOCUMENTS = "documents"
COLL_SNAPSHOTS = "document_snapshots"
COLL_SNAPSHOTS_ORPHANED = "document_snapshots_orphaned"
OWNED_COLLECTIONS = (COLL_DOCUMENTS, COLL_SNAPSHOTS, COLL_SNAPSHOTS_ORPHANED)

QUARANTINE_MISSING_DOCUMENT = "missing_document"

SOURCE_TABLE_DOCUMENTS = "documents"
SOURCE_TABLE_SNAPSHOTS = "document_snapshots"


def log(msg: str) -> None:
    print(f"[mongo-documents] {msg}", flush=True)


def atlas_client(timeout_ms: int = 30_000):
    """Connect to Atlas using MONGODB_ATLAS_URI (credentials never logged)."""
    from pymongo import MongoClient

    uri = os.getenv("MONGODB_ATLAS_URI")
    if not uri:
        raise SystemExit("MONGODB_ATLAS_URI is not set")
    return MongoClient(uri, serverSelectionTimeoutMS=timeout_ms, tz_aware=True)


def atlas_db(client):
    return client[ATLAS_DB]


def source_table(ns: str, table: str) -> str:
    """Fully qualified legacy source table recorded in `_migration.sourceTable`."""
    return f"{schema_name(ns)}.{table}"
