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
import re
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

# Fan-out expectations, measured against a live Oracle seed. Lines are assigned
# to a uniformly random header, so the per-invoice count is Poisson-like (min 0)
# rather than the 3-25 the contract text assumed. The empty/thin tail is a
# function of the row counts (from `--scale`) and the RNG seed (from the
# namespace), so it is keyed by (ns, scale) — both read from the manifest, never
# global. A header with no lines is still migrated (`lines: []` /
# `lineCount: 0`): it is not an anomaly and is never quarantined. An unmeasured
# (ns, scale) gets its fan-out reported but not asserted, so a healthy run of a
# differently sized estate cannot fail spuriously.
FANOUT_EXPECTED = {
    ("demo", "demo"): {"zeroLineInvoices": 5, "thinInvoices": 268},
}
THIN_FANOUT_THRESHOLD = 3

# `lineId` is a hex uuid, so the id space partitions cleanly by hex prefix.
# Sorting all 150k lines in one server-side $sort blows the aggregation memory
# limit (an M0 allows no external sort), so the ordered stream is assembled
# from lexicographic buckets instead: each bucket is matched through the
# `{"lines.lineId": 1}` index, sorted on its own few hundred lines, and the
# buckets are visited in ascending prefix order — which is exactly ascending
# `lineId` order overall, at bounded memory on both ends.
PREFIX_WIDTH = 2

# The seeder plants each orphan with a ghost invoice number `<NS>-GHOST-<index>`
# and derives both ids from that index, so a quarantined row's identity can be
# re-derived independently of the collection it was read from.
GHOST_INVOICE_NO = re.compile(r"^(?P<ns>[A-Z0-9_]+)-GHOST-(?P<index>\d+)$")
QUARANTINE_MISSING_HEADER = "missing_header"


def seeded_uuid(value: str) -> str:
    """The seeder's `md5_uuid`: md5 hex laid out as a uuid."""
    h = hashlib.md5(value.encode()).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def unplanted_orphans(orphans: list[dict], ns: str) -> list[dict]:
    """Quarantined rows whose ids are not the ones the seeder planted.

    The manifest carries only a *count* of planted orphans, so counting the
    collection twice proves nothing about identity. Each planted orphan is
    `line_id = md5_uuid("<ns>:line:<i>")` pointing at
    `md5_uuid("<ns>:ghost-invoice:<i>")` under invoice number
    `<NS>-GHOST-<i>`; recomputing both ids from that index catches a run that
    quarantined the wrong rows even when it quarantined the right number.
    """
    unplanted = []
    for row in orphans:
        match = GHOST_INVOICE_NO.match(row["invoiceNo"] or "")
        if match is None or match.group("ns") != ns.upper():
            unplanted.append(row | {"why": "invoiceNo is not a planted ghost"})
            continue
        index = int(match.group("index"))
        expected = {"lineId": seeded_uuid(f"{ns}:line:{index}"),
                    "danglingInvoiceId": seeded_uuid(f"{ns}:ghost-invoice:{index}")}
        if any(row[key] != value for key, value in expected.items()):
            unplanted.append(row | {"why": "ids do not match the planted recipe",
                                    "expected": expected})
    return unplanted


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


def manifest_scale(doc: dict) -> str | None:
    """The `--scale` the invoice tables were seeded at, as recorded by the seeder."""
    return doc.get("seed_legacy_params", {}).get(LINE_TARGET, {}).get("scale")


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


def as_decimal(value: Decimal128 | None) -> Decimal | None:
    """Line amounts are `NOT NULL` in the estate, but recon reports rather than crashes."""
    return None if value is None else value.to_decimal()


def amount_str(amount: Decimal | None) -> str:
    return "—" if amount is None else f"{amount:.2f}"


def split_pointers(orphans: list[dict]) -> tuple[list[str], list[str]]:
    """Quarantined lines carrying no `INVOICE_ID`, and the distinct ids the rest point at.

    `INVOICE_LINE.INVOICE_ID` is nullable and such a line is quarantined too: it
    points at nothing, so it is reported on its own rather than sorted in among
    the dangling pointers (where a `None` would abort the whole recon).
    """
    return ([row["lineId"] for row in orphans
             if row["danglingInvoiceId"] is None],
            sorted({row["danglingInvoiceId"] for row in orphans
                    if row["danglingInvoiceId"] is not None}))


def atlas_checksum(db) -> dict:
    """Manifest-shaped ordered md5 over every line Atlas holds."""
    digest = hashlib.md5()
    lines = 0
    previous = None
    total = Decimal("0")
    # a line with no amount cannot contribute a digest term; it is counted and
    # asserted on instead, so the checksum mismatch comes with its explanation
    without_amount = []
    for row in iter_lines(db):
        line_id, amount = row["lineId"], as_decimal(row.get("amount"))
        if previous is not None and line_id < previous:
            raise RuntimeError(f"line stream out of order at {line_id}")
        previous = line_id
        lines += 1
        if amount is None:
            without_amount.append(line_id)
            continue
        digest.update(f"{line_id}:{amount:.2f}\n".encode())
        total += amount
    return {"checksum": digest.hexdigest(), "lines": lines,
            "amountTotal": f"{total:.2f}",
            "linesWithoutAmount": without_amount}


def orphan_ledger(db, ns: str) -> dict:
    """Every quarantined line with the header id it points at, and the proof it dangles."""
    orphans = sorted(
        ({"lineId": doc["lineId"],
          "danglingInvoiceId": doc["raw"].get("INVOICE_ID"),
          "invoiceNo": doc["raw"].get("INVOICE_NO"),
          "amount": amount_str(as_decimal(doc.get("amount"))),
          "quarantineReason": doc["quarantine_reason"]}
         for doc in db[atlas.ORPHANED_LINES].find(
             {}, ["lineId", "amount", "quarantine_reason",
                  "raw.INVOICE_ID", "raw.INVOICE_NO"])),
        key=lambda row: row["lineId"])

    without_pointer, dangling = split_pointers(orphans)
    resolvable = sorted(
        doc["_id"] for doc in db[atlas.INVOICES].find(
            {"_id": {"$in": dangling}}, ["_id"]))
    embedded = sorted(
        doc["_id"] for doc in db[atlas.INVOICES].find(
            {"lines.lineId": {"$in": [row["lineId"] for row in orphans]}}, ["_id"]))
    return {
        "orphans": orphans,
        "orphansWithoutPointer": without_pointer,
        "danglingInvoiceIds": dangling,
        "danglingIdsThatResolve": resolvable,
        "orphanLinesAlsoEmbedded": embedded,
        "unplantedOrphans": unplanted_orphans(orphans, ns),
        "unexpectedQuarantineReasons": sorted(
            {row["quarantineReason"] for row in orphans}
            - {QUARANTINE_MISSING_HEADER}),
    }


def reconcile(db, ns: str) -> dict:
    doc = manifest(ns)
    targets = doc.get("targets", {})
    counts = atlas_counts(db)
    checksum = atlas_checksum(db)
    ledger = orphan_ledger(db, ns)

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
        ("embedded lines == manifest INVOICE_LINE rows minus the planted orphans",
         counts["embeddedLines"],
         expected_lines - expected_orphans
         if None not in (expected_lines, expected_orphans) else None),
        ("checksum stream covers every line",
         checksum["lines"], total_lines),
        ("zero-line invoices carry lines: [] and lineTotal 0.00",
         counts["zeroLineInvoicesWithEmptyArray"], counts["zeroLineInvoices"]),
        ("orphan documents == planted orphaned_rows anomaly",
         counts["orphanedLines"], expected_orphans),
        ("every quarantined row is a planted orphan (ids re-derived, not counted)",
         len(ledger["unplantedOrphans"]), 0),
        ("every quarantined row carries quarantine_reason missing_header",
         len(ledger["unexpectedQuarantineReasons"]), 0),
        ("no dangling INVOICE_ID resolves to a header",
         len(ledger["danglingIdsThatResolve"]), 0),
        ("no orphan line is also embedded in an invoice",
         len(ledger["orphanLinesAlsoEmbedded"]), 0),
        ("every line carries an amount (the checksum covers all of them)",
         len(checksum["linesWithoutAmount"]), 0),
        ("source-parity checksum == manifest checksum",
         checksum["checksum"], expected_checksum),
    ]

    fanout_expected = FANOUT_EXPECTED.get((ns, manifest_scale(doc)))
    if fanout_expected is not None:
        checks += [
            ("invoices with zero lines (migrated, not quarantined)",
             counts["zeroLineInvoices"], fanout_expected["zeroLineInvoices"]),
            (f"invoices with fewer than {THIN_FANOUT_THRESHOLD} lines",
             counts["thinInvoices"], fanout_expected["thinInvoices"]),
        ]

    results = [{"check": name, "actual": actual, "expected": expected,
                "ok": actual == expected}
               for name, actual, expected in checks]
    return {
        "ns": ns,
        "scale": manifest_scale(doc),
        "database": db.name,
        "reconciledAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manifestGeneratedAt": doc.get("generated_at"),
        "counts": counts | {"totalLines": total_lines},
        "checksum": checksum,
        "expected": {"invoices": expected_headers, "lines": expected_lines,
                     "orphans": expected_orphans, "checksum": expected_checksum,
                     "embeddedLines": (expected_lines - expected_orphans
                                      if None not in (expected_lines,
                                                      expected_orphans) else None),
                     "fanout": fanout_expected},
        "checks": results,
        "anomalyLedger": ledger,
        "verdict": "PASS" if all(r["ok"] for r in results) else "FAIL",
    }


def _row(label: str, actual, expected, check: dict | None) -> str:
    """One `## Counts` row, with its verdict taken from the matching check."""
    if check is None:
        verdict = "not asserted"
    else:
        verdict = "ok" if check["ok"] else "MISMATCH"
    return f"| {label} | {actual} | {expected} | {verdict} |"


def as_markdown(report: dict) -> str:
    counts, checksum = report["counts"], report["checksum"]
    ledger = report["anomalyLedger"]
    expected = report["expected"]
    by_check = {check["check"]: check for check in report["checks"]}

    def num(value) -> str:
        return f"{value:,}" if isinstance(value, int) else str(value)

    thin_check = f"invoices with fewer than {THIN_FANOUT_THRESHOLD} lines"
    reasons = sorted({row["quarantineReason"] for row in ledger["orphans"]})
    also_embedded = len(ledger["orphanLinesAlsoEmbedded"])
    unplanted = len(ledger["unplantedOrphans"])
    out = [
        f"# Recon — `mongo-invoices` (NS=`{report['ns']}`)",
        "",
        f"**Verdict: {report['verdict']}** — every number below is recomputed from "
        f"Atlas (`{report['database']}`) and compared against the seed manifest "
        f"`testdata/legacy/manifests/{report['ns']}.json` (SCALE="
        f"`{report['scale']}`, generated "
        f"`{report['manifestGeneratedAt']}`, runtime state, never committed).",
        "",
        f"Reconciled at `{report['reconciledAt']}`.",
        "",
        "## Counts",
        "",
        "| Metric | From Atlas | Expected | |",
        "|---|---|---|---|",
        _row("`invoices` documents", num(counts["invoices"]),
             num(expected["invoices"]),
             by_check.get("invoices documents == manifest INVOICE_HEADER rows")),
        _row("Embedded lines across all invoices", num(counts["embeddedLines"]),
             num(expected["embeddedLines"]),
             by_check.get("embedded lines == manifest INVOICE_LINE rows minus "
                          "the planted orphans")),
        _row("`invoice_lines_orphaned` documents", num(counts["orphanedLines"]),
             num(expected["orphans"]),
             by_check.get("orphan documents == planted orphaned_rows anomaly")),
        _row("Embedded + orphaned lines", num(counts["totalLines"]),
             num(expected["lines"]),
             by_check.get("embedded + orphaned lines == manifest "
                          "INVOICE_LINE rows")),
        _row("Source-parity checksum", f"`{checksum['checksum']}`",
             f"`{expected['checksum']}`",
             by_check.get("source-parity checksum == manifest checksum")),
        _row("Invoices with zero lines", counts["zeroLineInvoices"],
             (expected["fanout"] or {}).get("zeroLineInvoices", "—"),
             by_check.get("invoices with zero lines (migrated, not quarantined)")),
        _row(f"Invoices with fewer than {THIN_FANOUT_THRESHOLD} lines",
             counts["thinInvoices"],
             (expected["fanout"] or {}).get("thinInvoices", "—"),
             by_check.get(thin_check)),
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
        f"Manifest plants **{expected['orphans']}**; Atlas holds "
        f"**{counts['orphanedLines']}** in `invoice_lines_orphaned` with "
        f"quarantine reason(s) {reasons}, {unplanted} of them failing the planted "
        "`<NS>-GHOST-<i>` id recipe, "
        f"{also_embedded} of them also embedded in an invoice, "
        f"{len(ledger['orphansWithoutPointer'])} carrying no `INVOICE_ID` at all, "
        "and "
        f"{len(ledger['danglingIdsThatResolve'])} of the "
        f"{len(ledger['danglingInvoiceIds'])} distinct `INVOICE_ID`s they point "
        "at resolving to a header document.",
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
