#!/usr/bin/env python3
"""Reconcile `ow_tp_parse_custbill` against the golden legacy `.psv` output.

Runs the five numbered acceptance checks in
`docs/tech-partnerships/contracts/parse_custbill_fixedwidth.md` and writes a
markdown report with the compared values. The golden side is always the legacy
output on disk (produced by `make legacy-etl-run JOB=parse_custbill_fixedwidth`),
never anything this script or the conversion produced: the converted rows are
read from `ow_tp.silver.custbill_records` through
`scripts/tp_databricks/dbx.py`.

Comparisons are exact. Amounts are compared as `Decimal` to the cent, dates as
`datetime.date`, strings byte-for-byte. Any mismatch, extra row, missing row or
unjustified quarantine makes the whole run fail (exit 1) and is listed in the
report -- an honest red is the point of the exercise.

Usage:
    NS=demo python3 scripts/tp_databricks/recon_parse_custbill.py \
        --golden /home/ubuntu/tp-golden/custbill/parsed \
        --report docs/tech-partnerships/recon/parse_custbill_fixedwidth.md
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "databricks" / "notebooks"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import custbill_parse_sql  # noqa: E402
import custbill_sql  # noqa: E402
import dbx  # noqa: E402

# Golden facts quoted by the contract, captured from the legacy run. They are
# assertions about the baseline, not inputs to the comparison: if the local
# regeneration does not reproduce them, the baseline is wrong and the recon is
# meaningless, so check 0 fails loudly instead of comparing against whatever
# happens to be on disk.
GOLDEN_FILES = {
    "CUSTBILL_DEMO_001.psv": (
        2484,
        50,
        "7fc03e8ceb88ce807b18e3e0a8bb2450b7677108495bdcb883881887c09665bf",
    ),
    "CUSTBILL_DEMO_002.psv": (
        2468,
        50,
        "b576ad3de53b835643dc9096781cb491e6a03b3712c675c5598ab05f8c3c54a3",
    ),
}

# (file, record_type, currency) -> (count, amount) exactly as the contract lists.
CONTRACT_SUBTOTALS = {
    ("CUSTBILL_DEMO_001", "01", "EUR"): (12, Decimal("55683.32")),
    ("CUSTBILL_DEMO_001", "01", "GBP"): (16, Decimal("107084.75")),
    ("CUSTBILL_DEMO_001", "01", "USD"): (15, Decimal("70039.36")),
    ("CUSTBILL_DEMO_001", "02", "EUR"): (2, Decimal("12243.83")),
    ("CUSTBILL_DEMO_001", "02", "GBP"): (2, Decimal("9116.73")),
    ("CUSTBILL_DEMO_001", "02", "USD"): (3, Decimal("21160.45")),
    ("CUSTBILL_DEMO_002", "01", "EUR"): (10, Decimal("45871.09")),
    ("CUSTBILL_DEMO_002", "01", "GBP"): (16, Decimal("76028.83")),
    ("CUSTBILL_DEMO_002", "01", "USD"): (13, Decimal("60462.79")),
    ("CUSTBILL_DEMO_002", "02", "EUR"): (4, Decimal("21132.14")),
    ("CUSTBILL_DEMO_002", "02", "GBP"): (3, Decimal("19337.86")),
    ("CUSTBILL_DEMO_002", "02", "USD"): (4, Decimal("12229.99")),
}

# The legacy .psv carries no line number: its Nth data line is the Nth detail
# record of the .dat, which sits on line N + 1 because line 1 is the HDR.
HDR_LINES = 1

FIELDS = ("account_id", "customer_name", "bill_date", "amount", "currency", "record_type")


class Check:
    def __init__(self, number: str, title: str):
        self.number = number
        self.title = title
        self.ok = True
        self.details: list[str] = []
        self.blocked_reason: str | None = None

    def note(self, line: str) -> None:
        self.details.append(line)

    def fail(self, line: str) -> None:
        self.ok = False
        self.details.append(line)

    def blocked(self, command: str, error: str) -> None:
        self.ok = False
        self.blocked_reason = f"`{command}` -> {error}"

    @property
    def status(self) -> str:
        if self.blocked_reason:
            return "BLOCKED"
        return "PASS" if self.ok else "FAIL"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_golden(golden_dir: Path, ns: str) -> dict[tuple[str, int], dict[str, object]]:
    """Parse the legacy pipe-delimited output into keyed, typed rows.

    The legacy writer emits `account|name|YYYY-MM-DD|amount|currency|type`; the
    types here are the *legacy* interpretation, so a difference in typing shows
    up as a field mismatch rather than being normalised away.
    """
    rows: dict[tuple[str, int], dict[str, object]] = {}
    for psv in sorted(golden_dir.glob(f"CUSTBILL_{ns.upper()}_*.psv")):
        stem = psv.stem
        data_lines = [line for line in psv.read_text().splitlines() if line.strip()]
        for index, line in enumerate(data_lines, start=1):
            parts = line.split("|")
            if len(parts) != 6:
                raise ValueError(f"{psv.name}:{index}: expected 6 fields, got {len(parts)}: {line!r}")
            account_id, customer_name, bill_date, amount, currency, record_type = parts
            try:
                typed_bill_date: object = date.fromisoformat(bill_date)
            except ValueError:
                typed_bill_date = bill_date
            try:
                typed_amount: object = Decimal(amount)
            except ArithmeticError:
                typed_amount = amount
            rows[(stem, index + HDR_LINES)] = {
                "account_id": account_id,
                "customer_name": customer_name,
                "bill_date": typed_bill_date,
                "amount": typed_amount,
                "currency": currency,
                "record_type": record_type,
            }
    return rows


def read_converted(
    ns: str,
) -> tuple[dict[tuple[str, int], dict[str, object]], tuple[str, str] | None]:
    """Read the converted rows out of silver, keyed the same way as the golden."""
    ns_literal = custbill_parse_sql.quote_sql_literal(ns)
    statement = f"""
        SELECT file_name, line_no, account_id, customer_name, bill_date, amount, currency, record_type
        FROM {custbill_sql.SILVER_RECORDS}
        WHERE ns = {ns_literal}
        ORDER BY file_name, line_no
    """
    try:
        result = dbx.sql(statement)
    except dbx.DatabricksError as exc:
        return {}, (statement, str(exc))

    rows: dict[tuple[str, int], dict[str, object]] = {}
    for file_name, line_no, account, name, bill_date, amount, currency, rec_type in result:
        stem = file_name[: -len(".dat")] if file_name.endswith(".dat") else file_name
        rows[(stem, int(line_no))] = {
            "account_id": account,
            "customer_name": name,
            "bill_date": date.fromisoformat(bill_date),
            "amount": Decimal(amount),
            "currency": currency,
            "record_type": rec_type,
        }
    return rows, None


def subtotals(rows: dict[tuple[str, int], dict[str, object]]) -> dict[tuple[str, str, str], tuple[int, Decimal]]:
    agg: dict[tuple[str, str, str], tuple[int, Decimal]] = {}
    for (stem, _line_no), row in rows.items():
        if not isinstance(row["amount"], Decimal):
            continue
        key = (stem, str(row["record_type"]), str(row["currency"]))
        count, total = agg.get(key, (0, Decimal("0.00")))
        agg[key] = (count + 1, total + row["amount"])  # type: ignore[operator]
    return agg


def _display_legacy_value(field: str, value: object) -> str:
    if field in {"bill_date", "amount"} and isinstance(value, str):
        return f"unparseable legacy {field} {value!r}"
    return repr(value)


def check_baseline(ns: str, golden_dir: Path) -> Check:
    check = Check("0", "Golden baseline reproduced locally (bytes / data lines / SHA-256)")
    if ns != "demo":
        check.blocked(
            "golden baseline constants",
            "byte/line/SHA constants were captured from ns=demo, not ns=%s" % ns,
        )
        return check
    for name, (want_bytes, want_lines, want_sha) in sorted(GOLDEN_FILES.items()):
        path = golden_dir / name
        if not path.exists():
            check.fail(f"{name}: missing from {golden_dir}")
            continue
        got_bytes = path.stat().st_size
        got_lines = len([line for line in path.read_text().splitlines() if line.strip()])
        got_sha = _sha256(path)
        detail = f"{name}: {got_bytes} bytes / {got_lines} lines / {got_sha}"
        if (got_bytes, got_lines, got_sha) == (want_bytes, want_lines, want_sha):
            check.note(f"{detail} — matches contract")
        else:
            check.fail(
                f"{detail} != contract {want_bytes} bytes / {want_lines} lines / {want_sha}"
            )
    return check


def check_row_parity(golden, converted, converted_error=None) -> Check:
    check = Check("1", "Row-level parity: every field of every row, keyed on (file, line_no)")
    if converted_error:
        check.blocked(*converted_error)
        return check
    check.note(f"golden rows: {len(golden)}; converted rows: {len(converted)}")
    missing = sorted(set(golden) - set(converted))
    extra = sorted(set(converted) - set(golden))
    for key in missing:
        check.fail(f"missing from silver.custbill_records: {key[0]} line {key[1]}")
    for key in extra:
        check.fail(f"present in silver.custbill_records but not in the legacy output: {key[0]} line {key[1]}")
    mismatches = 0
    for key in sorted(set(golden) & set(converted)):
        for field in FIELDS:
            want, got = golden[key][field], converted[key][field]
            if want != got:
                mismatches += 1
                check.fail(
                    f"{key[0]} line {key[1]} {field}: "
                    f"legacy {_display_legacy_value(field, want)} != converted {got!r}"
                )
    if not missing and not extra and not mismatches:
        check.note(f"all {len(golden)} rows match on all {len(FIELDS)} fields")
    return check


def check_subtotals(ns: str, golden, converted, converted_error=None) -> Check:
    check = Check("2", "Per-file subtotals per record type and currency, exact to the cent")
    if converted_error:
        check.blocked(*converted_error)
        return check
    golden_agg = subtotals(golden)
    converted_agg = subtotals(converted)
    keys = set(golden_agg) | set(converted_agg)
    if ns == "demo":
        keys |= set(CONTRACT_SUBTOTALS)
    else:
        check.blocked(
            "contract subtotals",
            "contract subtotal constants were captured from ns=demo, not ns=%s" % ns,
        )
    for key in sorted(keys):
        contract = CONTRACT_SUBTOTALS.get(key) if ns == "demo" else None
        legacy = golden_agg.get(key)
        got = converted_agg.get(key)
        label = f"{key[0]} {key[1]} {key[2]}"
        if legacy == got and (ns != "demo" or contract == legacy):
            check.note(f"{label}: {got[0]} / {got[1]}")
        else:
            check.fail(f"{label}: contract {contract}, legacy {legacy}, converted {got}")
    return check


def check_file_recon(ns: str, expected_files: int) -> Check:
    check = Check("3", "Trailer reconciliation: declared_trailer_count = parsed + rejected, recon_ok")
    ns_literal = custbill_parse_sql.quote_sql_literal(ns)
    statement = f"""
        SELECT file_name, declared_trailer_count, parsed_count, rejected_count, recon_ok
        FROM {custbill_sql.SILVER_FILE_RECON}
        WHERE ns = {ns_literal}
        ORDER BY file_name
        """
    try:
        rows = dbx.sql(statement)
    except dbx.DatabricksError as exc:
        check.blocked(statement, str(exc))
        return check
    if not rows:
        check.fail("silver.custbill_file_recon has no rows for this namespace")
    for file_name, declared, parsed, rejected, recon_ok in rows:
        declared_i, parsed_i, rejected_i = int(declared), int(parsed), int(rejected)
        detail = (
            f"{file_name}: declared {declared_i} = parsed {parsed_i} + rejected {rejected_i}, "
            f"recon_ok={recon_ok}"
        )
        if declared_i == parsed_i + rejected_i and str(recon_ok).lower() == "true":
            check.note(detail)
        else:
            check.fail(detail)
    if len(rows) != expected_files:
        check.fail(f"expected {expected_files} recon rows from namespace-scoped golden files, found {len(rows)}")
    return check


def check_quarantine(ns: str, golden) -> Check:
    check = Check("4", "Quarantine justified: nothing the legacy output contains is rejected")
    exists_statement = f"SHOW TABLES IN {custbill_sql.CATALOG}.silver LIKE 'custbill_rejects'"
    try:
        exists = dbx.sql(exists_statement)
    except dbx.DatabricksError as exc:
        check.blocked(exists_statement, str(exc))
        return check
    if not exists:
        check.fail("silver.custbill_rejects does not exist; the quarantine must be visible even when empty")
        return check

    ns_literal = custbill_parse_sql.quote_sql_literal(ns)
    statement = f"""
        SELECT file_name, line_no, reject_reason, raw_line
        FROM {custbill_sql.SILVER_REJECTS}
        WHERE ns = {ns_literal}
        ORDER BY file_name, line_no
        """
    try:
        rows = dbx.sql(statement)
    except dbx.DatabricksError as exc:
        check.blocked(statement, str(exc))
        return check
    check.note("silver.custbill_rejects exists (present even when empty)")
    check.note(f"quarantined rows for ns={ns}: {len(rows)}")
    for file_name, line_no, reason, raw_line in rows:
        stem = file_name[: -len(".dat")] if file_name.endswith(".dat") else file_name
        key = (stem, int(line_no))
        if key in golden:
            check.fail(
                f"{file_name} line {line_no} was quarantined ({reason}) but the legacy output "
                f"emitted it as {golden[key]!r} — a real difference, not a clean reject"
            )
        else:
            check.note(f"{file_name} line {line_no}: {reason} (absent from the legacy output) raw={raw_line!r}")
    return check


def check_idempotency(ns: str, converted, converted_error=None) -> Check:
    check = Check("5", "Idempotency: re-running the job leaves counts and totals unchanged")
    if converted_error:
        check.blocked(*converted_error)
        return check
    for name, statement in custbill_parse_sql.gate_statements(ns):
        try:
            offending = dbx.sql(statement)
        except dbx.DatabricksError as exc:
            check.blocked(statement, str(exc))
            return check
        if offending:
            preview = "; ".join(" | ".join(map(str, row)) for row in offending[:5])
            check.fail(f"bronze gate '{name}' returned {len(offending)} offending rows: {preview}")
            return check

    before_rows = len(converted)
    before_total = sum(row["amount"] for row in converted.values())
    check.note(f"before re-run: {before_rows} rows, total {before_total}")
    for _name, statement in custbill_parse_sql.parse_statements(ns):
        try:
            dbx.sql(statement)
        except dbx.DatabricksError as exc:
            check.blocked(statement, str(exc))
            return check
    for name, statement in custbill_parse_sql.recon_gate_statements(ns):
        try:
            offending = dbx.sql(statement)
        except dbx.DatabricksError as exc:
            check.blocked(statement, str(exc))
            return check
        if offending:
            check.fail(f"post-rerun gate '{name}' returned {len(offending)} offending rows")
    after, after_error = read_converted(ns)
    if after_error:
        check.blocked(*after_error)
        return check
    after_total = sum(row["amount"] for row in after.values())
    check.note(f"after re-run: {len(after)} rows, total {after_total}")
    if len(after) != before_rows or after_total != before_total:
        check.fail("row count or amount total changed across a re-run")
    differing = [key for key in set(after) | set(converted) if after.get(key) != converted.get(key)]
    if differing:
        check.fail(f"{len(differing)} rows differ after the re-run, e.g. {sorted(differing)[:5]}")
    else:
        check.note("every row identical field-by-field across the re-run")
    return check


def render_report(ns: str, golden_dir: Path, checks: list[Check]) -> str:
    verdict = "green" if all(c.ok for c in checks) else (
        "blocked" if any(c.blocked_reason for c in checks) else "red"
    )
    lines = [
        "# Recon: `parse_custbill_fixedwidth.sh` -> `ow_tp_parse_custbill`",
        "",
        f"- Namespace: `ns={ns}`",
        f"- Golden legacy output: `{golden_dir}` (regenerated with `make legacy-etl-gen-data NS={ns}`,"
        f" `make legacy-etl-run JOB=sftp_ingest_poll`, `make legacy-etl-run JOB=parse_custbill_fixedwidth`)",
        f"- Converted output: `{custbill_sql.SILVER_RECORDS}` / `{custbill_sql.SILVER_REJECTS}`"
        f" / `{custbill_sql.SILVER_FILE_RECON}`",
        "- Reproduce: `NS=%s python3 scripts/tp_databricks/recon_parse_custbill.py`" % ns,
        "- Negative controls (quarantine and trailer gate actually failing a run):"
        " [parse_custbill_negative_controls.md](parse_custbill_negative_controls.md)",
        "",
        f"**Result: {verdict}**",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for check in checks:
        lines.append(f"| {check.number}. {check.title} | **{check.status}** |")
    lines.append("")
    for check in checks:
        lines.append(f"## {check.number}. {check.title} — {check.status}")
        lines.append("")
        if check.blocked_reason:
            lines.append(f"Blocked: {check.blocked_reason}")
        for detail in check.details:
            lines.append(f"- {detail}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default=os.environ.get("NS", "demo"))
    parser.add_argument(
        "--golden",
        default=os.environ.get("GOLDEN_DIR", "/home/ubuntu/tp-golden/custbill/parsed"),
        help="directory holding the legacy .psv output",
    )
    parser.add_argument(
        "--report",
        default="docs/tech-partnerships/recon/parse_custbill_fixedwidth.md",
        help="where to write the markdown report (relative to the repo root)",
    )
    parser.add_argument("--skip-idempotency", action="store_true")
    args = parser.parse_args()
    args.ns = custbill_parse_sql.validate_namespace(args.ns)

    golden_dir = Path(args.golden)
    golden = read_golden(golden_dir, args.ns)
    converted, converted_error = read_converted(args.ns)
    checks = [check_baseline(args.ns, golden_dir)]
    checks.append(check_row_parity(golden, converted, converted_error))
    checks.append(check_subtotals(args.ns, golden, converted, converted_error))
    expected_files = len({stem for stem, _line_no in golden})
    checks.append(check_file_recon(args.ns, expected_files))
    checks.append(check_quarantine(args.ns, golden))
    if not args.skip_idempotency:
        checks.append(check_idempotency(args.ns, converted, converted_error))

    report = render_report(args.ns, golden_dir, checks)
    report_path = REPO_ROOT / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report + "\n")

    for check in checks:
        print(f"{check.status:>7}  {check.number}. {check.title}")
        for detail in check.details:
            print(f"         {detail}")
    print(f"report written to {report_path}")
    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
