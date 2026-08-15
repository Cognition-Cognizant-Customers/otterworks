#!/usr/bin/env python3
"""Idempotent Atlas setup for the `customers` migration workload.

Creates (if absent) the two collections this workload owns in the target
database and applies their indexes. Re-running is a no-op: collection creation
tolerates both the client-side pre-check error and the server's `NamespaceExists` (48), and `create_indexes` is idempotent for identical
key/option specs.

    make mongo-tp-customers-setup            # apply
    make mongo-tp-customers-setup DRY_RUN=1  # print the plan only

Scope guard: this script only ever touches `customers` and
`customers_quarantine` in `$MONGO_DB` (default `ow_tp_demo`). It never creates,
modifies or drops a cluster, a database user, or any other collection.
"""

import argparse
import sys

from pymongo import IndexModel
from pymongo.errors import CollectionInvalid, OperationFailure

import config

NAMESPACE_EXISTS = 48


def _index_models(specs):
    models = []
    for spec in specs:
        opts = {k: v for k, v in spec.items() if k not in ("name", "keys")}
        models.append(IndexModel(spec["keys"], name=spec["name"], **opts))
    return models


def setup(db, dry_run: bool = False) -> int:
    existing = set(db.list_collection_names())
    for name in (config.CUSTOMERS, config.QUARANTINE):
        specs = config.INDEXES[name]
        if name in existing:
            print(f"[setup] collection {db.name}.{name}: exists")
        elif dry_run:
            print(f"[setup] collection {db.name}.{name}: would create")
        else:
            try:
                db.create_collection(name)
            except CollectionInvalid:
                pass  # lost the client-side pre-check race
            except OperationFailure as exc:
                if exc.code != NAMESPACE_EXISTS:
                    raise
            print(f"[setup] collection {db.name}.{name}: created")

        for spec in specs:
            keys = ", ".join(f"{f}:{d}" for f, d in spec["keys"])
            extra = " unique" if spec.get("unique") else ""
            print(f"[setup]   index {spec['name']} ({keys}){extra}")
        if not dry_run:
            db[name].create_indexes(_index_models(specs))

    if not dry_run:
        for name in (config.CUSTOMERS, config.QUARANTINE):
            have = sorted(db[name].index_information())
            print(f"[setup] {db.name}.{name} indexes: {have}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan without contacting Atlas for writes")
    args = ap.parse_args()

    with config.mongo_client() as client:
        db = client[config.database_name()]
        return setup(db, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
