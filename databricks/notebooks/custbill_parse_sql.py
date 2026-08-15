"""Pipeline SQL for the converted CUSTBILL parse job (`ow_tp_parse_custbill`).

Companion to `custbill_sql.py` (table DDL). This module holds the statements that
actually do the conversion work, so the notebook the Databricks job runs and the
local driver that executes the same statement set through
`scripts/tp_databricks/dbx.py` can never drift apart:

    gate_statements(ns)   -- the bronze manifest handshake that replaces the
                             legacy cron offset (`*/15` ingest vs `5-59/15`
                             parse), any of which returning a non-empty result
                             means the run must fail before touching silver.
    parse_statements(ns)  -- typed parse into namespace-scoped staging tables.
    publish_statements(ns) -- atomically replace the published namespace from staging.
    recon_gate_statements(ns) -- post-parse assertions; a non-empty result fails
                             the run (the reconciliation ETL-0187 asked for in
                             2011 and the legacy job only ever logged).

The parse stages all three outputs, gates the staged trailer reconciliation, then
publishes records, rejects, and recon last with one atomic Delta statement each.
The three publishes are not cross-table atomic: a failure between statements can
leave a namespace temporarily mixed across the published tables, so recon is
published last and the residual window is explicit rather than implied away.

Both statement groups are pure strings: no Databricks, no Spark session, so they
are reviewable and diffable on their own.

Copybook CBCUST01 field positions (1-based, as in the legacy `cut -c` calls):
CUST-ID 1-10, CUST-NAME 11-40, BILL-DATE 41-48, BILL-AMT 49-60 (implied V99),
CURRENCY 61-63, REC-TYPE 64-65. Detail records are 65 characters.
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
SILVER_RECORDS = f"{CATALOG}.silver.custbill_records"
SILVER_REJECTS = f"{CATALOG}.silver.custbill_rejects"
SILVER_FILE_RECON = f"{CATALOG}.silver.custbill_file_recon"
STAGING_RECORDS = f"{CATALOG}.silver.custbill_records_staging"
STAGING_REJECTS = f"{CATALOG}.silver.custbill_rejects_staging"
STAGING_FILE_RECON = f"{CATALOG}.silver.custbill_file_recon_staging"
BRONZE_FILES = f"{CATALOG}.bronze.custbill_files"
BRONZE_LINES = f"{CATALOG}.bronze.custbill_lines"

RECORD_LENGTH = 65


def validate_namespace(value: str) -> str:
    """Validate a demo namespace before it is interpolated into SQL."""
    if re.fullmatch(r"[A-Za-z0-9_]+", value) is None:
        raise ValueError(f"invalid namespace {value!r}: expected [A-Za-z0-9_]+")
    return value


def quote_sql_literal(value: str) -> str:
    """Single-quote a SQL string literal (namespaces are `[a-z0-9_]+`, but the
    statements are still built without string-concatenating unescaped input)."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _sliced_cte(ns: str) -> str:
    """CTE slicing bronze detail records into typed candidate columns.

    One pass over the bronze table replaces the legacy three passes of
    `sed`/`cut`/`awk` over a PID-suffixed temp file (`/tmp/cb_body.$$`, left
    behind whenever the parse failed).
    """
    lit = quote_sql_literal(ns)
    return f"""
    WITH detail AS (
      SELECT l.ns, l.file_name, l.line_no, l.raw_line
      FROM {BRONZE_LINES} l
      INNER JOIN (
        SELECT DISTINCT ns, file_name
        FROM {BRONZE_FILES}
        WHERE ns = {lit}
      ) m ON m.ns = l.ns AND m.file_name = l.file_name
      WHERE l.ns = {lit}
        AND l.raw_line NOT LIKE 'HDR%'
        AND l.raw_line NOT LIKE 'TRL%'
    ),
    sliced AS (
      SELECT
        ns,
        file_name,
        line_no,
        raw_line,
        rtrim(substr(raw_line, 1, 10))  AS account_id,
        rtrim(substr(raw_line, 11, 30)) AS customer_name,
        substr(raw_line, 41, 8)         AS bill_date_raw,
        substr(raw_line, 49, 12)        AS amount_raw,
        rtrim(substr(raw_line, 61, 3))  AS currency,
        substr(raw_line, 64, 2)         AS record_type
      FROM detail
    ),
    typed AS (
      SELECT
        sliced.*,
        try_to_date(bill_date_raw, 'yyyyMMdd') AS bill_date,
        try_cast(amount_raw AS DECIMAL(20,0)) AS amount_units,
        CASE
          WHEN length(raw_line) <> {RECORD_LENGTH} THEN 'bad_record_length'
          WHEN NOT account_id RLIKE '^[A-Za-z0-9]{{1,10}}$' THEN 'invalid_account_id'
          WHEN NOT amount_raw RLIKE '^[0-9]{{12}}$' THEN 'invalid_amount'
          WHEN try_to_date(bill_date_raw, 'yyyyMMdd') IS NULL THEN 'invalid_bill_date'
          WHEN NOT currency RLIKE '^[A-Z]{{3}}$' THEN 'invalid_currency'
          WHEN record_type NOT IN ('01', '02') THEN 'invalid_record_type'
          ELSE NULL
        END AS reject_reason
      FROM sliced
    )
    """


def gate_statements(ns: str) -> list[tuple[str, str]]:
    """Bronze manifest handshake, run before any silver write.

    Each statement returns the offending rows; a non-empty result means the
    upstream landing is incomplete and the run must fail. This is the explicit
    dependency that replaces "the parse runs five minutes after the ingest and
    usually that works out", and the manifest/checksum handshake that replaces
    the legacy size-compared-twice settle check.
    """
    lit = quote_sql_literal(ns)
    return [
        (
            "bronze manifest is present",
            f"SELECT 'no files in manifest' AS problem FROM (SELECT count(*) AS c FROM {BRONZE_FILES} WHERE ns = {lit}) WHERE c = 0",
        ),
        (
            "every bronze line belongs to a manifest file",
            f"""
            SELECT l.file_name, count(*) AS orphaned_lines
            FROM {BRONZE_LINES} l
            LEFT JOIN {BRONZE_FILES} f ON f.ns = l.ns AND f.file_name = l.file_name
            WHERE l.ns = {lit} AND f.file_name IS NULL
            GROUP BY l.file_name
            """,
        ),
        (
            "every manifest file has bronze lines, exactly one HDR and one TRL",
            f"""
            SELECT f.file_name,
                   count(l.raw_line) AS lines,
                   sum(CASE WHEN l.raw_line LIKE 'HDR%' THEN 1 ELSE 0 END) AS hdr,
                   sum(CASE WHEN l.raw_line LIKE 'TRL%' THEN 1 ELSE 0 END) AS trl
            FROM {BRONZE_FILES} f
            LEFT JOIN {BRONZE_LINES} l ON l.ns = f.ns AND l.file_name = f.file_name
            WHERE f.ns = {lit}
            GROUP BY f.file_name
            HAVING lines = 0 OR hdr <> 1 OR trl <> 1
            """,
        ),
        (
            "manifest record_count matches all landed lines",
            f"""
            -- The ingest unit's manifest record_count includes HDR and TRL rows.
            SELECT f.file_name, f.record_count AS manifest_record_count, count(l.raw_line) AS landed_lines
            FROM {BRONZE_FILES} f
            LEFT JOIN {BRONZE_LINES} l ON l.ns = f.ns AND l.file_name = f.file_name
            WHERE f.ns = {lit}
            GROUP BY f.file_name, f.record_count
            HAVING f.record_count <> count(l.raw_line)
            """,
        ),
        (
            "bronze lines carry no duplicate (file_name, line_no)",
            f"""
            SELECT file_name, line_no, count(*) AS copies
            FROM {BRONZE_LINES}
            WHERE ns = {lit}
            GROUP BY file_name, line_no
            HAVING count(*) > 1
            """,
        ),
    ]


def parse_statements(ns: str) -> list[tuple[str, str]]:
    """Build the namespace-scoped staging parse, idempotent without touching published rows."""
    lit = quote_sql_literal(ns)
    sliced = _sliced_cte(ns)
    return [
        (
            "clear staged silver.custbill_records for this namespace",
            f"DELETE FROM {STAGING_RECORDS} WHERE ns = {lit}",
        ),
        (
            "clear staged silver.custbill_rejects for this namespace",
            f"DELETE FROM {STAGING_REJECTS} WHERE ns = {lit}",
        ),
        (
            "clear staged silver.custbill_file_recon for this namespace",
            f"DELETE FROM {STAGING_FILE_RECON} WHERE ns = {lit}",
        ),
        (
            "typed detail records into staged silver.custbill_records",
            f"""
            INSERT INTO {STAGING_RECORDS}
              (ns, file_name, line_no, record_type, account_id, customer_name,
               invoice_id, currency, amount, bill_date, parsed_at)
            {sliced}
            SELECT
              ns,
              file_name,
              line_no,
              record_type,
              account_id,
              customer_name,
              CAST(NULL AS STRING) AS invoice_id,
              currency,
              CAST(amount_units / 100 AS DECIMAL(18,2)) AS amount,
              bill_date,
              current_timestamp() AS parsed_at
            FROM typed
            WHERE reject_reason IS NULL
            """,
        ),
        (
            "quarantine invalid records in staged silver.custbill_rejects",
            f"""
            INSERT INTO {STAGING_REJECTS}
              (ns, file_name, line_no, raw_line, reject_reason, rejected_at)
            {sliced}
            SELECT ns, file_name, line_no, raw_line, reject_reason, current_timestamp()
            FROM typed
            WHERE reject_reason IS NOT NULL
            """,
        ),
        (
            "trailer reconciliation into staged silver.custbill_file_recon",
            f"""
            INSERT INTO {STAGING_FILE_RECON}
              (ns, file_name, declared_trailer_count, parsed_count, rejected_count,
               recon_ok, reconciled_at)
            WITH trailer AS (
              SELECT ns, file_name,
                     try_cast(regexp_extract(raw_line, '^TRL([0-9]+)', 1) AS BIGINT) AS declared_trailer_count
              FROM {BRONZE_LINES}
              WHERE ns = {lit} AND raw_line LIKE 'TRL%'
            ),
            parsed AS (
              SELECT file_name, count(*) AS parsed_count
              FROM {STAGING_RECORDS} WHERE ns = {lit} GROUP BY file_name
            ),
            rejected AS (
              SELECT file_name, count(*) AS rejected_count
              FROM {STAGING_REJECTS} WHERE ns = {lit} GROUP BY file_name
            )
            SELECT
              t.ns,
              t.file_name,
              t.declared_trailer_count,
              coalesce(p.parsed_count, 0)   AS parsed_count,
              coalesce(r.rejected_count, 0) AS rejected_count,
              t.declared_trailer_count = coalesce(p.parsed_count, 0) + coalesce(r.rejected_count, 0) AS recon_ok,
              current_timestamp() AS reconciled_at
            FROM trailer t
            LEFT JOIN parsed p ON p.file_name = t.file_name
            LEFT JOIN rejected r ON r.file_name = t.file_name
            """,
        ),
    ]


def publish_statements(ns: str) -> list[tuple[str, str]]:
    """Publish the validated namespace from staging with one replace per table."""
    lit = quote_sql_literal(ns)
    return [
        (
            "publish silver.custbill_records",
            f"""
            INSERT INTO {SILVER_RECORDS} BY NAME REPLACE WHERE ns = {lit}
            SELECT ns, file_name, line_no, record_type, account_id, customer_name,
                   invoice_id, currency, amount, bill_date, parsed_at
            FROM {STAGING_RECORDS}
            WHERE ns = {lit}
            """,
        ),
        (
            "publish silver.custbill_rejects",
            f"""
            INSERT INTO {SILVER_REJECTS} BY NAME REPLACE WHERE ns = {lit}
            SELECT ns, file_name, line_no, raw_line, reject_reason, rejected_at
            FROM {STAGING_REJECTS}
            WHERE ns = {lit}
            """,
        ),
        (
            "publish silver.custbill_file_recon",
            f"""
            INSERT INTO {SILVER_FILE_RECON} BY NAME REPLACE WHERE ns = {lit}
            SELECT ns, file_name, declared_trailer_count, parsed_count, rejected_count,
                   recon_ok, reconciled_at
            FROM {STAGING_FILE_RECON}
            WHERE ns = {lit}
            """,
        ),
    ]


def recon_gate_statements(ns: str, staged: bool = False) -> list[tuple[str, str]]:
    """Trailer and published-output assertions. A non-empty result must fail."""
    lit = quote_sql_literal(ns)
    records = STAGING_RECORDS if staged else SILVER_RECORDS
    file_recon = STAGING_FILE_RECON if staged else SILVER_FILE_RECON
    statements = [
        (
            "trailer counts reconcile for every file",
            f"""
            SELECT file_name, declared_trailer_count, parsed_count, rejected_count
            FROM {file_recon}
            WHERE ns = {lit} AND (recon_ok IS NOT TRUE)
            """,
        ),
        (
            "every bronze file produced a recon row",
            f"""
            SELECT f.file_name
            FROM {BRONZE_FILES} f
            LEFT JOIN {file_recon} c ON c.ns = f.ns AND c.file_name = f.file_name
            WHERE f.ns = {lit} AND c.file_name IS NULL
            """,
        ),
        (
            "no duplicate (file_name, line_no) in silver.custbill_records",
            f"""
            SELECT file_name, line_no, count(*) AS copies
            FROM {records}
            WHERE ns = {lit}
            GROUP BY file_name, line_no
            HAVING count(*) > 1
            """,
        ),
    ]
    if staged:
        statements.append(
            (
                "staged output is not empty when published rows exist",
                f"""
                SELECT concat(
                           'empty staged result would have erased ',
                           cast(p.published_count AS STRING),
                           ' published rows'
                       ) AS problem
                FROM (
                    SELECT count(*) AS published_count
                    FROM {SILVER_RECORDS}
                    WHERE ns = {lit}
                ) p
                CROSS JOIN (
                    SELECT count(*) AS staged_count
                    FROM {STAGING_RECORDS}
                    WHERE ns = {lit}
                ) s
                WHERE p.published_count > 0 AND s.staged_count = 0
                """,
            )
        )
    return statements
