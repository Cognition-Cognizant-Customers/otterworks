#!/usr/bin/env python3
"""Recon for ow_tp_finance_report against the golden legacy report.

Compares the converted job's output in `ow_tp.gold.*` with the deterministic output of
`etl/legacy-extra/jobs/finance_excel_report.pl`, per the numbered acceptance checks in
docs/tech-partnerships/contracts/finance_excel_report.md.

The golden side is the legacy artifact itself, regenerated locally with
`make legacy-etl-gen-data NS=demo` + `make legacy-etl-run JOB=...`; it is never derived
from the conversion. Amounts are compared as exact decimals -- no float tolerance.

Usage:
    NS=demo python3 scripts/tp_databricks/recon_finance_report.py [--report-out PATH]

Exit status is 0 only when every check passes; a check that cannot be executed is
reported as BLOCKED with its command and error, and is never counted as a pass.
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dbx  # noqa: E402

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "databricks",
        "notebooks",
    ),
)

import tp_finance_report as pipeline  # noqa: E402

CATALOG = dbx.CATALOG
LEGACY_ROOT = os.environ.get("OTTERWORKS_LEGACY_ROOT", "/tmp/otterworks-legacy")
GRID = [
    ("EUR", "INVOICE"),
    ("EUR", "CREDIT"),
    ("GBP", "INVOICE"),
    ("GBP", "CREDIT"),
    ("USD", "INVOICE"),
    ("USD", "CREDIT"),
]


class Check:
    def __init__(self, number: int, title: str) -> None:
        self.number = number
        self.title = title
        self.result = "PASS"
        self.lines: list[str] = []

    def note(self, line: str) -> None:
        self.lines.append(line)

    def fail(self, line: str) -> None:
        self.result = "FAIL"
        self.lines.append(f"FAIL: {line}")

    def block(self, command: str, error: str, missing: str) -> None:
        self.result = "BLOCKED"
        self.lines.append(f"BLOCKED: command `{command}` failed: {error}")
        self.lines.append(f"missing: {missing}")

    def expect(self, condition: bool, message: str) -> bool:
        if condition:
            self.note(f"ok: {message}")
        else:
            self.fail(message)
        return condition


def golden_report(
    ns: str, report_date: str | None
) -> tuple[str, bytes, list[tuple[str, str, int, Decimal]]]:
    """Locate and parse the legacy report. The golden side of every comparison.

    When a report date is requested the artifact for *that* date is required, so the two
    sides of the comparison can never be different business days.
    """
    stamp = "*" if report_date is None else report_date.replace("-", "")
    pattern = os.path.join(LEGACY_ROOT, "reports", f"finance_billing_{stamp}.csv")
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise SystemExit(
            f"no golden legacy report under {pattern}; regenerate it with "
            "`make legacy-etl-gen-data NS=" + ns + "` then "
            "`make legacy-etl-run JOB=sftp_ingest_poll`, `JOB=parse_custbill_fixedwidth`, "
            "`JOB=finance_excel_report`"
        )
    path = paths[-1]
    with open(path, "rb") as handle:
        raw = handle.read()
    rows = []
    reader = csv.reader(raw.decode("utf-8").splitlines())
    header = next(reader)
    if header != ["Currency", "RecordType", "RecordCount", "TotalAmount"]:
        raise SystemExit(f"unexpected golden header in {path}: {header}")
    for currency, record_type, count, amount in reader:
        rows.append((currency, record_type, int(count), Decimal(amount)))
    return path, raw, rows


def gold_rows(ns: str, report_date: str) -> list[tuple[str, str, int, Decimal]]:
    statement = f"""
        SELECT currency, record_type, record_count, total_amount
        FROM {CATALOG}.gold.finance_billing_summary
        WHERE ns = '{ns}' AND report_date = DATE '{report_date}'
        ORDER BY currency, CASE record_type WHEN 'INVOICE' THEN 0 ELSE 1 END
    """
    return [(r[0], r[1], int(r[2]), Decimal(r[3])) for r in dbx.sql(statement)]


def check_1(ns: str, report_date: str, golden: list[tuple[str, str, int, Decimal]]) -> Check:
    check = Check(1, "Row-level parity with the golden legacy report (exact decimals)")
    rows = gold_rows(ns, report_date)
    check.note(f"golden rows: {len(golden)}, gold table rows: {len(rows)}")
    if not check.expect(len(rows) == 6, f"gold.finance_billing_summary has 6 rows (got {len(rows)})"):
        return check
    check.expect(
        [(c, t) for c, t, _, _ in rows] == GRID,
        f"currency x record_type grid matches {GRID} (got {[(c, t) for c, t, _, _ in rows]})",
    )
    golden_by_key = {(c, t): (n, a) for c, t, n, a in golden}
    for currency, record_type, count, amount in rows:
        want = golden_by_key.get((currency, record_type))
        if want is None:
            check.fail(f"{currency}/{record_type} present in gold, absent from the golden report")
            continue
        want_count, want_amount = want
        check.expect(
            count == want_count and amount == want_amount,
            f"{currency}/{record_type}: count {count} == {want_count}, "
            f"total {amount} == {want_amount}",
        )
    for key in golden_by_key:
        if key not in {(c, t) for c, t, _, _ in rows}:
            check.fail(f"{key[0]}/{key[1]} present in the golden report, absent from gold")
    return check


def check_2(ns: str, report_date: str) -> Check:
    check = Check(2, "Cross-foot: 100 records and gold totals equal silver recomputed")
    total_count = int(
        dbx.sql_scalar(
            f"""SELECT coalesce(sum(record_count), 0)
                FROM {CATALOG}.gold.finance_billing_summary
                WHERE ns = '{ns}' AND report_date = DATE '{report_date}'"""
        )
    )
    check.expect(total_count == 100, f"SUM(record_count) = 100 (got {total_count})")

    silver = {
        (r[0], r[1]): (int(r[2]), Decimal(r[3]))
        for r in dbx.sql(
            f"""SELECT currency,
                       CASE record_type WHEN '01' THEN 'INVOICE' WHEN '02' THEN 'CREDIT' END,
                       count(*), sum(amount)
                FROM {CATALOG}.silver.custbill_records
                WHERE ns = '{ns}' AND record_type IN ('01', '02')
                GROUP BY currency, record_type"""
        )
    }
    for currency, record_type, count, amount in gold_rows(ns, report_date):
        want = silver.get((currency, record_type))
        if want is None:
            check.fail(f"{currency}/{record_type} in gold has no silver counterpart")
            continue
        check.expect(
            (count, amount) == want,
            f"{currency}/{record_type}: gold ({count}, {amount}) == silver {want}",
        )
    silver_total = sum(count for count, _ in silver.values())
    check.expect(silver_total == 100, f"silver detail rows aggregated = 100 (got {silver_total})")
    return check


def check_3(ns: str, report_date: str) -> Check:
    check = Check(3, "Delivery audit row tells the truth about delivery")
    rows = dbx.sql(
        f"""SELECT artifact_path, recipient_list, delivery_status, delivered_at
            FROM {CATALOG}.gold.finance_report_delivery
            WHERE ns = '{ns}' AND report_date = DATE '{report_date}'"""
    )
    if not check.expect(len(rows) == 1, f"exactly one delivery row for the run (got {len(rows)})"):
        return check
    artifact_path, recipients, status, delivered_at = rows[0]
    check.note(f"status={status} recipients={recipients} artifact={artifact_path}")
    check.expect(
        status in (
            pipeline.STATUS_DELIVERED,
            pipeline.STATUS_NO_TRANSPORT,
            pipeline.STATUS_NO_RECIPIENTS,
        ),
        f"delivery_status is a known value (got {status!r})",
    )
    if status == pipeline.STATUS_DELIVERED:
        check.expect(
            delivered_at is not None,
            "DELIVERED rows carry a delivered_at timestamp",
        )
    else:
        check.expect(
            delivered_at is None,
            f"non-delivery is not stamped as delivered (delivered_at={delivered_at!r})",
        )
        check.expect(
            status.startswith("NOT_DELIVERED"),
            "the sendmail no-op is recorded as an explicit non-delivery, not as success",
        )
    if status == pipeline.STATUS_NO_RECIPIENTS:
        check.expect(not recipients, "a run with no configured distribution list records none")
    else:
        check.expect(
            bool(recipients), "recipient list resolved from the secret scope, not from code"
        )
    return check


def check_4(ns: str, report_date: str, golden: list[tuple[str, str, int, Decimal]]) -> Check:
    check = Check(4, "Emitted artifact is a valid file of its extension")
    directory = f"/Volumes/{CATALOG}/bronze/landing/{ns}/reports"
    statement = f"LIST '{directory}'"
    try:
        listing = dbx.sql(statement)
    except Exception as error:  # noqa: BLE001 - reported, never swallowed
        check.block(statement, str(error), f"read access to {directory}")
        return check
    names = [row[1] for row in listing]
    check.note(f"{directory}: {names or '<empty>'}")
    check.expect(
        not [n for n in names if n.lower().endswith((".xls", ".xlsx"))],
        "no .xls artifact (the legacy CSV-renamed-.xls defect is gone)",
    )
    expected = f"finance_billing_{report_date.replace('-', '')}.csv"
    if not names:
        check.note("no artifact emitted; the contract makes the file optional")
        return check
    if not check.expect(expected in names, f"{expected} present"):
        return check
    read = f"SELECT value FROM text.`{directory}/{expected}`"
    try:
        content = [row[0] for row in dbx.sql(read)]
    except Exception as error:  # noqa: BLE001
        check.block(read, str(error), f"read access to {directory}/{expected}")
        return check
    parsed = list(csv.reader(content))
    if not check.expect(bool(parsed), f"{expected} is not empty (got {len(parsed)} lines)"):
        return check
    check.expect(
        parsed[0] == ["Currency", "RecordType", "RecordCount", "TotalAmount"],
        f"CSV header well formed (got {parsed[0]})",
    )
    check.expect(
        all(len(row) == 4 for row in parsed),
        "every CSV line has 4 fields (parses as CSV, is not a renamed foreign format)",
    )
    body = [(r[0], r[1], int(r[2]), Decimal(r[3])) for r in parsed[1:]]
    check.expect(
        body == golden,
        f"artifact body equals the golden report rows (got {body})",
    )
    return check


def check_5(ns: str, report_date: str) -> Check:
    check = Check(5, "Idempotency: re-running replaces gold rows instead of duplicating")
    before = gold_rows(ns, report_date)
    delivery_before = dbx.sql_scalar(
        f"""SELECT count(*) FROM {CATALOG}.gold.finance_report_delivery
            WHERE ns = '{ns}' AND report_date = DATE '{report_date}'"""
    )
    statements = pipeline.summary_statements(ns, report_date, CATALOG)
    check.note(
        f"re-executing the job's {len(statements)} summary statements against the "
        "serverless warehouse"
    )
    for statement in statements:
        result = dbx.sql(statement)
        check.note(f"`{' '.join(statement.split())[:60]}...` -> {result}")
    after = gold_rows(ns, report_date)
    delivery_after = dbx.sql_scalar(
        f"""SELECT count(*) FROM {CATALOG}.gold.finance_report_delivery
            WHERE ns = '{ns}' AND report_date = DATE '{report_date}'"""
    )
    check.expect(len(after) == 6, f"still 6 gold rows after the re-run (got {len(after)})")
    check.expect(after == before, "counts and totals unchanged by the re-run")
    check.expect(
        int(delivery_after) == int(delivery_before) == 1,
        f"still one delivery row (before {delivery_before}, after {delivery_after})",
    )
    return check


def render_report(
    ns: str,
    report_date: str,
    golden_path: str,
    golden_raw: bytes,
    golden: list[tuple[str, str, int, Decimal]],
    converted: list[tuple[str, str, int, Decimal]],
    checks: list[Check],
    verdict: str,
) -> str:
    digest = hashlib.sha256(golden_raw).hexdigest()
    out = [
        "# Recon: `ow_tp_finance_report` vs `finance_excel_report.pl`",
        "",
        f"- verdict: **{verdict}**",
        f"- namespace: `{ns}`, report_date: `{report_date}`",
        f"- golden legacy artifact: `{golden_path}` ({len(golden_raw)} bytes, "
        f"sha256 `{digest}`)",
        f"- converted output: `{CATALOG}.gold.finance_billing_summary`, "
        f"`{CATALOG}.gold.finance_report_delivery`",
        "- both sides produced independently: the golden side is the legacy Perl job's own"
        " output, regenerated with the `legacy-etl-*` targets; the converted side is the"
        " job's statement set executed on the serverless SQL warehouse.",
        "",
        "## Compared values",
        "",
        "| Currency | RecordType | Legacy count | Gold count | Legacy total | Gold total |",
        "|---|---|---:|---:|---:|---:|",
    ]
    gold = {(c, t): (n, a) for c, t, n, a in converted}
    for currency, record_type, count, amount in golden:
        got = gold.get((currency, record_type), ("-", "-"))
        out.append(
            f"| {currency} | {record_type} | {count} | {got[0]} | {amount} | {got[1]} |"
        )
    out += ["", "## Checks", ""]
    for check in checks:
        out.append(f"### {check.number}. {check.title} — **{check.result}**")
        out.append("")
        out += [f"- {line}" for line in check.lines]
        out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default=os.environ.get("NS", "demo"))
    parser.add_argument("--report-date", default=None)
    parser.add_argument("--report-out", default=None)
    args = parser.parse_args()
    ns = args.ns

    golden_path, golden_raw, golden = golden_report(ns, args.report_date)
    stem = os.path.basename(golden_path).replace("finance_billing_", "").replace(".csv", "")
    report_date = args.report_date or f"{stem[0:4]}-{stem[4:6]}-{stem[6:8]}"

    converted = gold_rows(ns, report_date)
    checks = [
        check_1(ns, report_date, golden),
        check_2(ns, report_date),
        check_3(ns, report_date),
        check_4(ns, report_date, golden),
        check_5(ns, report_date),
    ]
    results = {check.result for check in checks}
    verdict = "green" if results == {"PASS"} else ("blocked" if "BLOCKED" in results else "partial")

    report = render_report(
        ns, report_date, golden_path, golden_raw, golden, converted, checks, verdict
    )
    print(report)
    if args.report_out:
        with open(args.report_out, "w", encoding="utf-8") as handle:
            handle.write(report + "\n")
        print(f"\nwrote {args.report_out}", file=sys.stderr)
    return 0 if verdict == "green" else 1


if __name__ == "__main__":
    sys.exit(main())
