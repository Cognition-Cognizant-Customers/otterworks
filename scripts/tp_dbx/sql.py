#!/usr/bin/env python3
"""SQL for the OtterWorks Databricks history showcase.

One place for every statement so the recon the job runs and the recon the
harness runs are provably the same text, parameterised only by namespace.

Fixed-width layout is copybook CBCUST01 (see
etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh):
  1-10 CUST-ID, 11-40 CUST-NAME, 41-48 BILL-DATE YYYYMMDD,
  49-60 BILL-AMT PIC 9(10)V99 implied decimal, 61-63 CURRENCY, 64-65 REC-TYPE
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Names:
    catalog: str = "ow_tp"
    ns: str = "demo"

    @property
    def landing(self) -> str:
        return f"/Volumes/{self.catalog}/bronze/landing/{self.ns}"

    @property
    def history_dir(self) -> str:
        return f"{self.landing}/history"

    @property
    def bronze(self) -> str:
        return f"{self.catalog}.bronze.custbill_history_raw_{self.ns}"

    @property
    def silver(self) -> str:
        return f"{self.catalog}.silver.custbill_history_{self.ns}"

    @property
    def quarantine(self) -> str:
        return f"{self.catalog}.silver.custbill_quarantine_{self.ns}"

    @property
    def gold(self) -> str:
        return f"{self.catalog}.gold.custbill_annual_{self.ns}"

    @property
    def expectations(self) -> str:
        return f"{self.catalog}.ops.history_expectations_{self.ns}"

    @property
    def recon_runs(self) -> str:
        return f"{self.catalog}.ops.recon_runs_{self.ns}"


def provision(n: Names) -> list[str]:
    return [
        (f"CREATE CATALOG IF NOT EXISTS {n.catalog} "
         "COMMENT 'OtterWorks tech-partnerships migration demo (prefix ow_tp)'"),
        f"CREATE SCHEMA IF NOT EXISTS {n.catalog}.bronze COMMENT 'Raw mainframe CUSTBILL drops as landed'",
        f"CREATE SCHEMA IF NOT EXISTS {n.catalog}.silver COMMENT 'Parsed, validated billing records'",
        f"CREATE SCHEMA IF NOT EXISTS {n.catalog}.gold COMMENT 'Finance-facing billing aggregates'",
        f"CREATE SCHEMA IF NOT EXISTS {n.catalog}.ops COMMENT 'Migration reconciliation and demo operations'",
        (f"CREATE VOLUME IF NOT EXISTS {n.catalog}.bronze.landing "
         "COMMENT 'Landing zone replacing the legacy SFTP drop directory'"),
        f"""CREATE TABLE IF NOT EXISTS {n.bronze} (
              source_file STRING COMMENT 'CUSTBILL extract file name as dropped by the mainframe',
              source_period STRING COMMENT 'YYYYMM billing period the drop covers',
              source_year INT COMMENT 'Calendar year of the drop, used as the load partition',
              record_kind STRING COMMENT 'HDR, TRL or BODY',
              raw_line STRING COMMENT 'Untouched fixed-width record',
              file_modification_time TIMESTAMP COMMENT 'When the drop landed on the volume; the Files API stamps upload time, so this is not the mainframe drop date — that comes from source_period',
              ingested_at TIMESTAMP)
            USING DELTA PARTITIONED BY (source_year)
            COMMENT 'Bronze: byte-preserved CUSTBILL history, one row per line'""",
        f"""CREATE TABLE IF NOT EXISTS {n.silver} (
              cust_id STRING, cust_name STRING, bill_date DATE,
              amount_cents BIGINT COMMENT 'PIC 9(10)V99 implied decimal held as cents',
              currency STRING, record_type STRING COMMENT '01=invoice 02=credit',
              source_file STRING, source_period STRING, source_year INT)
            USING DELTA PARTITIONED BY (source_year)
            COMMENT 'Silver: validated billing records (quarantined rows excluded)'""",
        f"""CREATE TABLE IF NOT EXISTS {n.quarantine} (
              source_file STRING, source_period STRING, source_year INT,
              cust_id STRING, raw_line STRING,
              reason STRING COMMENT 'invalid_calendar_date | nonnumeric_amount | trailer_count_mismatch',
              detected_at TIMESTAMP)
            USING DELTA
            COMMENT 'Silver: records the legacy parser passed through silently'""",
        f"""CREATE TABLE IF NOT EXISTS {n.gold} (
              source_year INT, currency STRING, record_type STRING,
              record_count BIGINT, total_amount_cents BIGINT)
            USING DELTA
            COMMENT 'Gold: annual finance totals, the finance_excel_report replacement'""",
        f"""CREATE TABLE IF NOT EXISTS {n.expectations} (
              source_year INT, currency STRING, record_type STRING,
              record_count BIGINT, total_amount_cents BIGINT,
              quarantine_record_count BIGINT, file_count BIGINT)
            USING DELTA
            COMMENT 'Legacy-derived expectations from gen_history_data.pl (source of truth for recon)'""",
        f"""CREATE TABLE IF NOT EXISTS {n.recon_runs} (
              run_id STRING, checked_at TIMESTAMP, check_id STRING,
              expected STRING, actual STRING, result STRING)
            USING DELTA
            COMMENT 'Every reconciliation check ever run against this namespace'""",
    ]


def _body_projection(n: Names) -> str:
    return f"""
      SELECT
        trim(substr(raw_line, 1, 10)) AS cust_id,
        trim(substr(raw_line, 11, 30)) AS cust_name,
        substr(raw_line, 41, 8) AS bill_date_raw,
        substr(raw_line, 49, 12) AS amount_raw,
        trim(substr(raw_line, 61, 3)) AS currency,
        substr(raw_line, 64, 2) AS record_type,
        source_file, source_period, source_year, raw_line
      FROM {n.bronze}
      WHERE record_kind = 'BODY'"""


def load_bronze(n: Names, path: str, overwrite: bool) -> str:
    verb = "INSERT OVERWRITE" if overwrite else "INSERT INTO"
    return f"""
    {verb} {n.bronze}
    SELECT
      regexp_extract(_metadata.file_path, '([^/]+)$', 1) AS source_file,
      regexp_extract(_metadata.file_path, 'CUSTBILL_[A-Z0-9_-]+_([0-9]{{6}})\\\\.dat$', 1) AS source_period,
      CAST(substr(regexp_extract(_metadata.file_path, 'CUSTBILL_[A-Z0-9_-]+_([0-9]{{6}})\\\\.dat$', 1), 1, 4) AS INT) AS source_year,
      CASE WHEN startswith(value, 'HDR') THEN 'HDR'
           WHEN startswith(value, 'TRL') THEN 'TRL'
           ELSE 'BODY' END AS record_kind,
      value AS raw_line,
      _metadata.file_modification_time AS file_modification_time,
      current_timestamp() AS ingested_at
    FROM read_files('{path}', format => 'text', recursiveFileLookup => true)
    WHERE length(trim(value)) > 0"""


def delete_bronze_period(n: Names, period: str) -> str:
    return f"DELETE FROM {n.bronze} WHERE source_period = '{period}'"


def build_silver(n: Names) -> str:
    return f"""
    INSERT OVERWRITE {n.silver}
    SELECT cust_id, cust_name, to_date(bill_date_raw, 'yyyyMMdd') AS bill_date,
           CAST(amount_raw AS BIGINT) AS amount_cents, currency, record_type,
           source_file, source_period, source_year
    FROM ({_body_projection(n)}) parsed
    WHERE amount_raw RLIKE '^[0-9]{{12}}$'
      AND try_to_date(bill_date_raw, 'yyyyMMdd') IS NOT NULL"""


def build_quarantine(n: Names) -> str:
    return f"""
    INSERT OVERWRITE {n.quarantine}
    WITH parsed AS ({_body_projection(n)}),
    row_defects AS (
      SELECT source_file, source_period, source_year, cust_id, raw_line,
             CASE WHEN NOT amount_raw RLIKE '^[0-9]{{12}}$' THEN 'nonnumeric_amount'
                  ELSE 'invalid_calendar_date' END AS reason
      FROM parsed
      WHERE NOT amount_raw RLIKE '^[0-9]{{12}}$'
         OR try_to_date(bill_date_raw, 'yyyyMMdd') IS NULL
    ),
    trailer_defects AS (
      SELECT b.source_file, b.source_period, b.source_year, '' AS cust_id,
             concat('trailer=', CAST(b.trailer_count AS STRING), ' body=', CAST(b.body_count AS STRING)) AS raw_line,
             'trailer_count_mismatch' AS reason
      FROM (
        SELECT source_file, source_period, source_year,
               max(CASE WHEN record_kind = 'TRL' THEN CAST(substr(raw_line, 4, 10) AS BIGINT) END) AS trailer_count,
               count_if(record_kind = 'BODY') AS body_count
        FROM {n.bronze} GROUP BY source_file, source_period, source_year
      ) b
      WHERE b.trailer_count IS NOT NULL AND b.trailer_count <> b.body_count
    )
    SELECT source_file, source_period, source_year, cust_id, raw_line, reason, current_timestamp()
    FROM (SELECT * FROM row_defects UNION ALL SELECT * FROM trailer_defects)"""


def build_gold(n: Names) -> str:
    return f"""
    INSERT OVERWRITE {n.gold}
    SELECT source_year, currency, record_type, count(*) AS record_count,
           sum(amount_cents) AS total_amount_cents
    FROM {n.silver}
    GROUP BY source_year, currency, record_type"""


def recon_checks(n: Names) -> str:
    """Row-per-check reconciliation, recomputed from the target tables."""
    return f"""
    WITH gold AS (SELECT * FROM {n.gold}),
    exp AS (SELECT * FROM {n.expectations}),
    totals AS (
      -- coalesce across the full outer join: a gold group with no expectation is
      -- a mismatch to report, not a NULL check_id that blows up the report
      SELECT concat('annual_total/', CAST(coalesce(e.source_year, g.source_year) AS STRING), '/',
                    coalesce(e.currency, g.currency), '/', coalesce(e.record_type, g.record_type)) AS check_id,
             concat(CAST(coalesce(e.record_count, 0) AS STRING), '|',
                    CAST(coalesce(e.total_amount_cents, 0) AS STRING)) AS expected,
             concat(CAST(coalesce(g.record_count, 0) AS STRING), '|', CAST(coalesce(g.total_amount_cents, 0) AS STRING)) AS actual
      FROM exp e FULL OUTER JOIN gold g
        ON e.source_year = g.source_year AND e.currency = g.currency AND e.record_type = g.record_type
    ),
    exp_year AS (
      SELECT source_year, max(quarantine_record_count) AS quarantine_record_count,
             max(file_count) AS file_count
      FROM exp GROUP BY source_year
    ),
    quarantine AS (
      SELECT concat('quarantine_count/', CAST(e.source_year AS STRING)) AS check_id,
             CAST(e.quarantine_record_count AS STRING) AS expected,
             CAST(coalesce(q.actual_count, 0) AS STRING) AS actual
      FROM exp_year e
      LEFT JOIN (SELECT source_year, count(*) AS actual_count FROM {n.quarantine}
                 WHERE reason <> 'trailer_count_mismatch' GROUP BY source_year) q
        ON e.source_year = q.source_year
    ),
    files AS (
      SELECT concat('file_count/', CAST(e.source_year AS STRING)) AS check_id,
             CAST(e.file_count AS STRING) AS expected,
             CAST(coalesce(b.actual_count, 0) AS STRING) AS actual
      FROM exp_year e
      LEFT JOIN (SELECT source_year, count(DISTINCT source_file) AS actual_count FROM {n.bronze}
                 GROUP BY source_year) b
        ON e.source_year = b.source_year
    ),
    grand AS (
      SELECT 'grand_total/all_years' AS check_id,
             concat(CAST(sum(record_count) AS STRING), '|', CAST(sum(total_amount_cents) AS STRING)) AS expected,
             concat(CAST((SELECT coalesce(sum(record_count), 0) FROM gold) AS STRING), '|',
                    CAST((SELECT coalesce(sum(total_amount_cents), 0) FROM gold) AS STRING)) AS actual
      FROM exp
    ),
    all_checks AS (
      SELECT * FROM totals UNION ALL SELECT * FROM quarantine
      UNION ALL SELECT * FROM files UNION ALL SELECT * FROM grand
    )
    SELECT check_id, expected, actual,
           CASE WHEN expected = actual THEN 'pass' ELSE 'fail' END AS result
    FROM all_checks ORDER BY check_id"""


def recon_gate(n: Names) -> str:
    """Same checks, but fails the SQL task so a Databricks job run goes red."""
    return f"""
    WITH checks AS ({recon_checks(n)}),
    failures AS (
      SELECT concat(check_id, ' expected=', expected, ' actual=', actual) AS msg
      FROM checks WHERE result = 'fail'
    )
    SELECT CASE WHEN (SELECT count(*) FROM failures) > 0
                THEN raise_error(concat('RECONCILIATION FAILED (', CAST((SELECT count(*) FROM failures) AS STRING),
                     ' checks): ', (SELECT concat_ws('; ', slice(collect_list(msg), 1, 8)) FROM failures)))
                ELSE concat('RECONCILIATION GREEN: ', CAST((SELECT count(*) FROM checks) AS STRING),
                     ' checks match the legacy baseline')
           END AS recon_result"""


def dlt_source(n: Names) -> str:
    """Lakeflow declarative pipeline source: the same bronze -> silver -> gold
    shape, but with the quarantine rules expressed as declared expectations
    instead of harness code, so data quality shows up in the pipeline UI."""
    return f"""-- Databricks notebook source
-- OtterWorks CUSTBILL history: declarative quality rules for ns={n.ns}.
-- Generated by scripts/tp_dbx/showcase.py; edit there, not here.

CREATE OR REFRESH MATERIALIZED VIEW custbill_dlt_{n.ns} (
  CONSTRAINT valid_calendar_date EXPECT (bill_date IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT numeric_amount EXPECT (amount_cents IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT known_currency EXPECT (currency IN ('USD', 'EUR', 'GBP')),
  CONSTRAINT known_record_type EXPECT (record_type IN ('01', '02'))
)
COMMENT 'Validated CUSTBILL records; rows the legacy parser passed through silently are dropped by expectation'
AS SELECT
     trim(substr(raw_line, 1, 10)) AS cust_id,
     trim(substr(raw_line, 11, 30)) AS cust_name,
     try_to_date(substr(raw_line, 41, 8), 'yyyyMMdd') AS bill_date,
     -- same digits-only predicate the harness quarantines on: try_cast would
     -- accept a padded or signed amount the harness rejects, and the row would
     -- then be both kept here and routed to quarantine below
     CASE WHEN substr(raw_line, 49, 12) RLIKE '^[0-9]{{12}}$'
          THEN CAST(substr(raw_line, 49, 12) AS BIGINT) END AS amount_cents,
     trim(substr(raw_line, 61, 3)) AS currency,
     substr(raw_line, 64, 2) AS record_type,
     source_file, source_period, source_year
   FROM {n.bronze}
   WHERE record_kind = 'BODY';

-- Quarantine routing, declared: the rows the expectations above drop are kept
-- here with the same reason vocabulary the harness uses, so nothing is lost.
CREATE OR REFRESH MATERIALIZED VIEW custbill_dlt_quarantine_{n.ns} (
  CONSTRAINT quarantine_reason_known
    EXPECT (reason IN ('nonnumeric_amount', 'invalid_calendar_date'))
)
COMMENT 'Records dropped by the silver expectations, routed with their reason'
AS SELECT source_file, source_period, source_year,
          trim(substr(raw_line, 1, 10)) AS cust_id, raw_line,
          CASE WHEN NOT substr(raw_line, 49, 12) RLIKE '^[0-9]{{12}}$'
               THEN 'nonnumeric_amount' ELSE 'invalid_calendar_date' END AS reason
   FROM {n.bronze}
   WHERE record_kind = 'BODY'
     AND (NOT substr(raw_line, 49, 12) RLIKE '^[0-9]{{12}}$'
          OR try_to_date(substr(raw_line, 41, 8), 'yyyyMMdd') IS NULL);

-- File-level integrity: the legacy chain only warned on a trailer mismatch, so
-- this expectation reports the bad files rather than dropping their rows.
CREATE OR REFRESH MATERIALIZED VIEW custbill_dlt_files_{n.ns} (
  CONSTRAINT trailer_count_matches_body EXPECT (trailer_count = body_count)
)
COMMENT 'One row per landed CUSTBILL drop with its trailer/body reconciliation'
AS SELECT source_file, source_period, source_year,
          max(CASE WHEN record_kind = 'TRL'
                   THEN CAST(substr(raw_line, 4, 10) AS BIGINT) END) AS trailer_count,
          count_if(record_kind = 'BODY') AS body_count
   FROM {n.bronze}
   GROUP BY source_file, source_period, source_year;

CREATE OR REFRESH MATERIALIZED VIEW custbill_dlt_annual_{n.ns}
COMMENT 'Annual finance totals derived inside the pipeline (finance_excel_report replacement)'
AS SELECT source_year, currency, record_type, count(*) AS record_count,
          sum(amount_cents) AS total_amount_cents
   FROM custbill_dlt_{n.ns}
   GROUP BY source_year, currency, record_type;
"""


def dlt_quality_parity(n: Names) -> str:
    """Do the pipeline's declared expectations quarantine exactly the rows the
    harness quarantines? Trailer defects are file-level in the pipeline, so they
    are compared as file counts, not row counts."""
    return f"""
    WITH harness_rows AS (
      SELECT source_file, reason, cust_id FROM {n.quarantine}
      WHERE reason <> 'trailer_count_mismatch'
    ),
    pipeline_rows AS (
      SELECT source_file, reason, cust_id
      FROM {n.catalog}.silver.custbill_dlt_quarantine_{n.ns}
    ),
    row_diff AS (
      SELECT 'quarantine_row' AS scope, 'harness_only' AS side, count(*) AS records
      FROM (SELECT * FROM harness_rows EXCEPT SELECT * FROM pipeline_rows)
      UNION ALL
      SELECT 'quarantine_row', 'pipeline_only', count(*)
      FROM (SELECT * FROM pipeline_rows EXCEPT SELECT * FROM harness_rows)
    ),
    trailer_diff AS (
      SELECT 'trailer_file' AS scope, 'count_mismatch' AS side,
             abs((SELECT count(DISTINCT source_file) FROM {n.quarantine}
                  WHERE reason = 'trailer_count_mismatch')
                 - (SELECT count(*) FROM {n.catalog}.silver.custbill_dlt_files_{n.ns}
                    WHERE trailer_count IS NOT NULL AND trailer_count <> body_count)) AS records
    )
    SELECT * FROM (SELECT * FROM row_diff UNION ALL SELECT * FROM trailer_diff)
    WHERE records <> 0"""


def dlt_parity(n: Names) -> str:
    """Does the declarative pipeline agree with the harness-built gold table?"""
    return f"""
    SELECT g.source_year, g.currency, g.record_type,
           g.record_count AS harness_rows, d.record_count AS pipeline_rows,
           g.total_amount_cents AS harness_cents, d.total_amount_cents AS pipeline_cents
    FROM {n.gold} g
    FULL OUTER JOIN {n.catalog}.silver.custbill_dlt_annual_{n.ns} d
      ON g.source_year = d.source_year AND g.currency = d.currency
     AND g.record_type = d.record_type
    WHERE coalesce(g.record_count, -1) <> coalesce(d.record_count, -1)
       OR coalesce(g.total_amount_cents, -1) <> coalesce(d.total_amount_cents, -1)
    ORDER BY g.source_year"""


def anomaly_set(n: Names) -> str:
    return f"""
    SELECT source_file, reason, cust_id FROM {n.quarantine}
    ORDER BY source_file, reason, cust_id"""


def describe_history(n: Names, table: str) -> str:
    return f"DESCRIBE HISTORY {table}"


def timetravel_totals(n: Names, table: str, version: int) -> str:
    """As-of evidence for the table actually asked about; each layer has its own
    money column (bronze has none), so the projection follows the layer."""
    if table == n.gold:
        measures = ("coalesce(sum(record_count), 0) AS record_count, "
                    "coalesce(sum(total_amount_cents), 0) AS total_amount_cents")
    elif table == n.silver:
        measures = ("count(*) AS record_count, "
                    "coalesce(sum(amount_cents), 0) AS total_amount_cents")
    else:
        measures = "count(*) AS record_count"
    return f"SELECT {measures} FROM {table} VERSION AS OF {version}"
