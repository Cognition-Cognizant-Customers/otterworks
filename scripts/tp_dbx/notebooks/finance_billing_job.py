# Databricks notebook source
"""Converted finance billing report job — replaces etl/legacy-extra/jobs/finance_excel_report.pl.

Medallion path for one namespace slice: the legacy parsed `.psv` drops land on the
bronze volume, bronze holds them byte-preserved, silver types and validates them,
gold aggregates currency x record-type, and the finance export is written to the
gold exports volume and then *verified* — the run fails when delivery did not
happen, which is what the legacy sendmail pipe never did.

Deficiencies retired versus the Perl original:
  * CSV renamed `.xls` — the artifact is CSV and its name must say `.csv`.
  * `|/usr/sbin/sendmail` silently no-opping — delivery is read back and checked.
  * hard-coded personal recipient — the destination is a job parameter.
  * `UNKNOWN(rt)` buckets and skipped unreadable files — both fail the run.

This file is uploaded verbatim as the job's notebook; everything above `main()` is
importable so the recon harness and the unit tests exercise the same code the job
runs.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

HEADER = "Currency,RecordType,RecordCount,TotalAmount"
RECORD_TYPE_NAMES = {"01": "INVOICE", "02": "CREDIT"}
EXPORT_NAME = "finance_billing.csv"
EMPTY_EXPORT_NAME = "finance_billing.empty.csv"
PSV_FIELD_COUNT = 6


class DeliveryError(RuntimeError):
    """Delivery could not be verified after the run."""


@dataclass(frozen=True)
class Names:
    catalog: str = "ow_tp"
    ns: str = "cnvfinance"

    @property
    def landing(self) -> str:
        return f"/Volumes/{self.catalog}/bronze/landing/{self.ns}"

    @property
    def exports(self) -> str:
        return f"/Volumes/{self.catalog}/gold/exports/{self.ns}"

    @property
    def bronze(self) -> str:
        return f"{self.catalog}.bronze.custbill_finance_raw_{self.ns}"

    @property
    def silver(self) -> str:
        return f"{self.catalog}.silver.custbill_finance_records_{self.ns}"

    @property
    def gold(self) -> str:
        return f"{self.catalog}.gold.custbill_billing_summary_{self.ns}"

    @property
    def audit(self) -> str:
        return f"{self.catalog}.ops.finance_delivery_audit_{self.ns}"


def require_ns(ns: str) -> str:
    if not ns or not all("a" <= ch <= "z" or "0" <= ch <= "9" or ch == "_" for ch in ns) or len(ns) > 24:
        raise ValueError(f"namespace must be lowercase [a-z0-9_] and at most 24 chars: {ns!r}")
    return ns


def require_ident(value: str, label: str) -> str:
    if not value or not all(
        "A" <= ch <= "Z" or "a" <= ch <= "z" or "0" <= ch <= "9" or ch == "_" for ch in value
    ):
        raise ValueError(f"{label} must match [A-Za-z0-9_]+: {value!r}")
    return value


def check_export_name(name: str) -> str:
    """The legacy job wrote CSV bytes to a `.xls` name; the content and the
    extension must agree, so a non-CSV name is a hard failure, not a rename.

    The name also reaches the audit INSERT as part of a SQL literal, so it is
    restricted to an inert alphabet instead of being escaped."""
    if "/" in name or name.startswith("."):
        raise ValueError(f"export name must be a bare file name: {name!r}")
    if not all(
        "A" <= ch <= "Z"
        or "a" <= ch <= "z"
        or "0" <= ch <= "9"
        or ch in "._-"
        for ch in name
    ):
        raise ValueError(f"export name must match [A-Za-z0-9._-]+: {name!r}")
    if not name.endswith(".csv"):
        raise ValueError(
            f"mislabelled_artifact_type: this job emits CSV bytes, so {name!r} would "
            "declare a type the content does not have (the legacy .xls rename)"
        )
    return name


def require_run_id(run_id: str) -> str:
    """The run id reaches an INSERT as a literal, and a job parameter can be
    overridden at run-now time, so anything outside the platform's own run-id
    alphabet is refused instead of escaped."""
    run_id = str(run_id)
    if not run_id or not all(
        "A" <= ch <= "Z"
        or "a" <= ch <= "z"
        or "0" <= ch <= "9"
        or ch in "_-"
        for ch in run_id
    ):
        raise ValueError(f"run_id must match [A-Za-z0-9_-]+: {run_id!r}")
    return run_id


def cents_to_amount(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def render_export(rows) -> bytes:
    """Render the finance export exactly as the legacy report did: header, then one
    line per currency x record-type sorted by currency then record-type code,
    amounts to the cent. UTF-8, no BOM, `\\n` line endings.

    rows: iterable of (currency, record_type, record_count, total_amount_cents).
    """
    lines = [HEADER]
    for currency, record_type, record_count, total_cents in sorted(rows, key=lambda r: (r[0], r[1])):
        name = RECORD_TYPE_NAMES.get(record_type)
        if name is None:
            raise ValueError(
                f"unmapped record_type {record_type!r}: refusing to emit the legacy "
                "UNKNOWN(rt) bucket"
            )
        lines.append(f"{currency},{name},{int(record_count)},{cents_to_amount(total_cents)}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def export_row_count(payload: bytes) -> int:
    text = payload.decode("utf-8")
    return max(len([line for line in text.split("\n") if line]) - 1, 0)


# --- SQL ---------------------------------------------------------------------
def ddl(n: Names) -> list[str]:
    """Only this namespace's own tables — no catalog, schema or volume DDL, and
    nothing without the namespace suffix."""
    return [
        f"""CREATE TABLE IF NOT EXISTS {n.bronze} (
              source_file STRING COMMENT 'Parsed CUSTBILL .psv file as landed',
              raw_line STRING COMMENT 'Untouched pipe-delimited record',
              ingested_at TIMESTAMP)
            USING DELTA
            COMMENT 'Bronze: byte-preserved parsed CUSTBILL records for the finance aggregate'""",
        f"""CREATE TABLE IF NOT EXISTS {n.silver} (
              cust_id STRING, cust_name STRING, bill_date DATE,
              amount_cents BIGINT COMMENT 'Billed amount held as cents, never as a float',
              currency STRING, record_type STRING COMMENT '01=invoice 02=credit',
              source_file STRING, ingested_at TIMESTAMP)
            USING DELTA
            COMMENT 'Silver: validated billing records feeding the finance aggregate'""",
        f"""CREATE TABLE IF NOT EXISTS {n.gold} (
              ns STRING, currency STRING, record_type STRING,
              record_type_name STRING COMMENT 'INVOICE or CREDIT, never an UNKNOWN bucket',
              record_count BIGINT, total_amount_cents BIGINT, built_at TIMESTAMP)
            USING DELTA
            COMMENT 'Gold: finance billing summary, the finance_excel_report.pl replacement'""",
        f"""CREATE TABLE IF NOT EXISTS {n.audit} (
              run_id STRING, delivered_at TIMESTAMP, export_path STRING,
              byte_size BIGINT, row_count BIGINT, sha256 STRING,
              input_row_count BIGINT)
            USING DELTA
            COMMENT 'Ops: verified deliveries of the finance export (replaces the silent sendmail pipe)'""",
    ]


def load_bronze(n: Names, input_dir: str, has_files: bool) -> str:
    if not has_files:
        # empty-input semantics: an empty batch must leave an auditable empty bronze
        # rather than fail on schema inference
        return f"""INSERT OVERWRITE {n.bronze}
                   SELECT CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS TIMESTAMP)
                   WHERE false"""
    return f"""INSERT OVERWRITE {n.bronze}
               SELECT _metadata.file_name AS source_file, value AS raw_line, current_timestamp()
               FROM read_files('{input_dir}', format => 'text', schema => 'value STRING')"""


def validation_query(n: Names) -> str:
    """Every way a record can be wrong, as rows. The legacy chain passed all of
    these through silently; here any row returned fails the run.

    Missing or NULL values may never fail open into a plausible-looking row:
    an unparseable amount, date, currency or record type is a rejection, not a
    zero and not an UNKNOWN bucket. A NULL line is itself a rejection rather than
    a row quietly filtered out of the batch; only genuinely blank lines are
    skipped, as the legacy reader skipped them.
    """
    return f"""
      WITH parsed AS (
        SELECT source_file, raw_line, split(raw_line, '\\\\|') AS f
        FROM {n.bronze}
        WHERE raw_line IS NULL OR length(trim(raw_line)) > 0
      )
      SELECT source_file, raw_line,
        CASE
          WHEN raw_line IS NULL THEN 'null_raw_line'
          WHEN size(f) <> {PSV_FIELD_COUNT} THEN 'field_count'
          WHEN trim(f[0]) = '' THEN 'missing_cust_id'
          WHEN try_cast(f[2] AS DATE) IS NULL THEN 'invalid_bill_date'
          WHEN try_cast(f[3] AS DECIMAL(18,2)) IS NULL THEN 'nonnumeric_amount'
          WHEN trim(f[4]) = '' THEN 'missing_currency'
          WHEN NOT trim(f[5]) IN ('01', '02') THEN 'unknown_record_type'
        END AS reason
      FROM parsed
      WHERE raw_line IS NULL
         OR size(f) <> {PSV_FIELD_COUNT}
         OR trim(f[0]) = ''
         OR try_cast(f[2] AS DATE) IS NULL
         OR try_cast(f[3] AS DECIMAL(18,2)) IS NULL
         OR trim(f[4]) = ''
         OR NOT trim(f[5]) IN ('01', '02')
      ORDER BY source_file, raw_line
      LIMIT 50"""


def build_silver(n: Names) -> str:
    return f"""INSERT OVERWRITE {n.silver}
      WITH parsed AS (
        SELECT source_file, ingested_at, split(raw_line, '\\\\|') AS f
        FROM {n.bronze}
        WHERE raw_line IS NOT NULL AND length(trim(raw_line)) > 0
      )
      SELECT
        trim(f[0]) AS cust_id,
        f[1] AS cust_name,
        CAST(f[2] AS DATE) AS bill_date,
        CAST(round(CAST(f[3] AS DECIMAL(18,2)) * 100) AS BIGINT) AS amount_cents,
        trim(f[4]) AS currency,
        trim(f[5]) AS record_type,
        source_file, ingested_at
      FROM parsed"""


def build_gold(n: Names) -> str:
    return f"""INSERT OVERWRITE {n.gold}
      SELECT '{n.ns}' AS ns, currency, record_type,
             CASE record_type WHEN '01' THEN 'INVOICE' WHEN '02' THEN 'CREDIT' END AS record_type_name,
             count(*) AS record_count, sum(amount_cents) AS total_amount_cents,
             current_timestamp() AS built_at
      FROM {n.silver}
      GROUP BY currency, record_type"""


def gold_rows_query(n: Names) -> str:
    return f"""SELECT currency, record_type, record_count, total_amount_cents
               FROM {n.gold} WHERE ns = '{n.ns}'
               ORDER BY currency, record_type"""


# --- run ---------------------------------------------------------------------
def deliver(payload: bytes, directory: str, name: str, skip_write: bool = False) -> dict:
    """Write the export and then prove *this run's* bytes landed. `skip_write`
    exercises the planted silent-delivery-noop anomaly: verification must fail
    rather than the run reporting success.

    The export is byte-identical across reruns, so reading the destination back
    cannot on its own tell a fresh write from last run's leftovers. This run's
    bytes are therefore written to a scratch name and verified there, and only a
    verified scratch file is moved onto the export path: a delivery that did
    nothing has nothing to move, and the previously delivered report survives a
    failed run instead of being destroyed by it.
    """
    check_export_name(name)
    path = f"{directory}/{name}"
    staged = f"{directory}/.{name}.staging"
    os.makedirs(directory, exist_ok=True)
    if os.path.exists(staged):
        os.remove(staged)
    if not skip_write:
        with open(staged, "wb") as handle:
            handle.write(payload)
    if not os.path.exists(staged):
        raise DeliveryError(f"silent_delivery_noop: nothing was delivered for {path}")
    with open(staged, "rb") as handle:  # a fresh handle: never trust the write buffer
        landed = handle.read()
    if landed != payload:
        os.remove(staged)
        raise DeliveryError(
            f"delivery mismatch at {path}: {len(landed)} bytes landed, {len(payload)} expected"
        )
    os.replace(staged, path)
    with open(path, "rb") as handle:
        landed = handle.read()
    if landed != payload:
        raise DeliveryError(
            f"delivery mismatch at {path}: {len(landed)} bytes landed, {len(payload)} expected"
        )
    return {
        "path": path,
        "byte_size": len(landed),
        "row_count": export_row_count(landed),
        "sha256": hashlib.sha256(landed).hexdigest(),
    }


def batch_files(input_dir: str) -> list[str]:
    """Every file `read_files()` would ingest, as paths relative to the batch
    directory — the emptiness decision has to be made over the same set that is
    actually read, recursively and without a name filter, or a batch of files
    this job does not recognise looks exactly like no batch at all."""
    if not os.path.isdir(input_dir):
        return []
    found = []
    for directory, _, names in os.walk(input_dir):
        relative = os.path.relpath(directory, input_dir)
        found.extend(name if relative == "." else f"{relative}/{name}" for name in names)
    return sorted(found)


def require_input_dir(input_dir: str) -> str:
    if not os.path.isdir(input_dir):
        raise ValueError(f"missing_input_dir: {input_dir}")
    return input_dir


def unrecognised_inputs(files) -> list[str]:
    """The legacy reader took `CUSTBILL*.psv` in the batch directory itself.
    Anything else present is a rejection: an unread file must never be reported
    as an empty month."""
    return [
        f
        for f in files
        if "/" in f or not (f.startswith("CUSTBILL") and f.endswith(".psv"))
    ]


def run(spark, dbutils, params: dict) -> dict:
    n = Names(catalog=require_ident(params["catalog"], "catalog"), ns=require_ns(params["ns"]))
    input_dir = require_input_dir(
        f"{n.landing}/{require_ident(params['input_subdir'], 'input_subdir')}"
    )
    probe = params.get("delivery_probe", "off")
    if probe not in {"off", "skip_write"}:
        raise ValueError(f"delivery_probe must be off or skip_write: {probe!r}")
    # both the artifact name and the run id are checked up front: a misconfigured
    # run must fail the same way whether or not the batch turns out to have data
    requested_export = check_export_name(params["export_name"])
    run_id = require_run_id(params["run_id"])

    for statement in ddl(n):
        spark.sql(statement)

    files = batch_files(input_dir)
    unrecognised = unrecognised_inputs(files)
    if unrecognised:
        raise ValueError(
            f"unrecognised_input in {input_dir}: {', '.join(unrecognised)} — refusing to "
            "report an empty month for a batch this job cannot read"
        )
    spark.sql(load_bronze(n, input_dir, bool(files)))

    violations = spark.sql(validation_query(n)).collect()
    if violations:
        detail = "; ".join(f"{row['source_file']}:{row['reason']}" for row in violations)
        raise ValueError(f"{len(violations)} invalid source record(s), run rejected: {detail}")

    spark.sql(build_silver(n))
    spark.sql(build_gold(n))

    rows = [
        (row["currency"], row["record_type"], row["record_count"], row["total_amount_cents"])
        for row in spark.sql(gold_rows_query(n)).collect()
    ]
    payload = render_export(rows)
    # empty input never truncates the last good export in place
    name = requested_export if rows else EMPTY_EXPORT_NAME
    delivered = deliver(payload, n.exports, name, skip_write=probe == "skip_write")

    input_rows = spark.sql(f"SELECT count(*) AS c FROM {n.silver}").collect()[0]["c"]
    spark.sql(
        f"""INSERT INTO {n.audit} VALUES (
              '{run_id}', current_timestamp(), '{delivered['path']}',
              {delivered['byte_size']}, {delivered['row_count']},
              '{delivered['sha256']}', {input_rows})"""
    )
    summary = dict(delivered, input_files=files, gold_groups=len(rows), silver_rows=int(input_rows))
    print(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    params = {
        "ns": dbutils.widgets.get("ns"),  # noqa: F821 — notebook-injected
        "catalog": dbutils.widgets.get("catalog"),  # noqa: F821
        "input_subdir": dbutils.widgets.get("input_subdir"),  # noqa: F821
        "export_name": dbutils.widgets.get("export_name"),  # noqa: F821
        "delivery_probe": dbutils.widgets.get("delivery_probe"),  # noqa: F821
        "run_id": dbutils.widgets.get("run_id"),  # noqa: F821
    }
    run(spark, dbutils, params)  # noqa: F821


if __name__ == "__main__":
    for widget, default in (
        ("ns", "cnvfinance"), ("catalog", "ow_tp"), ("input_subdir", "parsed"),
        ("export_name", EXPORT_NAME), ("delivery_probe", "off"), ("run_id", "manual"),
    ):
        try:
            dbutils.widgets.text(widget, default)
        except NameError:  # imported outside Databricks (unit tests, recon harness)
            break
    else:
        main()
