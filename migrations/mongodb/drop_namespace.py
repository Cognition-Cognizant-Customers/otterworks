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

from mongo_common import db_name, log, mongo_client, valid_ns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    args = parser.parse_args()
    if not valid_ns(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2
    client = mongo_client()
    client.drop_database(db_name(args.ns))
    log("mongo-clean", f"dropped database {db_name(args.ns)}")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
