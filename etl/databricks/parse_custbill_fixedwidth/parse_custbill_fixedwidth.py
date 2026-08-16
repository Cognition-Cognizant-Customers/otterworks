# Databricks notebook source
"""ow_tp_parse_custbill_fixedwidth — CUSTBILL fixed-width parser (silver layer).

Converts etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh into a medallion
job. Reads CUSTBILL*.dat drops from the bronze landing volume as raw bytes,
slices fields on the byte offsets of copybook CBCUST01, validates strictly,
and loads typed rows into ow_tp.silver.custbill_records. Records or whole
files failing validation are quarantined to ow_tp.silver.custbill_rescue —
nothing is silently dropped and nothing invalid reaches custbill_records.

Copybook CBCUST01 (byte offsets, 1-based inclusive):
  1-10  CUST-ID    PIC X(10)
 11-40  CUST-NAME  PIC X(30)
 41-48  BILL-DATE  PIC 9(8)  YYYYMMDD
 49-60  BILL-AMT   PIC 9(10)V99 (implied decimal)
 61-63  CURRENCY   PIC X(3)
 64-65  REC-TYPE   PIC X(2)  (01=invoice 02=credit)

The parsing core below is pure Python (no Spark imports) so the recon script
can exercise it locally against the deterministic legacy fixture; the Spark
driver runs only when executed as a Databricks notebook.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

CATALOG = "ow_tp"
UNIT = "parse_custbill_fixedwidth"
RECORD_LEN = 65
CCY_RE = re.compile(r"^[A-Z]{3}$")
RECTYPE_RE = re.compile(r"^[0-9]{2}$")


@dataclass
class ParsedRecord:
    line_number: int
    account_id: str
    customer_name: str
    billing_date: date
    amount: Decimal
    currency: str
    record_type: str


@dataclass
class RescueRecord:
    line_number: int
    raw_line: str
    reject_reason: str


@dataclass
class FileResult:
    file_name: str
    records: list = field(default_factory=list)
    rescues: list = field(default_factory=list)
    body_count: int = 0
    trailer_count: int = None
    file_rejected: bool = False


def _printable_ascii(bs: bytes) -> bool:
    return all(0x20 <= b <= 0x7E for b in bs)


def _decode_field(raw: bytes, name: str):
    """Return (text, reject_reason). Bytes outside printable ASCII fail the field."""
    if not _printable_ascii(raw):
        return None, f"non_ascii_bytes:{name}"
    return raw.decode("ascii"), None


def parse_line(raw: bytes, line_number: int):
    """Parse one fixed-width body line. Returns (ParsedRecord, None) or (None, reason)."""
    if len(raw) != RECORD_LEN:
        return None, f"malformed_length:expected={RECORD_LEN},got={len(raw)}"
    fields = {}
    for name, lo, hi in (
        ("account_id", 0, 10),
        ("customer_name", 10, 40),
        ("billing_date", 40, 48),
        ("amount", 48, 60),
        ("currency", 60, 63),
        ("record_type", 63, 65),
    ):
        text, reason = _decode_field(raw[lo:hi], name)
        if reason:
            return None, reason
        fields[name] = text

    account_id = fields["account_id"].rstrip(" ")
    if not account_id:
        return None, "blank_mandatory_field:account_id"
    customer_name = fields["customer_name"].rstrip(" ")

    ds = fields["billing_date"]
    if not ds.isdigit():
        return None, f"invalid_date:not_numeric:{ds!r}"
    try:
        billing_date = date(int(ds[0:4]), int(ds[4:6]), int(ds[6:8]))
    except ValueError:
        return None, f"invalid_date:not_a_calendar_date:{ds}"

    amt = fields["amount"]
    if not amt.isdigit():
        return None, f"malformed_amount:not_numeric:{amt!r}"
    amount = Decimal(int(amt)).scaleb(-2).quantize(Decimal("0.01"))

    currency = fields["currency"].rstrip(" ")
    if not CCY_RE.match(currency):
        return None, f"invalid_currency:{fields['currency']!r}"

    record_type = fields["record_type"]
    if not RECTYPE_RE.match(record_type):
        return None, f"invalid_record_type:{record_type!r}"

    return (
        ParsedRecord(
            line_number=line_number,
            account_id=account_id,
            customer_name=customer_name,
            billing_date=billing_date,
            amount=amount,
            currency=currency,
            record_type=record_type,
        ),
        None,
    )


def _parse_trailer_count(raw: bytes):
    digits = raw[3:13]
    if not digits.isdigit():
        return None
    return int(digits)


def parse_file(file_name: str, data: bytes) -> FileResult:
    """Parse a whole CUSTBILL file. Enforces trailer-count reconciliation:
    a file whose body-record count disagrees with its trailer count is
    rejected whole to rescue (zero rows reach custbill_records)."""
    result = FileResult(file_name=file_name)
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]

    body = []
    trailers = []
    for idx, raw in enumerate(lines, start=1):
        if raw.startswith(b"HDR"):
            continue
        if raw.startswith(b"TRL"):
            trailers.append((idx, raw))
            continue
        body.append((idx, raw))
    result.body_count = len(body)

    def reject_whole_file(reason: str):
        result.file_rejected = True
        result.records = []
        result.rescues = [
            RescueRecord(idx, raw.decode("ascii", errors="replace"), reason)
            for idx, raw in body
        ] or [RescueRecord(0, "", reason)]

    if len(trailers) != 1:
        reject_whole_file(f"trailer_error:expected_1_trailer,got={len(trailers)}")
        return result
    trailer_count = _parse_trailer_count(trailers[0][1])
    result.trailer_count = trailer_count
    if trailer_count is None:
        reject_whole_file("trailer_error:unparseable_count")
        return result
    if trailer_count != len(body):
        reject_whole_file(
            f"trailer_mismatch:trailer={trailer_count},parsed={len(body)}"
        )
        return result

    for idx, raw in body:
        rec, reason = parse_line(raw, idx)
        if reason:
            result.rescues.append(
                RescueRecord(idx, raw.decode("ascii", errors="replace"), reason)
            )
        else:
            result.records.append(rec)
    return result


def legacy_psv_line(rec: ParsedRecord) -> str:
    """Render a record exactly as the legacy awk chain does (parity checks)."""
    return "|".join(
        [
            rec.account_id,
            rec.customer_name,
            rec.billing_date.isoformat(),
            f"{rec.amount:.2f}",
            rec.currency,
            rec.record_type,
        ]
    )


def select_pending(names) -> list:
    """Pending-file selection used by the driver: unprocessed CUSTBILL drops
    only. An empty selection means the run is a no-op and silver is left
    untouched (legacy: parser exits quietly when incoming/ is empty)."""
    return sorted(
        n for n in names if n.startswith("CUSTBILL") and n.endswith(".dat")
    )


def apply_to_state(state: dict, ns: str, result: FileResult) -> None:
    """Delete-then-insert per (ns, file_name): the idempotent write the Spark
    driver performs with DELETE + append. Used by the recon fixture to prove
    rerun idempotency without a live warehouse."""
    records = state.setdefault("custbill_records", {})
    rescues = state.setdefault("custbill_rescue", {})
    records[(ns, result.file_name)] = list(result.records)
    rescues[(ns, result.file_name)] = list(result.rescues)


# COMMAND ----------


def run_pipeline(spark, dbutils) -> None:
    from pyspark.sql.types import (
        DateType,
        DecimalType,
        IntegerType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    dbutils.widgets.text("ns", "demo")
    ns = dbutils.widgets.get("ns")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", ns):
        raise ValueError(f"invalid ns parameter: {ns!r}")

    landing = f"/Volumes/{CATALOG}/bronze/landing/{ns}/{UNIT}"
    incoming = f"{landing}/incoming"
    archive = f"{landing}/archive"

    records_table = f"{CATALOG}.silver.custbill_records"
    rescue_table = f"{CATALOG}.silver.custbill_rescue"

    spark.sql(
        f"""CREATE TABLE IF NOT EXISTS {records_table} (
            ns STRING NOT NULL,
            file_name STRING NOT NULL,
            line_number INT,
            account_id STRING NOT NULL,
            customer_name STRING,
            billing_date DATE NOT NULL,
            amount DECIMAL(12,2) NOT NULL,
            currency STRING NOT NULL,
            record_type STRING NOT NULL,
            ingested_at TIMESTAMP
        )"""
    )
    spark.sql(
        f"""CREATE TABLE IF NOT EXISTS {rescue_table} (
            ns STRING NOT NULL,
            file_name STRING NOT NULL,
            line_number INT,
            raw_line STRING,
            reject_reason STRING NOT NULL,
            ingested_at TIMESTAMP
        )"""
    )

    try:
        entries = dbutils.fs.ls(incoming)
    except Exception as e:
        if "java.io.FileNotFoundException" in str(e) or "NOT_FOUND" in str(e):
            # Empty-input semantics: no incoming directory means nothing to do.
            # Prior silver output is left untouched.
            print(f"no incoming directory at {incoming}; exiting (no-op)")
            return
        raise

    pending = select_pending(e.name for e in entries)
    if not pending:
        print(f"no unprocessed CUSTBILL*.dat files in {incoming}; exiting (no-op)")
        return

    records_schema = StructType(
        [
            StructField("ns", StringType(), False),
            StructField("file_name", StringType(), False),
            StructField("line_number", IntegerType(), True),
            StructField("account_id", StringType(), False),
            StructField("customer_name", StringType(), True),
            StructField("billing_date", DateType(), False),
            StructField("amount", DecimalType(12, 2), False),
            StructField("currency", StringType(), False),
            StructField("record_type", StringType(), False),
            StructField("ingested_at", TimestampType(), True),
        ]
    )
    rescue_schema = StructType(
        [
            StructField("ns", StringType(), False),
            StructField("file_name", StringType(), False),
            StructField("line_number", IntegerType(), True),
            StructField("raw_line", StringType(), True),
            StructField("reject_reason", StringType(), False),
            StructField("ingested_at", TimestampType(), True),
        ]
    )

    for file_name in pending:
        with open(f"{incoming}/{file_name}", "rb") as f:
            data = f.read()
        result = parse_file(file_name, data)
        now = datetime.now(timezone.utc)

        # Idempotent per-file write: delete any rows from a previous run of
        # this same file, then append. A rerun cannot duplicate rows.
        for table in (records_table, rescue_table):
            spark.sql(
                f"DELETE FROM {table} WHERE ns = :ns AND file_name = :file_name",
                args={"ns": ns, "file_name": file_name},
            )
        if result.records:
            rows = [
                (
                    ns,
                    file_name,
                    r.line_number,
                    r.account_id,
                    r.customer_name,
                    r.billing_date,
                    r.amount,
                    r.currency,
                    r.record_type,
                    now,
                )
                for r in result.records
            ]
            spark.createDataFrame(rows, records_schema).write.mode("append").saveAsTable(records_table)
        if result.rescues:
            rows = [
                (ns, file_name, r.line_number, r.raw_line, r.reject_reason, now)
                for r in result.rescues
            ]
            spark.createDataFrame(rows, rescue_schema).write.mode("append").saveAsTable(rescue_table)

        # Archive only after the write landed; no temp files are ever created,
        # so a failure before this point leaves no orphaned state and the file
        # stays in incoming/ for the (idempotent) rerun.
        dbutils.fs.mkdirs(archive)
        dbutils.fs.mv(f"{incoming}/{file_name}", f"{archive}/{file_name}")
        print(
            f"{file_name}: {len(result.records)} record(s) loaded, "
            f"{len(result.rescues)} rescued"
            + (" (whole file rejected)" if result.file_rejected else "")
        )


if __name__ == "__main__":
    run_pipeline(spark, dbutils)  # noqa: F821
