#!/usr/bin/env python3
"""SQL for the ow_tp_parse_<ns> conversion of parse_custbill_fixedwidth.sh.

One place for every statement so the recon the Databricks job runs and the
recon the harness runs are provably the same text, parameterised only by
namespace.

Fixed-width layout is copybook CBCUST01 (see
etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh):
  1-10 CUST-ID, 11-40 CUST-NAME, 41-48 BILL-DATE YYYYMMDD,
  49-60 BILL-AMT PIC 9(10)V99 implied decimal, 61-63 CURRENCY, 64-65 REC-TYPE

Legacy parse semantics preserved byte-for-byte on valid records:
  - trailing spaces stripped from CUST-ID, CUST-NAME and CURRENCY only
    (awk gsub(/ +$/,"") -> rtrim, never trim)
  - amount rendered with exactly two decimals, no thousands separator
  - date rendered YYYY-MM-DD by digit insertion

Records the legacy parser silently mishandled become explicit quarantine
rows instead: invalid_cust_id, nonnumeric_amount, invalid_calendar_date,
unknown_currency, unknown_record_type (row-level) and
trailer_count_mismatch (file-level).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Names:
    catalog: str = "ow_tp"
    ns: str = "cnvparse"

    @property
    def landing(self) -> str:
        return f"/Volumes/{self.catalog}/bronze/landing/{self.ns}"

    @property
    def drop_dir(self) -> str:
        return f"{self.landing}/parse"

    @property
    def bronze(self) -> str:
        return f"{self.catalog}.bronze.custbill_parse_raw_{self.ns}"

    @property
    def silver(self) -> str:
        return f"{self.catalog}.silver.custbill_parsed_{self.ns}"

    @property
    def quarantine(self) -> str:
        return f"{self.catalog}.silver.custbill_parse_quarantine_{self.ns}"

    @property
    def expectations(self) -> str:
        return f"{self.catalog}.ops.parse_expectations_{self.ns}"

    @property
    def recon_runs(self) -> str:
        return f"{self.catalog}.ops.parse_recon_runs_{self.ns}"

    @property
    def job_name(self) -> str:
        return f"ow_tp_parse_{self.ns}"


def provision(n: Names) -> list[str]:
    """Namespace-scoped tables only. The catalog, medallion schemas and the
    landing volume are parent-owned shared infrastructure and are never
    created (or replaced) here."""
    return [
        f"""CREATE TABLE IF NOT EXISTS {n.bronze} (
              source_file STRING COMMENT 'CUSTBILL extract file name as dropped by the mainframe',
              record_kind STRING COMMENT 'HDR, TRL or BODY',
              raw_line STRING COMMENT 'Untouched fixed-width record',
              file_modification_time TIMESTAMP,
              ingested_at TIMESTAMP)
            USING DELTA
            COMMENT 'Bronze: byte-preserved CUSTBILL drops, one row per line'""",
        f"""CREATE TABLE IF NOT EXISTS {n.silver} (
              cust_id STRING, cust_name STRING, bill_date DATE,
              amount_cents BIGINT COMMENT 'PIC 9(10)V99 implied decimal held as cents',
              currency STRING, record_type STRING COMMENT '01=invoice 02=credit',
              source_file STRING)
            USING DELTA
            COMMENT 'Silver: schema-validated CUSTBILL records (quarantined rows excluded)'""",
        f"""CREATE TABLE IF NOT EXISTS {n.quarantine} (
              source_file STRING, cust_id STRING, raw_line STRING,
              reason STRING COMMENT 'invalid_cust_id | nonnumeric_amount | invalid_calendar_date | unknown_currency | unknown_record_type | trailer_count_mismatch',
              detected_at TIMESTAMP)
            USING DELTA
            COMMENT 'Silver: records the legacy parser passed through or ignored silently'""",
        f"""CREATE TABLE IF NOT EXISTS {n.expectations} (
              check_id STRING, expected STRING)
            USING DELTA
            COMMENT 'Legacy-derived expected values (source of truth: deterministic legacy baseline)'""",
        f"""CREATE TABLE IF NOT EXISTS {n.recon_runs} (
              run_id STRING, checked_at TIMESTAMP, check_id STRING,
              expected STRING, actual STRING, result STRING)
            USING DELTA
            COMMENT 'Every reconciliation check ever run against this namespace'""",
    ]


def load_bronze(n: Names) -> str:
    return f"""
    INSERT OVERWRITE {n.bronze}
    SELECT
      regexp_extract(_metadata.file_path, '([^/]+)$', 1) AS source_file,
      CASE WHEN startswith(value, 'HDR') THEN 'HDR'
           WHEN startswith(value, 'TRL') THEN 'TRL'
           ELSE 'BODY' END AS record_kind,
      value AS raw_line,
      _metadata.file_modification_time AS file_modification_time,
      current_timestamp() AS ingested_at
    FROM read_files('{n.drop_dir}', format => 'text', recursiveFileLookup => true)
    WHERE length(trim(value)) > 0"""


def _body_projection(n: Names) -> str:
    # rtrim, not trim: the legacy awk strips trailing spaces only, and valid
    # records must round-trip byte-for-byte
    return f"""
      SELECT
        rtrim(substr(raw_line, 1, 10)) AS cust_id,
        rtrim(substr(raw_line, 11, 30)) AS cust_name,
        substr(raw_line, 41, 8) AS bill_date_raw,
        substr(raw_line, 49, 12) AS amount_raw,
        rtrim(substr(raw_line, 61, 3)) AS currency,
        substr(raw_line, 64, 2) AS record_type,
        source_file, raw_line
      FROM {n.bronze}
      WHERE record_kind = 'BODY'"""


_VALID = """length(cust_id) BETWEEN 1 AND 10
      AND amount_raw RLIKE '^[0-9]{12}$'
      AND try_to_date(bill_date_raw, 'yyyyMMdd') IS NOT NULL
      AND currency IN ('USD', 'EUR', 'GBP')
      AND record_type IN ('01', '02')"""


def build_silver(n: Names) -> str:
    return f"""
    INSERT OVERWRITE {n.silver}
    SELECT cust_id, cust_name, to_date(bill_date_raw, 'yyyyMMdd') AS bill_date,
           CAST(amount_raw AS BIGINT) AS amount_cents, currency, record_type,
           source_file
    FROM ({_body_projection(n)}) parsed
    WHERE {_VALID}"""


def build_quarantine(n: Names) -> str:
    return f"""
    INSERT OVERWRITE {n.quarantine}
    WITH parsed AS ({_body_projection(n)}),
    row_defects AS (
      SELECT source_file, cust_id, raw_line,
             CASE WHEN length(cust_id) = 0 THEN 'invalid_cust_id'
                  WHEN NOT amount_raw RLIKE '^[0-9]{{12}}$' THEN 'nonnumeric_amount'
                  WHEN try_to_date(bill_date_raw, 'yyyyMMdd') IS NULL THEN 'invalid_calendar_date'
                  WHEN currency NOT IN ('USD', 'EUR', 'GBP') THEN 'unknown_currency'
                  ELSE 'unknown_record_type' END AS reason
      FROM parsed
      WHERE NOT ({_VALID})
    ),
    trailer_defects AS (
      SELECT b.source_file, '' AS cust_id,
             concat('trailer=', CAST(b.trailer_count AS STRING), ' body=', CAST(b.body_count AS STRING)) AS raw_line,
             'trailer_count_mismatch' AS reason
      FROM (
        SELECT source_file,
               max(CASE WHEN record_kind = 'TRL' THEN CAST(substr(raw_line, 4, 10) AS BIGINT) END) AS trailer_count,
               count_if(record_kind = 'BODY') AS body_count
        FROM {n.bronze} GROUP BY source_file
      ) b
      WHERE b.trailer_count IS NOT NULL AND b.trailer_count <> b.body_count
    )
    SELECT source_file, cust_id, raw_line, reason, current_timestamp()
    FROM (SELECT * FROM row_defects UNION ALL SELECT * FROM trailer_defects)"""


# The exact bytes the legacy parser emitted for a valid record, reconstructed
# from the typed silver columns. Integer div/mod, not float division: awk's
# sprintf("%.2f", cents/100) agrees with exact cents arithmetic for every
# non-negative integer amount, and integers cannot pick up float noise.
PSV_LINE = """concat(cust_id, '|', cust_name, '|', date_format(bill_date, 'yyyy-MM-dd'), '|',
               CAST(amount_cents DIV 100 AS STRING), '.', lpad(CAST(amount_cents % 100 AS STRING), 2, '0'), '|',
               currency, '|', record_type)"""


def recon_checks(n: Names) -> str:
    """Row-per-check reconciliation, recomputed from the target tables and
    compared against the legacy-derived expectations table."""
    return f"""
    WITH exp AS (SELECT check_id, expected FROM {n.expectations}),
    silver_lines AS (
      SELECT source_file, amount_cents, currency, record_type,
             {PSV_LINE} AS psv_line
      FROM {n.silver}
    ),
    actuals AS (
      SELECT concat('file_valid_rows/', source_file) AS check_id,
             CAST(count(*) AS STRING) AS actual
      FROM silver_lines GROUP BY source_file
      UNION ALL
      SELECT concat('file_valid_sha256/', source_file),
             sha2(concat_ws(char(10), sort_array(collect_list(psv_line))), 256)
      FROM silver_lines GROUP BY source_file
      UNION ALL
      SELECT concat('input_sha256/', source_file),
             sha2(concat_ws(char(10), sort_array(collect_list(raw_line))), 256)
      FROM {n.bronze} GROUP BY source_file
      UNION ALL
      SELECT concat('totals/', currency, '/', record_type),
             concat(CAST(count(*) AS STRING), '|', CAST(sum(amount_cents) AS STRING))
      FROM silver_lines GROUP BY currency, record_type
      UNION ALL
      SELECT 'grand_total',
             concat(CAST(count(*) AS STRING), '|', CAST(coalesce(sum(amount_cents), 0) AS STRING))
      FROM silver_lines
      UNION ALL
      SELECT 'files_ingested', CAST(count(DISTINCT source_file) AS STRING) FROM {n.bronze}
      UNION ALL
      SELECT 'quarantine_rows', CAST(count(*) AS STRING) FROM {n.quarantine}
    )
    SELECT coalesce(e.check_id, a.check_id) AS check_id,
           coalesce(e.expected, '<no expectation>') AS expected,
           coalesce(a.actual, '<not recomputed>') AS actual,
           CASE WHEN e.expected IS NOT NULL AND a.actual IS NOT NULL AND e.expected = a.actual
                THEN 'pass' ELSE 'fail' END AS result
    FROM exp e FULL OUTER JOIN actuals a ON e.check_id = a.check_id
    ORDER BY check_id"""


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


def anomaly_set(n: Names) -> str:
    return f"""
    SELECT source_file, reason, cust_id FROM {n.quarantine}
    ORDER BY source_file, reason, cust_id"""
