#!/usr/bin/env python3
"""SQL for the CUSTBILL chain job (legacy crontab + run_all.sh replacement).

Every statement lives here so the text the Databricks job runs is provably the
text the recon harness reads. Names are namespace-suffixed: a namespace only
ever touches `*_<ns>` tables and the `<ns>/` prefix of the shared landing
volume.

Legacy stages this replaces (etl/legacy-extra/):
  sftp_ingest_poll.ksh          -> task `ingest`   (bronze landing, no sleep-settle)
  parse_custbill_fixedwidth.sh  -> task `parse`    (bronze -> silver, trailer gate)
  finance_excel_report.pl       -> task `finance`  (silver -> gold aggregate)
  run_all.sh + crontab offsets  -> task dependencies, max_concurrent_runs=1

Fixed-width layout is copybook CBCUST01:
  1-10 CUST-ID, 11-40 CUST-NAME, 41-48 BILL-DATE YYYYMMDD,
  49-60 BILL-AMT PIC 9(10)V99 implied decimal, 61-63 CURRENCY, 64-65 REC-TYPE
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NAME_RE = re.compile(r"^[a-z0-9_]{1,24}$")


@dataclass(frozen=True)
class ChainNames:
    ns: str
    catalog: str = "ow_tp"

    def __post_init__(self) -> None:
        """Names are interpolated into generated SQL text, so they are constrained
        here as well as at the CLI boundary: no builder can emit quote-breaking
        text regardless of how it was constructed."""
        for field, value in (("ns", self.ns), ("catalog", self.catalog)):
            if not NAME_RE.match(value):
                raise ValueError(f"{field}={value!r} must match {NAME_RE.pattern}")

    @property
    def drop_dir(self) -> str:
        """Landing-volume replacement for /sftp/mainframe/upload."""
        return f"/Volumes/{self.catalog}/bronze/landing/{self.ns}/chain/drop"

    @property
    def drop_marker(self) -> str:
        """read_files cannot resolve _metadata when the directory it scans holds
        no readable file, so an empty batch would fail instead of being the
        no-op the contract requires. This marker keeps the drop directory
        non-empty; the ingest filter excludes it by name. It cannot be named
        with a leading underscore or dot, which read_files ignores."""
        return f"{self.drop_dir}/chain-keep.marker"

    @property
    def bronze(self) -> str:
        return f"{self.catalog}.bronze.chain_landed_{self.ns}"

    @property
    def silver(self) -> str:
        return f"{self.catalog}.silver.chain_records_{self.ns}"

    @property
    def gold(self) -> str:
        return f"{self.catalog}.gold.chain_finance_{self.ns}"

    @property
    def ledger(self) -> str:
        return f"{self.catalog}.ops.chain_runs_{self.ns}"

    @property
    def job_name(self) -> str:
        return f"{self.catalog}_custbill_chain_{self.ns}"

    @property
    def workspace_dir(self) -> str:
        return f"/Shared/{self.catalog}/chain_{self.ns}"

    def task_sql_path(self, task_key: str) -> str:
        return f"{self.workspace_dir}/{task_key}.sql"


def tables(n: ChainNames) -> list[str]:
    """Unit-owned tables only: never a catalog, schema or volume, and never a
    table without the namespace suffix."""
    return [
        f"""CREATE TABLE IF NOT EXISTS {n.bronze} (
              run_id STRING COMMENT 'Databricks job run that landed the file',
              source_file STRING COMMENT 'CUSTBILL extract file name as dropped',
              file_size_bytes BIGINT COMMENT 'Size on the landing volume, the legacy ingest byte count',
              record_kind STRING COMMENT 'HDR, TRL or BODY',
              raw_line STRING COMMENT 'Untouched fixed-width record',
              landed_at TIMESTAMP,
              ns STRING)
            USING DELTA
            COMMENT 'Bronze: CUSTBILL drops landed by the chain ingest task'""",
        f"""CREATE TABLE IF NOT EXISTS {n.silver} (
              source_file STRING,
              cust_id STRING,
              cust_name STRING,
              bill_date_raw STRING COMMENT 'BILL-DATE as YYYYMMDD, unvalidated (record validation is the parse unit)',
              bill_date_iso STRING COMMENT 'YYYY-MM-DD rendering, byte-faithful to the legacy .psv field',
              amount_cents BIGINT COMMENT 'PIC 9(10)V99 implied decimal held as cents',
              currency STRING,
              record_type STRING COMMENT '01=invoice 02=credit',
              run_id STRING,
              parsed_at TIMESTAMP,
              ns STRING)
            USING DELTA
            COMMENT 'Silver: parsed CUSTBILL body records, the .psv replacement'""",
        f"""CREATE TABLE IF NOT EXISTS {n.gold} (
              currency STRING,
              record_type STRING COMMENT 'INVOICE or CREDIT, as the legacy spreadsheet labelled it',
              record_count BIGINT,
              total_amount_cents BIGINT,
              run_id STRING,
              computed_at TIMESTAMP,
              ns STRING)
            USING DELTA
            COMMENT 'Gold: finance billing totals, the finance_excel_report .xls replacement'""",
        f"""CREATE TABLE IF NOT EXISTS {n.ledger} (
              run_id STRING,
              task_key STRING,
              status STRING COMMENT 'succeeded | failed',
              detail STRING,
              row_count BIGINT,
              recorded_at TIMESTAMP,
              ns STRING)
            USING DELTA
            COMMENT 'Run ledger for the CUSTBILL chain: what the legacy run_all.log never recorded'""",
    ]


def _ledger_insert(
    n: ChainNames,
    task_key: str,
    status: str,
    detail: str,
    row_count: str,
    ns_literal: str,
) -> str:
    return f"""INSERT INTO {n.ledger}
    SELECT :run_id AS run_id, '{task_key}' AS task_key, '{status}' AS status,
           {detail} AS detail, {row_count} AS row_count,
           current_timestamp() AS recorded_at, {ns_literal} AS ns"""


def validate_params(n: ChainNames) -> str:
    """Mandatory run parameters: an empty or foreign namespace fails the run
    rather than defaulting to a path or table that belongs to another slice."""
    return f"""-- chain task: validate_params (contract: run parameters are mandatory)
SELECT CASE
         WHEN coalesce(:ns, '') = '' OR coalesce(:run_id, '') = ''
           THEN raise_error('chain run parameters ns and run_id are mandatory; refusing to default')
         WHEN :ns <> '{n.ns}'
           THEN raise_error(concat('this chain deployment is bound to namespace {n.ns} but was run with ns=', :ns))
         ELSE concat('parameters ok: ns=', :ns, ' run_id=', :run_id)
       END AS param_check"""


def ingest(n: ChainNames) -> str:
    """Replaces sftp_ingest_poll.ksh: no size-settle sleep, no silent `cp ||
    true`, and a file is landed exactly once (anti-join on source_file) instead
    of being copied, archived and deleted by three unchecked commands."""
    new_files = f"""
      SELECT
        regexp_extract(file_path, '([^/]+)$', 1) AS source_file,
        file_size, value
      FROM (
        SELECT value, _metadata.file_path AS file_path, _metadata.file_size AS file_size
        FROM read_files('{n.drop_dir}', format => 'text', schema => 'value STRING')
      )
      WHERE regexp_extract(file_path, '([^/]+)$', 1) LIKE 'CUSTBILL%.dat'
        AND regexp_extract(file_path, '([^/]+)$', 1) NOT IN (
              SELECT source_file FROM {n.bronze} GROUP BY source_file)"""
    return f"""-- chain task: ingest (replaces etl/legacy-extra/jobs/sftp_ingest_poll.ksh)
INSERT INTO {n.bronze}
SELECT
  :run_id AS run_id,
  source_file,
  file_size AS file_size_bytes,
  CASE WHEN startswith(value, 'HDR') THEN 'HDR'
       WHEN startswith(value, 'TRL') THEN 'TRL'
       ELSE 'BODY' END AS record_kind,
  value AS raw_line,
  current_timestamp() AS landed_at,
  :ns AS ns
FROM ({new_files});

{
        _ledger_insert(
            n,
            "ingest",
            "succeeded",
            "concat('landed files=', cast(count(DISTINCT source_file) AS STRING),"
            " ' lines=', cast(count(*) AS STRING),"
            " ' bytes=', cast(coalesce((SELECT sum(size) FROM (SELECT max(file_size_bytes) AS size"
            f" FROM {n.bronze} WHERE run_id = :run_id GROUP BY source_file)), 0) AS STRING))",
            "count(*)",
            ":ns",
        )
    }
  FROM {n.bronze} WHERE run_id = :run_id"""


def parse(n: ChainNames) -> str:
    """Replaces parse_custbill_fixedwidth.sh: the trailer count the legacy job
    logged and ignored (ETL-0187, 2011) is now a gate, a non-numeric BILL-AMT
    fails the run instead of landing NULL, and the work set is files in bronze
    that are not yet in silver rather than whatever happened to be on disk."""
    pending = f"""
      SELECT source_file FROM {n.bronze}
      WHERE source_file NOT IN (SELECT source_file FROM {n.silver} GROUP BY source_file)
      GROUP BY source_file"""
    trailer_check = f"""
      SELECT b.source_file,
             count_if(b.record_kind = 'BODY') AS body_count,
             max(CASE WHEN b.record_kind = 'TRL'
                      THEN cast(substr(b.raw_line, 4, 10) AS BIGINT) END) AS trailer_count
      FROM {n.bronze} b
      WHERE b.source_file IN ({pending})
      GROUP BY b.source_file"""
    bad_amounts = f"""
      SELECT source_file, count(*) AS bad_rows
      FROM {n.bronze}
      WHERE record_kind = 'BODY'
        AND source_file IN ({pending})
        AND NOT substr(raw_line, 49, 12) RLIKE '^[0-9]{{12}}$'
      GROUP BY source_file"""
    return f"""-- chain task: parse (replaces etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh)
-- gate 1: trailer record count must match the body count of every pending file
SELECT CASE
         WHEN coalesce(:run_id, '') = '' THEN raise_error('run_id is mandatory')
         WHEN (SELECT count(*) FROM ({trailer_check}) t
               WHERE t.trailer_count IS NULL OR t.trailer_count <> t.body_count) > 0
           THEN raise_error(concat('trailer count mismatch, refusing to parse: ',
                (SELECT concat_ws('; ', collect_list(concat(t.source_file,
                        ' body=', cast(t.body_count AS STRING),
                        ' trailer=', coalesce(cast(t.trailer_count AS STRING), 'missing'))))
                 FROM ({trailer_check}) t
                 WHERE t.trailer_count IS NULL OR t.trailer_count <> t.body_count)))
         ELSE 'trailer counts reconciled' END AS trailer_gate;

-- gate 2: BILL-AMT must be 12 numeric digits; a bad amount fails the run and
-- never becomes a NULL that totals as zero
SELECT CASE
         WHEN (SELECT count(*) FROM ({bad_amounts})) > 0
           THEN raise_error(concat('non-numeric BILL-AMT, refusing to parse: ',
                (SELECT concat_ws('; ', collect_list(concat(source_file, ' rows=',
                        cast(bad_rows AS STRING)))) FROM ({bad_amounts}))))
         ELSE 'amounts numeric' END AS amount_gate;

INSERT INTO {n.silver}
SELECT
  source_file,
  trim(substr(raw_line, 1, 10)) AS cust_id,
  trim(substr(raw_line, 11, 30)) AS cust_name,
  substr(raw_line, 41, 8) AS bill_date_raw,
  concat_ws('-', substr(raw_line, 41, 4), substr(raw_line, 45, 2), substr(raw_line, 47, 2)) AS bill_date_iso,
  cast(substr(raw_line, 49, 12) AS BIGINT) AS amount_cents,
  trim(substr(raw_line, 61, 3)) AS currency,
  substr(raw_line, 64, 2) AS record_type,
  :run_id AS run_id,
  current_timestamp() AS parsed_at,
  :ns AS ns
FROM {n.bronze}
WHERE record_kind = 'BODY' AND source_file IN ({pending});

{
        _ledger_insert(
            n,
            "parse",
            "succeeded",
            "concat('parsed rows=', cast(count(*) AS STRING),"
            " ' files=', cast(count(DISTINCT source_file) AS STRING))",
            "count(*)",
            ":ns",
        )
    }
  FROM {n.silver} WHERE run_id = :run_id"""


def finance(n: ChainNames) -> str:
    """Replaces finance_excel_report.pl: a full recompute of the aggregate from
    silver (so a rerun is byte-identical rather than appending), integer cents
    instead of floating-point accumulation, and no sendmail path that silently
    does nothing."""
    return f"""-- chain task: finance (replaces etl/legacy-extra/jobs/finance_excel_report.pl)
INSERT OVERWRITE {n.gold}
SELECT
  currency,
  CASE record_type WHEN '01' THEN 'INVOICE' WHEN '02' THEN 'CREDIT'
       ELSE concat('UNKNOWN(', record_type, ')') END AS record_type,
  count(*) AS record_count,
  sum(amount_cents) AS total_amount_cents,
  :run_id AS run_id,
  current_timestamp() AS computed_at,
  :ns AS ns
FROM {n.silver}
GROUP BY currency, record_type;

{
        _ledger_insert(
            n,
            "finance",
            "succeeded",
            "concat('gold groups=', cast(count(*) AS STRING),"
            " ' records=', cast(coalesce(sum(record_count), 0) AS STRING),"
            " ' cents=', cast(coalesce(sum(total_amount_cents), 0) AS STRING))",
            "count(*)",
            ":ns",
        )
    }
  FROM {n.gold}"""


def chain_complete(n: ChainNames) -> str:
    """The honest replacement for `run_all done (probably)`: this only runs when
    every upstream task succeeded."""
    return f"""-- chain task: chain_complete (runs only on ALL_SUCCESS)
{
        _ledger_insert(
            n,
            "chain",
            "succeeded",
            "concat('chain complete: silver rows=', cast((SELECT count(*) FROM "
            + n.silver
            + ") AS STRING),"
            " ' gold groups=', cast((SELECT count(*) FROM " + n.gold + ") AS STRING))",
            f"(SELECT count(*) FROM {n.silver})",
            ":ns",
        )
    }"""


def chain_failed(n: ChainNames) -> str:
    """Runs when any upstream task failed, so a failed stage is observable
    without reading /var/log/etl."""
    return f"""-- chain task: chain_failed (runs only on AT_LEAST_ONE_FAILED)
{
        _ledger_insert(
            n,
            "chain",
            "failed",
            "concat('chain failed: at least one task failed in run ', :run_id,"
            " '; downstream tasks were not run')",
            "cast(NULL AS BIGINT)",
            ":ns",
        )
    }"""


TASK_SQL = {
    "validate_params": validate_params,
    "ingest": ingest,
    "parse": parse,
    "finance": finance,
    "chain_complete": chain_complete,
    "chain_failed": chain_failed,
}


def gold_export_csv_query(n: ChainNames) -> str:
    """The finance export, rendered in exactly the legacy CSV column order and
    row order (the legacy report sorts on "<ccy>|<rec-type>", so INVOICE (01)
    precedes CREDIT (02)) so its bytes can be compared to the legacy artifact."""
    return f"""SELECT currency, record_type, record_count,
       format_number(total_amount_cents / 100.0, '0.00') AS total_amount
FROM {n.gold}
ORDER BY currency,
         CASE record_type WHEN 'INVOICE' THEN '01' WHEN 'CREDIT' THEN '02' ELSE record_type END"""
