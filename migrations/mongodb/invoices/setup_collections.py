#!/usr/bin/env python3
"""Create the `invoices` workload's collections and indexes in Atlas.

Idempotent: re-running creates nothing that already exists and never drops
data. Only the two collections owned by this workload are touched
(`invoices`, `invoice_lines_orphaned` in `ow_tp_<ns>`); the Atlas project and
cluster themselves are owned by the shared Terraform stack and are not
managed here.

    MONGODB_ATLAS_URI=... uv run --no-project --with pymongo==4.10.1 \
        migrations/mongodb/invoices/setup_collections.py --ns demo
"""

import argparse
import sys

import atlas


def ensure_collection(db, name: str) -> None:
    if name in db.list_collection_names():
        print(f"[setup] {db.name}.{name}: exists")
    else:
        db.create_collection(name)
        print(f"[setup] {db.name}.{name}: created")
    created = db[name].create_indexes(atlas.INDEXES[name])
    print(f"[setup] {db.name}.{name}: indexes {sorted(created)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", required=True)
    args = ap.parse_args()

    db = atlas.database(args.ns)
    for name in (atlas.INVOICES, atlas.ORPHANED_LINES):
        ensure_collection(db, name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
