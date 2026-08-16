"""SQL for the converted CUSTBILL parse job (`ow_tp_parse_custbill`).

Single source of truth for the statements the converted job runs, shared by the
notebook that the Databricks job executes (`databricks/notebooks/
parse_custbill_fixedwidth.py`, which pulls these functions in with `%run`) and
by the local driver / recon scripts that execute the same statement set through
`scripts/tp_databricks/dbx.py` against the serverless SQL warehouse. Nothing
here talks to Databricks itself, so the statements are reviewable and testable
without a workspace.

Layout parsed here is copybook CBCUST01, as sliced by the legacy
`etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh`:

    pos  1-10   CUST-ID    PIC X(10)
    pos 11-40   CUST-NAME  PIC X(30)
    pos 41-48   BILL-DATE  PIC 9(8)    YYYYMMDD
    pos 49-60   BILL-AMT   PIC 9(10)V99  implied decimal
    pos 61-63   CURRENCY   PIC X(3)
    pos 64-65   REC-TYPE   PIC X(2)    01=invoice, 02=credit

Positions are 1-based, matching both the copybook and the legacy `cut -c` calls.
"""

from __future__ import annotations

import os
import re


def validate_catalog(value: str) -> str:
    """Validate the Unity Catalog identifier before interpolating table names."""
    if re.fullmatch(r"ow_tp[a-z0-9_]*", value) is None:
        raise ValueError(f"invalid catalog {value!r}: expected ^ow_tp[a-z0-9_]*$")
    return value


CATALOG = validate_catalog(os.environ.get("OW_TP_CATALOG", "ow_tp"))

RECORD_LENGTH = 65
VALID_RECORD_TYPES = ("01", "02")

# Tables owned by this unit (silver). Bronze is the ingest unit's territory; see
# bronze_bootstrap_ddl().
SILVER_RECORDS = f"{CATALOG}.silver.custbill_records"
SILVER_REJECTS = f"{CATALOG}.silver.custbill_rejects"
SILVER_FILE_RECON = f"{CATALOG}.silver.custbill_file_recon"
STAGING_RECORDS = f"{CATALOG}.silver.custbill_records_staging"
STAGING_REJECTS = f"{CATALOG}.silver.custbill_rejects_staging"
STAGING_FILE_RECON = f"{CATALOG}.silver.custbill_file_recon_staging"

BRONZE_FILES = f"{CATALOG}.bronze.custbill_files"
BRONZE_LINES = f"{CATALOG}.bronze.custbill_lines"


def silver_ddl() -> list[str]:
    """Idempotent DDL for the published and staging silver tables this unit owns.

    Typed columns replace the legacy string surgery: the implied decimal becomes
    a real DECIMAL(18,2) and the YYYYMMDD field a real DATE, so a record that
    cannot be typed is quarantined in custbill_rejects instead of being emitted
    reformatted-but-wrong (the legacy `awk` reformat never checked validity).
    """
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {SILVER_RECORDS} (
          ns STRING COMMENT 'Demo namespace; demo state is per-run and per-namespace.',
          file_name STRING COMMENT 'Bronze source file name, e.g. CUSTBILL_DEMO_001.dat.',
          line_no INT COMMENT '1-based line number in the source file (HDR is line 1).',
          record_type STRING COMMENT '01 = invoice, 02 = credit (copybook REC-TYPE).',
          account_id STRING COMMENT 'Copybook CUST-ID, trailing blanks trimmed.',
          invoice_id STRING COMMENT 'Reserved: copybook CBCUST01 carries no invoice number, so this is always NULL for this feed.',
          currency STRING COMMENT 'ISO currency code (copybook CURRENCY).',
          amount DECIMAL(18,2) COMMENT 'Copybook BILL-AMT with the implied decimal applied numerically (units/100), not by string insertion.',
          bill_date DATE COMMENT 'Copybook BILL-DATE parsed as a real date; invalid dates are rejected, never reformatted through.',
          parsed_at TIMESTAMP COMMENT 'When this run wrote the row.',
          customer_name STRING COMMENT 'Copybook CUST-NAME, trailing blanks trimmed.'
        )
        USING DELTA
        COMMENT 'Typed CUSTBILL detail records, replacing the legacy pipe-delimited .psv produced by three passes of cut/sed/awk.'
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {SILVER_REJECTS} (
          ns STRING COMMENT 'Demo namespace.',
          file_name STRING COMMENT 'Bronze source file name.',
          line_no INT COMMENT '1-based line number in the source file.',
          raw_line STRING COMMENT 'The record exactly as it arrived, for replay after the feed is fixed.',
          reject_reason STRING COMMENT 'Which schema/validity check failed.',
          rejected_at TIMESTAMP COMMENT 'When this run quarantined the row.'
        )
        USING DELTA
        COMMENT 'Quarantine for records failing schema or validity checks. The legacy parser had no validation at all: bad records passed straight through into the finance report. A visible, queryable quarantine is the point, so this table exists even when it is empty.'
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {SILVER_FILE_RECON} (
          ns STRING COMMENT 'Demo namespace.',
          file_name STRING COMMENT 'Bronze source file name.',
          declared_trailer_count BIGINT COMMENT 'Record count declared by the file TRL record.',
          parsed_count BIGINT COMMENT 'Detail records written to silver.custbill_records.',
          rejected_count BIGINT COMMENT 'Detail records quarantined in silver.custbill_rejects.',
          recon_ok BOOLEAN COMMENT 'declared_trailer_count = parsed_count + rejected_count.',
          reconciled_at TIMESTAMP COMMENT 'When this run reconciled the file.'
        )
        USING DELTA
        COMMENT 'Trailer-count reconciliation per file. The legacy job logged the trailer count next to the parsed count and moved on (ETL-0187, requested 2011, never implemented); here a mismatch fails the run.'
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {STAGING_RECORDS} (
          ns STRING,
          file_name STRING,
          line_no INT,
          record_type STRING,
          account_id STRING,
          invoice_id STRING,
          currency STRING,
          amount DECIMAL(18,2),
          bill_date DATE,
          parsed_at TIMESTAMP,
          customer_name STRING
        )
        USING DELTA
        COMMENT 'Namespace-scoped staging for typed CUSTBILL records; published only after trailer reconciliation passes.'
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {STAGING_REJECTS} (
          ns STRING,
          file_name STRING,
          line_no INT,
          raw_line STRING,
          reject_reason STRING,
          rejected_at TIMESTAMP
        )
        USING DELTA
        COMMENT 'Namespace-scoped staging for CUSTBILL quarantine rows.'
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {STAGING_FILE_RECON} (
          ns STRING,
          file_name STRING,
          declared_trailer_count BIGINT,
          parsed_count BIGINT,
          rejected_count BIGINT,
          recon_ok BOOLEAN,
          reconciled_at TIMESTAMP
        )
        USING DELTA
        COMMENT 'Namespace-scoped staging for CUSTBILL trailer reconciliation rows.'
        """,
    ]


def bronze_bootstrap_ddl() -> list[str]:
    """Idempotent DDL for the bronze tables this unit *reads*.

    These tables belong to the `ow_tp_sftp_ingest` unit and are declared in its
    contract (docs/tech-partnerships/contracts/sftp_ingest_poll.md). They are
    created here only IF NOT EXISTS, so that this unit can run before the ingest
    unit has landed; the column set is the ingest contract's, unchanged, and
    nothing here ever drops or rewrites another unit's rows.
    """
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {BRONZE_FILES} (
          ns STRING,
          file_name STRING,
          size_bytes BIGINT,
          sha256 STRING,
          record_count BIGINT,
          ingested_at TIMESTAMP,
          source_path STRING
        )
        USING DELTA
        COMMENT 'Ingest manifest: one row per ingested CUSTBILL file. Replaces the legacy "compare the file size twice, one second apart" settle heuristic with a checksum handshake.'
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {BRONZE_LINES} (
          ns STRING,
          file_name STRING,
          line_no INT,
          raw_line STRING
        )
        USING DELTA
        COMMENT 'Raw CUSTBILL records, untyped and byte-faithful including HDR/TRL. Typing is the parse unit job.'
        """,
    ]


def ddl_statements(include_bronze_bootstrap: bool = False) -> list[str]:
    """Return idempotent DDL, with parent-sanctioned bronze bootstrap opt-in.

    The bronze column set is the `ow_tp_sftp_ingest` contract verbatim. Bronze
    bootstrap is a deliberate fallback for the local loader and gate mode, not
    this unit claiming ownership of the ingest tables.
    """
    statements = list(bronze_bootstrap_ddl()) if include_bronze_bootstrap else []
    return statements + silver_ddl()
