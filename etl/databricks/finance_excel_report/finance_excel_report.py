# Databricks notebook source
"""ow_tp_finance_excel_report — finance billing summary (gold layer).

Converts etl/legacy-extra/jobs/finance_excel_report.pl into a medallion job.
Reads typed silver rows from ow_tp.silver.custbill_records (never raw files,
never the rescue quarantine), aggregates record counts and totals by
(currency, record_type), loads ow_tp.gold.finance_billing_summary, and writes
a genuine CSV export to the landing volume exports path with a deterministic
name. The legacy CSV-renamed-to-.xls masquerade and the silent sendmail no-op
(with its stale hardcoded recipients) are intentionally NOT migrated: delivery
is the export artifact in the volume plus the manifest printed into the job
run output.

Malformed-record policy (contract): silver rows with NULL currency,
record_type, or amount for the target ns fail the run with attribution —
they indicate an upstream contract breach and must never melt into a
plausible-looking total.

Empty-input semantics (contract): zero silver rows for the ns produces a
header-only export and zero gold rows for that run key; prior run keys'
outputs are untouched.

The aggregation/rendering core below is pure Python (no Spark imports) so the
recon script can exercise it locally against the deterministic legacy
fixture; the Spark driver runs only when executed as a Databricks notebook.
"""

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal

CATALOG = "ow_tp"
UNIT = "finance_excel_report"

RECORD_TYPE_NAMES = {"01": "INVOICE", "02": "CREDIT"}


@dataclass
class SummaryRow:
    currency: str
    record_type: str
    record_count: int
    total_amount: Decimal


def record_type_name(rt: str) -> str:
    """Legacy mapping: 01=INVOICE, 02=CREDIT, anything else UNKNOWN(rt)."""
    return RECORD_TYPE_NAMES.get(rt, f"UNKNOWN({rt})")


def find_attribution_breaches(rows) -> list:
    """Rows are (currency, record_type, amount) tuples from silver. Any NULL
    in an attribution or amount column is a contract breach: return the
    offending row indices (1-based) so the run can fail with attribution."""
    return [
        i
        for i, (ccy, rt, amt) in enumerate(rows, start=1)
        if ccy is None or rt is None or amt is None
    ]


def aggregate(rows) -> list:
    """Aggregate (currency, record_type, amount) silver rows exactly as the
    legacy report does: keyed by (currency, record-type code), sorted by that
    key, totals at cent precision."""
    counts: dict = {}
    totals: dict = {}
    for ccy, rt, amt in rows:
        key = (ccy, rt)
        counts[key] = counts.get(key, 0) + 1
        totals[key] = totals.get(key, Decimal("0.00")) + Decimal(amt)
    return [
        SummaryRow(ccy, record_type_name(rt), counts[(ccy, rt)],
                   totals[(ccy, rt)].quantize(Decimal("0.01")))
        for ccy, rt in sorted(counts)
    ]


def render_csv(summary) -> bytes:
    """Render the export exactly as the legacy report does: UTF-8, LF line
    endings, no BOM, header always present (header-only when empty)."""
    out = "Currency,RecordType,RecordCount,TotalAmount\n"
    for r in summary:
        out += f"{r.currency},{r.record_type},{r.record_count},{r.total_amount:.2f}\n"
    return out.encode("utf-8")


def export_file_name(stamp: str) -> str:
    """Deterministic export name matching the legacy report's."""
    if not re.fullmatch(r"[0-9]{8}", stamp):
        raise ValueError(f"invalid report stamp: {stamp!r}")
    return f"finance_billing_{stamp}.csv"


def apply_to_state(state: dict, ns: str, stamp: str, summary) -> None:
    """Delete-then-insert per (ns, stamp) run key: the idempotent write the
    Spark driver performs with DELETE + append. Used by the recon fixture to
    prove rerun idempotency without a live warehouse. Other run keys are
    never touched."""
    gold = state.setdefault("finance_billing_summary", {})
    gold[(ns, stamp)] = list(summary)


# COMMAND ----------


def run_pipeline(spark, dbutils) -> None:
    from datetime import datetime, timezone

    from pyspark.sql.types import (
        DecimalType,
        IntegerType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    dbutils.widgets.text("ns", "demo")
    dbutils.widgets.text("report_date", "")
    ns = dbutils.widgets.get("ns")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", ns):
        raise ValueError(f"invalid ns parameter: {ns!r}")
    now = datetime.now(timezone.utc)
    stamp = dbutils.widgets.get("report_date") or now.strftime("%Y%m%d")

    gold_table = f"{CATALOG}.gold.finance_billing_summary"
    records_table = f"{CATALOG}.silver.custbill_records"
    rescue_table = f"{CATALOG}.silver.custbill_rescue"

    spark.sql(
        f"""CREATE TABLE IF NOT EXISTS {gold_table} (
            ns STRING NOT NULL,
            report_date STRING NOT NULL,
            currency STRING NOT NULL,
            record_type STRING NOT NULL,
            record_count INT NOT NULL,
            total_amount DECIMAL(14,2) NOT NULL,
            generated_at TIMESTAMP
        )"""
    )

    # Malformed-record policy: NULL attribution in silver fails the run with
    # attribution before anything is aggregated or written.
    breaches = spark.sql(
        f"""SELECT file_name, line_number FROM {records_table}
            WHERE ns = :ns
              AND (currency IS NULL OR record_type IS NULL OR amount IS NULL)
            ORDER BY file_name, line_number""",
        args={"ns": ns},
    ).collect()
    if breaches:
        attribution = [f"{b.file_name}:{b.line_number}" for b in breaches]
        raise ValueError(
            f"upstream contract breach: {len(breaches)} silver row(s) with NULL "
            f"currency/record_type/amount for ns={ns}: {attribution}"
        )

    silver_rows = [
        (r.currency, r.record_type, r.amount)
        for r in spark.sql(
            f"SELECT currency, record_type, amount FROM {records_table} WHERE ns = :ns",
            args={"ns": ns},
        ).collect()
    ]
    summary = aggregate(silver_rows)

    # Reconciliation: gold record counts plus rescue attribution must account
    # for every silver row (rescue rows are excluded from totals by
    # construction — this job never reads the quarantine into an aggregate).
    rescue_count = spark.sql(
        f"SELECT COUNT(*) AS c FROM {rescue_table} WHERE ns = :ns",
        args={"ns": ns},
    ).collect()[0].c
    aggregated = sum(r.record_count for r in summary)
    if aggregated != len(silver_rows):
        raise ValueError(
            f"reconciliation failure: aggregated {aggregated} != {len(silver_rows)} silver rows"
        )
    print(
        f"reconciliation ns={ns}: {len(silver_rows)} silver record(s) aggregated, "
        f"{rescue_count} rescue row(s) quarantined and excluded from totals"
    )

    # Idempotent per-run-key write: delete any rows from a previous run of
    # this same (ns, report_date), then append. Other run keys untouched.
    spark.sql(
        f"DELETE FROM {gold_table} WHERE ns = :ns AND report_date = :stamp",
        args={"ns": ns, "stamp": stamp},
    )
    if summary:
        schema = StructType(
            [
                StructField("ns", StringType(), False),
                StructField("report_date", StringType(), False),
                StructField("currency", StringType(), False),
                StructField("record_type", StringType(), False),
                StructField("record_count", IntegerType(), False),
                StructField("total_amount", DecimalType(14, 2), False),
                StructField("generated_at", TimestampType(), True),
            ]
        )
        rows = [
            (ns, stamp, r.currency, r.record_type, r.record_count, r.total_amount, now)
            for r in summary
        ]
        spark.createDataFrame(rows, schema).write.mode("append").saveAsTable(gold_table)

    # Genuine CSV artifact to the exports volume path, deterministic name.
    exports = f"/Volumes/{CATALOG}/bronze/landing/{ns}/{UNIT}/exports"
    dbutils.fs.mkdirs(exports)
    csv_bytes = render_csv(summary)
    export_path = f"{exports}/{export_file_name(stamp)}"
    with open(export_path, "wb") as f:
        f.write(csv_bytes)
    with open(export_path, "rb") as f:
        written = f.read()
    if written != csv_bytes:
        raise IOError(f"export verification failed: {export_path}")

    # Observable delivery record: the export manifest is part of the job run
    # output. No sendmail, no hardcoded recipients, no .xls masquerade.
    print(
        f"export delivered: {export_path} "
        f"({len(written)} bytes, sha256={hashlib.sha256(written).hexdigest()})"
    )
    print(
        f"gold rows written for (ns={ns}, report_date={stamp}): {len(summary)}"
        + (" (empty input: header-only export)" if not summary else "")
    )


if __name__ == "__main__":
    run_pipeline(spark, dbutils)  # noqa: F821
