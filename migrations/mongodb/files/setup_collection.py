# /// script
# requires-python = ">=3.11"
# dependencies = ["pymongo"]
# ///
"""Create the `files` collection and its indexes in the namespace's Atlas database.

Workload infra for the `files` migration (DynamoDB file metadata -> Atlas). Only
touches the `files` collection of `ow_tp_<ns>`; the Atlas project, cluster and
database user are owned by the shared stack and are never modified here.

Idempotent: re-running creates nothing that already exists and reports the
resulting index set.

Usage:
    MONGODB_ATLAS_URI=... uv run migrations/mongodb/files/setup_collection.py --ns demo
"""

import argparse
import sys

from common import COLLECTION, db_name, log, mongo_client, valid_ns
from pymongo import ASCENDING
from pymongo.errors import CollectionInvalid

# Read patterns this collection serves: per-tenant owner listings, folder
# listings, trash views, and the storage-key lookup used by reconciliation
# (unique because an s3_key identifies exactly one file).
INDEXES = [
    {"name": "tenant_owner", "keys": [("tenant", ASCENDING), ("ownerId", ASCENDING)]},
    {"name": "folder", "keys": [("folderId", ASCENDING)]},
    {"name": "trashed", "keys": [("isTrashed", ASCENDING)]},
    {"name": "storage_s3key_unique", "keys": [("storage.s3Key", ASCENDING)], "unique": True},
]


def setup(ns: str) -> list[str]:
    client = mongo_client()
    db = client[db_name(ns)]
    try:
        db.create_collection(COLLECTION)
        log(f"created collection {db.name}.{COLLECTION}")
    except CollectionInvalid:
        log(f"collection {db.name}.{COLLECTION} already exists")

    collection = db[COLLECTION]
    for index in INDEXES:
        created = collection.create_index(
            index["keys"], name=index["name"], unique=index.get("unique", False)
        )
        log(f"index ready: {created}")

    names = sorted(collection.index_information())
    log(f"indexes on {db.name}.{COLLECTION}: {', '.join(names)}")
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    args = parser.parse_args()

    if not valid_ns(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2

    setup(args.ns)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
