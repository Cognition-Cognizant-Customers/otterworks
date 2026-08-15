#!/usr/bin/env python3
"""Run the `customers` migration: Oracle `CUSTOMER_MASTER` + EAV -> Atlas.

    make mongo-tp-customers-migrate                  # full batch
    make mongo-tp-customers-migrate LIMIT=500        # first 500 customers
    make mongo-tp-customers-migrate DRY_RUN=1        # transform only, no writes

Extract -> transform -> load runs one chunk at a time (`BATCH_SIZE` rows), so
memory stays flat regardless of source size. Loading is idempotent: rerunning
converges on the same documents, and `recon.py` recomputes the numbers from
Atlas afterwards.
"""

import argparse
import sys
from datetime import datetime, timezone

import oracledb

import config
import extract
import load
import transform


def _summary(counters, cust_stats, quar_stats):
    return {
        "customers_read": counters["read"],
        "customers_written": counters["written"],
        "eav_rows_consumed": counters["eav_rows"],
        "attribute_keys_folded": counters["attr_keys"],
        "attribute_conflicts": counters["attr_conflicts"],
        "customers_with_attributes": counters["with_attrs"],
        "quarantined_fields": counters["quarantined"],
        "dirty_dates": counters[transform.KIND_DIRTY_DATE],
        "malformed_csv_lists": counters[transform.KIND_MALFORMED_CSV],
        "customers_upserted": cust_stats.upserted,
        "customers_matched": cust_stats.matched,
        "quarantine_upserted": quar_stats.upserted,
        "quarantine_matched": quar_stats.matched,
    }


def migrate(conn, db, ns: str, batch_no: int, limit=None,
            dry_run: bool = False, size: int = config.BATCH_SIZE) -> dict:
    codes = extract.load_codes(conn)
    migrated_at = datetime.now(timezone.utc)
    cust_stats, quar_stats = load.LoadStats(), load.LoadStats()
    counters = {"read": 0, "written": 0, "eav_rows": 0, "attr_keys": 0,
                "attr_conflicts": 0, "with_attrs": 0, "quarantined": 0,
                transform.KIND_DIRTY_DATE: 0,
                transform.KIND_MALFORMED_CSV: 0}

    for rows, eav_by_cust in extract.iter_customer_batches(conn, batch_no, size):
        if limit is not None:
            rows = rows[:max(0, limit - counters["read"])]
            if not rows:
                break
        docs, quarantine_docs = [], []
        for row in rows:
            result = transform.transform_customer(
                row, eav_by_cust.get(row["CUST_ID"], []), codes, ns, migrated_at)
            docs.append(result.doc)
            quarantine_docs.extend(load.quarantine_docs(
                result.doc, result.quarantine, ns, migrated_at))
            counters["read"] += 1
            counters["eav_rows"] += result.eav_rows_consumed
            counters["attr_keys"] += result.attr_keys_folded
            counters["attr_conflicts"] += result.attr_conflicts
            counters["with_attrs"] += 1 if result.attr_keys_folded else 0
            counters["quarantined"] += len(result.quarantine)
            for entry in result.quarantine:
                counters[entry.kind] += 1

        if not dry_run:
            load.load_batch(db, docs, quarantine_docs, cust_stats, quar_stats)
            counters["written"] += len(docs)
        print(f"[migrate] {counters['read']} customers processed", flush=True)
        if limit is not None and counters["read"] >= limit:
            break

    return _summary(counters, cust_stats, quar_stats)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", default=config.namespace())
    ap.add_argument("--batch-no", type=int, default=config.batch_no())
    ap.add_argument("--batch-size", type=int, default=config.BATCH_SIZE)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true",
                    help="transform everything but write nothing to Atlas")
    args = ap.parse_args()

    conn = oracledb.connect(**config.oracle_dsn())
    client = None if args.dry_run else config.mongo_client()
    db = None if client is None else client[config.database_name()]
    try:
        summary = migrate(conn, db, args.ns, args.batch_no, limit=args.limit,
                          dry_run=args.dry_run, size=args.batch_size)
    finally:
        conn.close()
        if client is not None:
            client.close()

    print(f"[migrate] ns={args.ns} batch={args.batch_no}"
          f"{' (dry run)' if args.dry_run else ''}")
    for key, value in summary.items():
        print(f"[migrate]   {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
