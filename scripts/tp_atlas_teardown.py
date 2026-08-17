#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re

from pymongo import MongoClient


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ns", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_]+", args.ns):
        raise SystemExit("NS must contain only letters, digits, and underscores")
    uri = os.environ.get("MONGODB_ATLAS_URI")
    if not uri:
        raise SystemExit("MONGODB_ATLAS_URI is required")
    database_name = f"ow_tp_{args.ns.lower()}"
    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    try:
        before = set(client.list_database_names())
        if database_name in before:
            client.drop_database(database_name)
            print(f"dropped MongoDB database {database_name}")
        else:
            print(f"MongoDB database {database_name} was already absent")
        after = set(client.list_database_names())
        if database_name in after:
            print(f"negative verification FAILED: {database_name} is still present")
            return 1
        print(f"negative verification: {database_name} is absent")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
