#!/usr/bin/env python3
"""Run the Oracle -> Atlas `invoices` migration for one namespace.

    MONGODB_ATLAS_URI=... DB_PORT=52521 \
    uv run --no-project --with oracledb==2.5.1 --with pymongo==4.10.1 \
        migrations/mongodb/invoices/migrate.py --ns demo

The batch number scoping the run comes from the seed manifest
(`testdata/legacy/manifests/<ns>.json`), so the migration reads exactly the
namespace the before-state seeded. Reruns are idempotent; verification of the
result is `recon.py`'s job, not this script's.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import atlas
import extract
import load
import transform

MANIFEST_DIR = Path(__file__).resolve().parents[3] / "testdata/legacy/manifests"
LINE_TARGET = "oracle.OW_BILLING.INVOICE_LINE"


def manifest_batch_no(ns: str) -> int:
    path = MANIFEST_DIR / f"{ns}.json"
    if not path.exists():
        raise SystemExit(f"seed manifest not found: {path} (seed the estate first)")
    manifest = json.loads(path.read_text())
    params = manifest.get("seed_legacy_params", {}).get(LINE_TARGET, {})
    batch_no = params.get("batch_no")
    if batch_no is None:
        raise SystemExit(f"{path} has no batch_no for {LINE_TARGET}")
    return int(batch_no)


def migrate(conn, db, ns: str, batch_no: int, batch_size: int) -> dict:
    status_codes = extract.fetch_codes(conn, "INV_STATUS")
    migrated_at = datetime.now(timezone.utc)
    loader = load.Loader(db, batch_size=batch_size)
    findings = transform.Findings()
    invoices = embedded_lines = orphans = 0

    for kind, row, lines in extract.iter_units(conn, batch_no):
        if kind == extract.INVOICE:
            doc, doc_findings = transform.transform_invoice(
                row, lines, status_codes, ns, migrated_at)
            findings.merge(doc_findings)
            loader.add(atlas.INVOICES, doc)
            invoices += 1
            embedded_lines += doc["lineCount"]
        else:
            loader.add(atlas.ORPHANED_LINES,
                       transform.transform_orphan(row, ns, migrated_at))
            orphans += 1
    loader.flush()

    return {
        "ns": ns,
        "batchNo": batch_no,
        "invoices": invoices,
        "embeddedLines": embedded_lines,
        "orphanedLines": orphans,
        "writes": loader.stats,
        "findings": findings.as_dict(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", required=True)
    ap.add_argument("--batch-no", type=int,
                    help="override the batch number taken from the seed manifest")
    ap.add_argument("--batch-size", type=int, default=500,
                    help="documents per bulk_write")
    args = ap.parse_args()

    batch_no = args.batch_no or manifest_batch_no(args.ns)
    db = atlas.database(args.ns)
    conn = extract.connect()
    print(f"[migrate] ns={args.ns} batch={batch_no} target={db.name}")
    try:
        summary = migrate(conn, db, args.ns, batch_no, args.batch_size)
    finally:
        conn.close()

    print(f"[migrate] invoices={summary['invoices']} "
          f"embedded_lines={summary['embeddedLines']} "
          f"orphaned_lines={summary['orphanedLines']}")
    for collection, stats in sorted(summary["writes"].items()):
        print(f"[migrate] {collection}: {stats}")
    for kind, detail in summary["findings"].items():
        print(f"[migrate] finding {kind}: {detail['count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
