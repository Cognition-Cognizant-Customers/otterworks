#!/usr/bin/env python3
"""Stand up ow_tp.silver.custbill_* from the legacy CUSTBILL drops.

These tables belong to the `ow_tp_parse_custbill` work unit
(docs/tech-partnerships/contracts/parse_custbill_fixedwidth.md), which had not landed
when the finance report unit needed them. This bootstrap performs the same
schema-validated parse of copybook CBCUST01 that the contract specifies -- typed
DECIMAL(18,2) amounts with the implied decimal applied numerically, real DATE parsing,
quarantine for anything failing validation, and trailer-count reconciliation that fails
the run on a mismatch -- so the finance report reads the columns its contract names.
When the parse unit's own job lands, it supersedes this script.

Usage:
    NS=demo python3 scripts/tp_databricks/bootstrap_silver_custbill.py [--source-dir DIR]

The source files are the mainframe drops the legacy chain consumed, taken from the legacy
run root (`incoming/CUSTBILL*.dat.done`). They are landed line-for-line, unmodified, into
`<catalog>.bronze.custbill_raw_lines_bootstrap` over the SQL API: the demo PAT has no
`files` scope, so the landing volume cannot be written from this VM (403 `does not have
required scopes: files`). The real ingest unit lands the files in the volume; the parse
logic below is identical either way, it just reads its raw lines from a bronze table.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dbx  # noqa: E402

CATALOG = dbx.CATALOG
LEGACY_ROOT = os.environ.get("OTTERWORKS_LEGACY_ROOT", "/tmp/otterworks-legacy")


def ddl_statements(catalog: str = CATALOG) -> list[str]:
    """Idempotent DDL. Mirrors databricks/sql/silver_custbill_tables.sql."""
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {catalog}.bronze.custbill_raw_lines_bootstrap (
          ns STRING NOT NULL,
          file_name STRING NOT NULL,
          line_no BIGINT NOT NULL,
          raw_line STRING NOT NULL,
          ingested_at TIMESTAMP NOT NULL
        )
        COMMENT 'Raw CUSTBILL drop lines, byte-for-byte as they arrived. Bootstrap stand-in for the ingest unit bronze landing; drop it once ow_tp_sftp_ingest lands.'
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {catalog}.silver.custbill_records (
          ns STRING NOT NULL,
          file_name STRING NOT NULL,
          line_no BIGINT NOT NULL,
          record_type STRING NOT NULL,
          account_id STRING NOT NULL,
          invoice_id STRING,
          customer_name STRING,
          currency STRING NOT NULL,
          amount DECIMAL(18,2) NOT NULL,
          bill_date DATE NOT NULL,
          parsed_at TIMESTAMP NOT NULL
        )
        COMMENT 'Typed CUSTBILL detail records. Replaces the cut/sed/awk .psv produced by parse_custbill_fixedwidth.sh.'
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {catalog}.silver.custbill_rejects (
          ns STRING NOT NULL,
          file_name STRING NOT NULL,
          line_no BIGINT NOT NULL,
          raw_line STRING,
          reject_reason STRING NOT NULL,
          rejected_at TIMESTAMP NOT NULL
        )
        COMMENT 'Quarantine for records failing schema or validity checks; present even when empty.'
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {catalog}.silver.custbill_file_recon (
          ns STRING NOT NULL,
          file_name STRING NOT NULL,
          declared_trailer_count BIGINT,
          parsed_count BIGINT NOT NULL,
          rejected_count BIGINT NOT NULL,
          recon_ok BOOLEAN NOT NULL,
          reconciled_at TIMESTAMP NOT NULL
        )
        COMMENT 'Per-file trailer reconciliation (ETL-0187, requested 2011, never implemented in the legacy parser).'
        """,
    ]


def _validated_cte(ns: str, catalog: str) -> str:
    """Copybook CBCUST01 sliced by position, with every field validated.

      pos  1-10  CUST-ID    X(10)      pos 49-60  BILL-AMT  9(10)V99 (implied decimal)
      pos 11-40  CUST-NAME  X(30)      pos 61-63  CURRENCY  X(3)
      pos 41-48  BILL-DATE  9(8)       pos 64-65  REC-TYPE  X(2)

    line_no is the physical 1-based line number in the drop file, so HDR is line 1 and
    detail records keep the position they arrived in.
    """
    return f"""
        WITH lines AS (
          SELECT file_name, line_no, raw_line AS line
          FROM {catalog}.bronze.custbill_raw_lines_bootstrap
          WHERE ns = '{ns}' AND length(trim(raw_line)) > 0
        ),
        parsed AS (
          SELECT
            file_name,
            line_no,
            line,
            CASE
              WHEN line LIKE 'HDR%' THEN 'HEADER'
              WHEN line LIKE 'TRL%' THEN 'TRAILER'
              ELSE 'DETAIL'
            END AS rec_kind,
            rtrim(substring(line, 1, 10)) AS account_id,
            rtrim(substring(line, 11, 30)) AS customer_name,
            substring(line, 41, 8) AS bill_date_raw,
            substring(line, 49, 12) AS amount_raw,
            rtrim(substring(line, 61, 3)) AS currency,
            substring(line, 64, 2) AS record_type
          FROM lines
        ),
        validated AS (
          SELECT
            *,
            try_to_date(bill_date_raw, 'yyyyMMdd') AS bill_date,
            CASE
              WHEN rec_kind <> 'DETAIL' THEN NULL
              WHEN length(rtrim(line)) < 65 THEN 'SHORT_RECORD'
              WHEN account_id = '' THEN 'MISSING_CUST_ID'
              WHEN NOT amount_raw RLIKE '^[0-9]{{12}}$' THEN 'NON_NUMERIC_AMOUNT'
              WHEN NOT currency RLIKE '^[A-Z]{{3}}$' THEN 'INVALID_CURRENCY'
              WHEN record_type NOT IN ('01', '02') THEN 'UNKNOWN_RECORD_TYPE'
              WHEN try_to_date(bill_date_raw, 'yyyyMMdd') IS NULL THEN 'INVALID_BILL_DATE'
            END AS reject_reason
          FROM parsed
        )
    """


def parse_statements(ns: str, catalog: str = CATALOG) -> list[str]:
    """Delete-then-insert per namespace: re-running replaces rows instead of duplicating."""
    validated = _validated_cte(ns, catalog)
    return [
        f"DELETE FROM {catalog}.silver.custbill_records WHERE ns = '{ns}'",
        f"DELETE FROM {catalog}.silver.custbill_rejects WHERE ns = '{ns}'",
        f"DELETE FROM {catalog}.silver.custbill_file_recon WHERE ns = '{ns}'",
        f"""
        INSERT INTO {catalog}.silver.custbill_records
          (ns, file_name, line_no, record_type, account_id, invoice_id, customer_name,
           currency, amount, bill_date, parsed_at)
        {validated}
        SELECT
          '{ns}',
          file_name,
          line_no,
          record_type,
          account_id,
          CAST(NULL AS STRING) AS invoice_id,
          customer_name,
          currency,
          CAST(amount_raw AS DECIMAL(20,0)) / 100 AS amount,
          bill_date,
          current_timestamp()
        FROM validated
        WHERE rec_kind = 'DETAIL' AND reject_reason IS NULL
        """,
        f"""
        INSERT INTO {catalog}.silver.custbill_rejects
          (ns, file_name, line_no, raw_line, reject_reason, rejected_at)
        {validated}
        SELECT '{ns}', file_name, line_no, line, reject_reason, current_timestamp()
        FROM validated
        WHERE rec_kind = 'DETAIL' AND reject_reason IS NOT NULL
        """,
        f"""
        INSERT INTO {catalog}.silver.custbill_file_recon
          (ns, file_name, declared_trailer_count, parsed_count, rejected_count, recon_ok, reconciled_at)
        {validated},
        agg AS (
          SELECT
            file_name,
            max(CASE WHEN rec_kind = 'TRAILER' THEN try_cast(substring(line, 4, 10) AS BIGINT) END)
              AS declared_trailer_count,
            count_if(rec_kind = 'DETAIL' AND reject_reason IS NULL) AS parsed_count,
            count_if(rec_kind = 'DETAIL' AND reject_reason IS NOT NULL) AS rejected_count
          FROM validated
          GROUP BY file_name
        )
        SELECT
          '{ns}',
          file_name,
          declared_trailer_count,
          parsed_count,
          rejected_count,
          declared_trailer_count IS NOT NULL
            AND declared_trailer_count = parsed_count + rejected_count AS recon_ok,
          current_timestamp()
        FROM agg
        """,
    ]


def _land_drops(ns: str, source_dir: str, catalog: str = CATALOG) -> list[tuple[str, int]]:
    """Land each drop file line-for-line into bronze, replacing any previous landing."""
    paths = sorted(glob.glob(os.path.join(source_dir, "CUSTBILL*.dat"))) or sorted(
        glob.glob(os.path.join(source_dir, "CUSTBILL*.dat.done"))
    )
    if not paths:
        raise SystemExit(f"no CUSTBILL*.dat[.done] files under {source_dir}")

    dbx.sql(f"DELETE FROM {catalog}.bronze.custbill_raw_lines_bootstrap WHERE ns = '{ns}'")
    landed = []
    for path in paths:
        name = os.path.basename(path)
        if name.endswith(".done"):
            name = name[: -len(".done")]
        with open(path, encoding="utf-8", newline="") as handle:
            lines = handle.read().replace("\r\n", "\n").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        values = ",".join(
            "('{ns}','{name}',{line_no},'{line}',current_timestamp())".format(
                ns=ns, name=name, line_no=index, line=line.replace("'", "''")
            )
            for index, line in enumerate(lines, start=1)
        )
        dbx.sql(
            f"""INSERT INTO {catalog}.bronze.custbill_raw_lines_bootstrap
                (ns, file_name, line_no, raw_line, ingested_at) VALUES {values}"""
        )
        landed.append((name, len(lines)))
    return landed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default=os.environ.get("NS", "demo"))
    parser.add_argument("--source-dir", default=os.path.join(LEGACY_ROOT, "incoming"))
    parser.add_argument("--skip-landing", action="store_true")
    args = parser.parse_args()
    ns = args.ns

    for statement in ddl_statements():
        dbx.sql(statement)

    if not args.skip_landing:
        for name, count in _land_drops(ns, args.source_dir):
            print(f"landed {name}: {count} lines")

    for statement in parse_statements(ns):
        dbx.sql(statement)

    recon = dbx.sql(
        f"""
        SELECT file_name, declared_trailer_count, parsed_count, rejected_count, recon_ok
        FROM {CATALOG}.silver.custbill_file_recon
        WHERE ns = '{ns}' ORDER BY file_name
        """
    )
    for row in recon:
        print("recon: " + "\t".join(map(str, row)))
    bad = [r for r in recon if str(r[4]).lower() != "true"]
    if bad or not recon:
        print("trailer reconciliation failed; silver is not trustworthy", file=sys.stderr)
        return 1

    total = dbx.sql_scalar(
        f"SELECT count(*) FROM {CATALOG}.silver.custbill_records WHERE ns = '{ns}'"
    )
    print(f"silver.custbill_records rows for ns={ns}: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
