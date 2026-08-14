# /// script
# requires-python = ">=3.11"
# dependencies = ["pymongo"]
# ///
"""
Drop the ow_tp_<ns> database from Atlas (demo cleanup). Only ever touches the
one namespaced database — nothing else in the shared cluster.

Usage:
    uv run migrations/mongodb/drop_namespace.py --ns <ns>
"""

import argparse
import sys

from pymongo.errors import OperationFailure

from mongo_common import db_name, log, mongo_client, valid_ns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    args = parser.parse_args()
    if not valid_ns(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2
    client = mongo_client()
    db = client[db_name(args.ns)]
    try:
        client.drop_database(db.name)
        log("mongo-clean", f"dropped database {db.name}")
    except OperationFailure:
        # readWrite-scoped users can drop collections but not databases;
        # dropping every collection removes the database just the same.
        names = db.list_collection_names()
        for coll in names:
            db.drop_collection(coll)
        log("mongo-clean",
            f"dropped {len(names)} collections from {db.name} "
            "(no dropDatabase privilege)")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
