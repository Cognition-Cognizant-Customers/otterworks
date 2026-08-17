#!/usr/bin/env python3
"""Safely bootstrap the dedicated Cron Box Atlas database and collections."""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterable

URI_ENV = "MONGODB_ATLAS_URI"
DATABASE = "ow_tp_cronbox_demo"
COLLECTIONS = ("documents", "files")


def _require_uri() -> str:
    uri = os.environ.get(URI_ENV)
    if not uri:
        raise SystemExit(f"{URI_ENV} is required for an Atlas write")
    return uri


def intended_operations(database: str = DATABASE) -> Iterable[str]:
    yield f"ensure database {database}"
    for collection in COLLECTIONS:
        yield f"ensure collection {database}.{collection}"


def ensure_namespace(uri: str, database: str = DATABASE) -> None:
    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=10_000)
    try:
        db = client[database]
        existing = set(db.list_collection_names())
        for collection in COLLECTIONS:
            if collection not in existing:
                db.create_collection(collection)
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="print operations without connecting"
    )
    mode.add_argument(
        "--apply", action="store_true", help="perform the idempotent namespace write"
    )
    parser.add_argument("--database", default=DATABASE)
    args = parser.parse_args()

    if not args.apply:
        for operation in intended_operations(args.database):
            print(operation)
        return 0

    ensure_namespace(_require_uri(), args.database)
    print(f"ensured database {args.database} and collections: {', '.join(COLLECTIONS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
