# /// script
# requires-python = ">=3.11"
# dependencies = ["pymongo"]
# ///
"""Workload infra for the `documents` migration: collections and indexes.

Creates (idempotently) the three collections this workload owns in the Atlas
database `ow_tp_demo` and their indexes:

  documents                     {ownerId: 1, updatedAt: -1}, {folderId: 1}, {isDeleted: 1}
  document_snapshots            {documentId: 1}
  document_snapshots_orphaned   {documentId: 1}

Only these collections are touched — never the shared cluster configuration,
never another workload's namespace.

Usage:
    uv run migrations/mongodb/documents/setup_collections.py
    uv run migrations/mongodb/documents/setup_collections.py --drop   # clean slate
"""

import argparse
import sys

from mongo_common import (
    ATLAS_DB,
    COLL_DOCUMENTS,
    COLL_SNAPSHOTS,
    COLL_SNAPSHOTS_ORPHANED,
    OWNED_COLLECTIONS,
    atlas_client,
    atlas_db,
    log,
)

INDEXES: dict[str, tuple[tuple[tuple[str, int], ...], ...]] = {
    COLL_DOCUMENTS: (
        (("ownerId", 1), ("updatedAt", -1)),
        (("folderId", 1),),
        (("isDeleted", 1),),
    ),
    COLL_SNAPSHOTS: ((("documentId", 1),),),
    COLL_SNAPSHOTS_ORPHANED: ((("documentId", 1),),),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--drop",
        action="store_true",
        help="drop this workload's collections before creating them",
    )
    args = parser.parse_args()

    client = atlas_client()
    try:
        db = atlas_db(client)
        existing = set(db.list_collection_names())

        if args.drop:
            for name in OWNED_COLLECTIONS:
                if name in existing:
                    db[name].drop()
                    log(f"dropped {ATLAS_DB}.{name}")
            existing = set(db.list_collection_names())

        for name in OWNED_COLLECTIONS:
            if name in existing:
                log(f"collection {ATLAS_DB}.{name} already present")
            else:
                db.create_collection(name)
                log(f"created collection {ATLAS_DB}.{name}")

        for name, specs in INDEXES.items():
            for keys in specs:
                index_name = db[name].create_index(list(keys))
                log(f"index {ATLAS_DB}.{name}: {index_name}")
    finally:
        client.close()
    log("setup complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
