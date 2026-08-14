"""Shared helpers for the MongoDB Atlas migration tooling.

Reuses the seed generators' helpers (checksum definition, connection config,
manifest loader) from testdata/legacy so the recon report speaks the exact
same checksum language as the seed manifest.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "testdata" / "legacy"))

from legacy_common import (  # noqa: E402,F401
    DYNAMO_TABLE,
    Checksum,
    aws_resource,
    load_manifest,
    pg_config,
    rng_for,
    schema_name,
    valid_ns,
)

# One database per namespace; never touch anything outside ow_tp_<ns>.
DOCUMENTS_COLLECTION = "documents"
SNAPSHOTS_COLLECTION = "document_snapshots"
FILES_COLLECTION = "files"

BATCH = 1_000  # bulk-write / cursor batch size (M0-friendly)


def db_name(ns: str) -> str:
    return f"ow_tp_{ns}"


def mongo_client():
    from pymongo import MongoClient

    uri = os.getenv("MONGODB_ATLAS_URI")
    if not uri:
        print("MONGODB_ATLAS_URI is not set", file=sys.stderr)
        raise SystemExit(2)
    return MongoClient(uri, serverSelectionTimeoutMS=20_000)


def log(prefix: str, msg: str) -> None:
    print(f"[{prefix}] {msg}", flush=True)
