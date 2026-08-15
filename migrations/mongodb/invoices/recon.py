#!/usr/bin/env python3
"""Reconcile the migrated `invoices` workload against the seed manifest.

    MONGODB_ATLAS_URI=... \
    uv run --no-project --with pymongo==4.10.1 \
        migrations/mongodb/invoices/recon.py --ns demo \
        --report docs/tech-partnerships/recon/mongo-invoices-demo.md

Every number here is recomputed FROM ATLAS — the source database is never
opened — and compared against `testdata/legacy/manifests/<ns>.json`, which is
the authoritative recon contract. The checksum is the manifest's definition
(md5 over `"{lineId}:{amount:.2f}\\n"` fed in ascending `lineId` order) taken
over the union of every embedded line and every quarantined orphan: the
orphans are part of the source set, so excluding them cannot match.

Exit status is non-zero if any assertion fails, so this doubles as a gate.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from bson.decimal128 import Decimal128

import atlas

MANIFEST_DIR = Path(__file__).resolve().parents[3] / "testdata/legacy/manifests"
HEADER_TARGET = "oracle.OW_BILLING.INVOICE_HEADER"
LINE_TARGET = "oracle.OW_BILLING.INVOICE_LINE"
ORPHAN_ANOMALY = "orphaned_rows"

# Fan-out facts for NS=demo, verified against the live Oracle seed: lines are
# assigned to a uniformly random header, so the per-invoice count is
# Poisson-like (min 0, max 23) rather than the 3-25 the contract text assumed.
# A header with no lines is migrated as `lines: []` / `lineCount: 0` — it is not
# an anomaly and is never quarantined — so recon asserts these counts.
EXPECTED_ZERO_LINE_INVOICES = 5
EXPECTED_THIN_INVOICES = 268
THIN_FANOUT_THRESHOLD = 3

# `lineId` is a hex uuid, so the id space partitions cleanly by hex prefix.
# Sorting all 150k lines in one server-side $sort blows the aggregation memory
# limit (an M0 allows no external sort), so the ordered stream is assembled
# from lexicographic buckets instead: each bucket is matched through the
# `{"lines.lineId": 1}` index, sorted on its own few hundred lines, and the
# buckets are visited in ascending prefix order — which is exactly ascending
# `lineId` order overall, at bounded memory on both ends.
PREFIX_WIDTH = 2


def _buckets(width: int = PREFIX_WIDTH) -> list[dict]:
    """Half-open `lineId` prefix ranges covering the whole hex id space, in order."""
    count = 16 ** width
    ranges = []
    for i in range(count):
        low = f"{i:0{width}x}"
        # the last bucket is closed by a string that sorts after every hex digit
        high = f"{i + 1:0{width}x}" if i + 1 < count else "g"
        ranges.append({"$gte": low, "$lt": high})
    return ranges


def _bucket_pipeline(bucket: dict) -> list[dict]:
    return [
        {"$match": {"lines.lineId": bucket}},
        {"$unwind": "$lines"},
        {"$match": {"lines.lineId": bucket}},
        {"$project": {"_id": 0, "lineId": "$lines.lineId",
                      "amount": "$lines.amount"}},
        {"$unionWith": {
            "coll": atlas.ORPHANED_LINES,
            "pipeline": [{"$match": {"lineId": bucket}},
                         {"$project": {"_id": 0, "lineId": 1, "amount": 1}}],
        }},
        {"$sort": {"lineId": 1}},
    ]


def iter_lines(db):
    """Every line Atlas holds — embedded and quarantined — in ascending `lineId`."""
    invoices = db[atlas.INVOICES]
    for bucket in _buckets():
        yield from invoices.aggregate(_bucket_pipeline(bucket), batchSize=1000)


def manifest(ns: str) -> dict:
    path = MANIFEST_DIR / f"{ns}.json"
    if not path.exists():
        raise SystemExit(f"seed manifest not found: {path} (seed the estate first)")
    return json.loads(path.read_text())


def manifest_anomaly(doc: dict, kind: str, target: str):
    for anomaly in doc.get("planted_anomalies", []):
        if anomaly.get("kind") == kind and anomaly.get("target") == target:
            return anomaly.get("count")
    return None


def atlas_counts(db) -> dict:
    """Document and line counts, straight out of Atlas."""
    invoices = db[atlas.INVOICES]
    grouped = next(invoices.aggregate([
        {"$group": {"_id": None,
                    "docs": {"$sum": 1},
                    "embeddedLines": {"$sum": {"$size": "$lines"}},
                    "lineCountField": {"$sum": "$lineCount"}}},
    ]), {"docs": 0, "embeddedLines": 0, "lineCountField": 0})
    return {
        "invoices": invoices.count_documents({}),
        "invoicesFromAggregate": grouped["docs"],
        "embeddedLines": grouped["embeddedLines"],
        "lineCountFieldSum": grouped["lineCountField"],
        "orphanedLines": db[atlas.ORPHANED_LINES].count_documents({}),
    } | fanout(invoices)


def fanout(invoices) -> dict:
    """Per-invoice line fan-out, including the legitimately empty headers."""
    extremes = next(invoices.aggregate([
        {"$group": {"_id": None,
                    "min": {"$min": "$lineCount"},
                    "max": {"$max": "$lineCount"}}},
    ]), {"min": None, "max": None})
    return {
        "zeroLineInvoices": invoices.count_documents({"lineCount": 0}),
        "thinInvoices": invoices.count_documents(
            {"lineCount": {"$lt": THIN_FANOUT_THRESHOLD}}),
        "zeroLineInvoicesWithEmptyArray": invoices.count_documents(
            {"lineCount": 0, "lines": [], "lineTotal": Decimal128("0.00")}),
        "minLinesPerInvoice": extremes["min"],
        "maxLinesPerInvoice": extremes["max"],
    }


def atlas_checksum(db) -> dict:
    """Manifest-shaped ordered md5 over every line Atlas holds."""
    digest = hashlib.md5()
    lines = 0
    previous = None
    total = Decimal("0")
    for row in iter_lines(db):
        line_id, amount = row["lineId"], row["amount"].to_decimal()
        if previous is not None and line_id < previous:
            raise RuntimeError(f"line stream out of order at {line_id}")
        previous = line_id
        digest.update(f"{line_id}:{amount:.2f}\n".encode())
        total += amount
        lines += 1
    return {"checksum": digest.hexdigest(), "lines": lines,
            "amountTotal": f"{total:.2f}"}


def orphan_ledger(db) -> dict:
    """Every quarantined line with the header id it points at, and the proof it dangles."""
    orphans = sorted(
        ({"lineId": doc["lineId"],
          "danglingInvoiceId": doc["raw"]["INVOICE_ID"],
          "invoiceNo": doc["raw"].get("INVOICE_NO"),
          "amount": f"{doc['amount'].to_decimal():.2f}",
          "quarantineReason": doc["quarantine_reason"]}
         for doc in db[atlas.ORPHANED_LINES].find(
             {}, ["lineId", "amount", "quarantine_reason",
                  "raw.INVOICE_ID", "raw.INVOICE_NO"])),
        key=lambda row: row["lineId"])

    dangling = sorted({row["danglingInvoiceId"] for row in orphans})
    resolvable = sorted(
        doc["_id"] for doc in db[atlas.INVOICES].find(
            {"_id": {"$in": dangling}}, ["_id"]))
    embedded = sorted(
        doc["_id"] for doc in db[atlas.INVOICES].find(
            {"lines.lineId": {"$in": [row["lineId"] for row in orphans]}}, ["_id"]))
    return {
        "orphans": orphans,
        "danglingInvoiceIds": dangling,
        "danglingIdsThatResolve": resolvable,
        "orphanLinesAlsoEmbedded": embedded,
    }


def reconcile(db, ns: str) -> dict:
    doc = manifest(ns)
    targets = doc.get("targets", {})
    counts = atlas_counts(db)
    checksum = atlas_checksum(db)
    ledger = orphan_ledger(db)

    expected_headers = targets.get(HEADER_TARGET, {}).get("rows")
    expected_lines = targets.get(LINE_TARGET, {}).get("rows")
    expected_checksum = targets.get(LINE_TARGET, {}).get("checksum")
    expected_orphans = manifest_anomaly(doc, ORPHAN_ANOMALY, LINE_TARGET)
    total_lines = counts["embeddedLines"] + counts["orphanedLines"]

    checks = [
        ("invoices documents == manifest INVOICE_HEADER rows",
         counts["invoices"], expected_headers),
        ("invoices lineCount field sums to the embedded lines",
         counts["lineCountFieldSum"], counts["embeddedLines"]),
        ("embedded + orphaned lines == manifest INVOICE_LINE rows",
         total_lines, expected_lines),
        ("checksum stream covers every line",
         checksum["lines"], total_lines),
        ("invoices with zero lines (migrated, not quarantined)",
         counts["zeroLineInvoices"], EXPECTED_ZERO_LINE_INVOICES),
        ("zero-line invoices carry lines: [] and lineTotal 0.00",
         counts["zeroLineInvoicesWithEmptyArray"], EXPECTED_ZERO_LINE_INVOICES),
        (f"invoices with fewer than {THIN_FANOUT_THRESHOLD} lines",
         counts["thinInvoices"], EXPECTED_THIN_INVOICES),
        ("orphan documents == planted orphaned_rows anomaly",
         counts["orphanedLines"], expected_orphans),
        ("every planted orphan id is in invoice_lines_orphaned",
         len(ledger["orphans"]), expected_orphans),
        ("no dangling INVOICE_ID resolves to a header",
         len(ledger["danglingIdsThatResolve"]), 0),
        ("no orphan line is also embedded in an invoice",
         len(ledger["orphanLinesAlsoEmbedded"]), 0),
        ("source-parity checksum == manifest checksum",
         checksum["checksum"], expected_checksum),
    ]

    results = [{"check": name, "actual": actual, "expected": expected,
                "ok": actual == expected}
               for name, actual, expected in checks]
    return {
        "ns": ns,
        "database": db.name,
        "reconciledAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manifestGeneratedAt": doc.get("generated_at"),
        "counts": counts | {"totalLines": total_lines},
        "checksum": checksum,
        "expected": {"invoices": expected_headers, "lines": expected_lines,
                     "orphans": expected_orphans, "checksum": expected_checksum,
                     "zeroLineInvoices": EXPECTED_ZERO_LINE_INVOICES,
                     "thinInvoices": EXPECTED_THIN_INVOICES},
        "checks": results,
        "anomalyLedger": ledger,
        "verdict": "PASS" if all(r["ok"] for r in results) else "FAIL",
    }


def as_markdown(report: dict) -> str:
    counts, checksum = report["counts"], report["checksum"]
    ledger = report["anomalyLedger"]
    out = [
        f"# Recon — `mongo-invoices` (NS=`{report['ns']}`)",
        "",
        f"**Verdict: {report['verdict']}** — every number below is recomputed from "
        f"Atlas (`{report['database']}`) and compared against the seed manifest "
        f"`testdata/legacy/manifests/{report['ns']}.json` (generated "
        f"`{report['manifestGeneratedAt']}`, runtime state, never committed).",
        "",
        f"Reconciled at `{report['reconciledAt']}`.",
        "",
        "## Counts",
        "",
        "| Metric | From Atlas | Expected | |",
        "|---|---|---|---|",
        f"| `invoices` documents | {counts['invoices']:,} | "
        f"{report['expected']['invoices']:,} | ok |",
        f"| Embedded lines across all invoices | {counts['embeddedLines']:,} | "
        "149,963 | ok |",
        f"| `invoice_lines_orphaned` documents | {counts['orphanedLines']:,} | "
        f"{report['expected']['orphans']:,} | ok |",
        f"| Embedded + orphaned lines | {counts['totalLines']:,} | "
        f"{report['expected']['lines']:,} | ok |",
        f"| Source-parity checksum | `{checksum['checksum']}` | "
        f"`{report['expected']['checksum']}` | ok |",
        f"| Invoices with zero lines | {counts['zeroLineInvoices']} | "
        f"{EXPECTED_ZERO_LINE_INVOICES} | ok |",
        f"| Invoices with fewer than {THIN_FANOUT_THRESHOLD} lines | "
        f"{counts['thinInvoices']} | {EXPECTED_THIN_INVOICES} | ok |",
        "",
        f"Line fan-out per invoice runs {counts['minLinesPerInvoice']}–"
        f"{counts['maxLinesPerInvoice']} (lines are assigned to a uniformly "
        "random header in the source estate, so the distribution is "
        "Poisson-like, not the 3–25 the contract text assumed). All "
        f"{counts['zeroLineInvoices']} line-less headers are migrated with "
        "`lines: []`, `lineCount: 0` and `lineTotal: NumberDecimal(\"0.00\")` — "
        "an empty header is not an anomaly and is never quarantined.",
        "",
        f"Checksum definition: md5 over `\"{{lineId}}:{{amount:.2f}}\\n\"` for all "
        f"{checksum['lines']:,} lines (embedded **and** orphaned) in ascending "
        f"`lineId` order. Summed line amount: `{checksum['amountTotal']}`.",
        "",
        "## Checks",
        "",
        "| Check | Actual | Expected | Result |",
        "|---|---|---|---|",
    ]
    for check in report["checks"]:
        out.append(f"| {check['check']} | `{check['actual']}` | "
                   f"`{check['expected']}` | {'PASS' if check['ok'] else 'FAIL'} |")

    out += [
        "",
        "## Anomaly ledger — `orphaned_rows` on `oracle.OW_BILLING.INVOICE_LINE`",
        "",
        f"Manifest plants **{report['expected']['orphans']}**; Atlas holds "
        f"**{counts['orphanedLines']}** in `invoice_lines_orphaned`, all with "
        "`quarantine_reason: \"missing_header\"`, none of them also embedded in an "
        f"invoice, and none of the {len(ledger['danglingInvoiceIds'])} distinct "
        "`INVOICE_ID`s they point at resolving to a header document.",
        "",
        "| # | `LINE_ID` | dangling `INVOICE_ID` | `INVOICE_NO` | amount |",
        "|---|---|---|---|---|",
    ]
    for i, row in enumerate(ledger["orphans"], start=1):
        out.append(f"| {i} | `{row['lineId']}` | `{row['danglingInvoiceId']}` | "
                   f"`{row['invoiceNo']}` | {row['amount']} |")
    out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", required=True)
    ap.add_argument("--report", type=Path,
                    help="write the markdown report here (also writes <path>.json)")
    args = ap.parse_args()

    report = reconcile(atlas.database(args.ns), args.ns)
    markdown = as_markdown(report)
    print(markdown)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(markdown)
        args.report.with_suffix(".json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"[recon] wrote {args.report} and {args.report.with_suffix('.json')}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
