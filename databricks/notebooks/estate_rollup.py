# Databricks notebook source
"""Converted `etl/legacy-extra/run_all.sh` gold layer — the `ow_tp_estate_rollup` task.

The legacy estate had no estate-level view of whether a night's batch reconciled. `run_all.sh`
ran three stages with `2>/dev/null || true` between `sleep 600`s, so a stage that produced
nothing looked exactly like a stage that produced everything, and the five Python crons in
`crontab` reported to nobody at all.

What replaces it here:

* one row per (`ns`, `run_date`, `unit`) in `gold.estate_daily_rollup`, whose `recon_result`
  is *derived* from the recon evidence each unit persists (`silver.custbill_file_recon.recon_ok`,
  `gold.audit_archive_manifest.verified`, `gold.search_reindex_summary.counts_match`,
  `gold.storage_cleanup_savings.metadata_read_ok`, `gold.user_activity_run_log.status`, and the
  silver→gold parity identity of each unit) — never hand-entered, and never a value this unit
  invents about itself;
* `gold.estate_anomalies`, which surfaces the defects the seed generator planted and that the
  legacy estate surfaced nowhere: each row carries the offending identifier *and* the
  `bronze.seed_anomaly_manifest` entry it traces to;
* both loads are `INSERT ... REPLACE WHERE`, so a re-run replaces its own slice instead of
  appending a second copy — the legacy estate had no idempotency at any layer;
* a unit that does not reconcile raises `EstateNotReconciled` at the end of the run, so the
  task (and therefore the orchestrator run) fails. The rows are written *before* the raise:
  the failure is visible in the job and the evidence for it is queryable in gold.

`gold.estate_daily_rollup` reads only silver/gold tables produced by the per-script wave, per
the contract. The anomaly detectors additionally read `bronze.file_metadata_raw` (the seeded
DynamoDB metadata, as landed by the storage-cleanup unit) and `bronze.seed_anomaly_manifest`,
which is what the contract's "seeded data / seed manifest" means; the rollup itself does not.

The module is deliberately runner-agnostic: `run_pipeline` takes `execute`/`scalar`, so the
exact statement text that runs as this notebook task (`spark.sql`) also runs through
`scripts/tp_databricks/run_estate_rollup.py` on the pre-existing serverless SQL warehouse,
which is what the recon evidence is produced with.
"""

from __future__ import annotations

import re
from typing import Callable

DEFAULT_CATALOG = "ow_tp"
# `ns`, `catalog`, `run_date` and `job_run_id` arrive as job parameters and are interpolated
# into statements that include `INSERT ... REPLACE WHERE ns = '<ns>'`, which deletes the slice
# it replaces. Every one of them is validated before a statement is built, so a value carrying
# a quote or a statement terminator cannot widen that predicate.
NS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}$")
RUN_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
JOB_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{0,64}$")
RECON_RESULTS = ("green", "red", "blocked")
# Anomaly kinds a converted unit's tables can actually surface. Every other kind the manifest
# declares is recorded as a coverage gap rather than dropped, so "no anomaly row" can never be
# confused with "no anomaly".
DETECTED_ANOMALY_KINDS = ("orphaned_metadata", "missing_hours")


class UnitEvidenceMissing(RuntimeError):
    """Raised by the `unit_gate` stage when a unit published no evidence to reconcile.

    In the shipped orchestrator, ordering is a `run_job_task` edge: the rollup runs because
    the unit's own job succeeded. The gate is the same assertion made from the data side —
    used by the local orchestrator mirror (`scripts/tp_databricks/run_estate_dev.py`), whose
    upstream tasks cannot be `run_job_task` edges in a workspace where the unit jobs are not
    applied. A unit that published nothing fails its task instead of being rolled up as a
    zero, which is what `run_all.sh` did with `2>/dev/null || true`.
    """


class EstateNotReconciled(RuntimeError):
    """Raised when a unit's rollup row is not green.

    This is the legacy defect being retired: `run_all.sh` ended `|| true` and exited 0 no
    matter what its stages did. Here the rows are written and then the task fails, so the
    orchestrator run fails with the evidence already in gold.
    """


def validate_ns(ns: str) -> str:
    if not NS_PATTERN.match(ns or ""):
        raise ValueError(f"invalid ns {ns!r}: expected {NS_PATTERN.pattern}")
    return ns


def validate_identifier(name: str, what: str = "identifier") -> str:
    if not IDENTIFIER_PATTERN.match(name or ""):
        raise ValueError(f"invalid {what} {name!r}: expected {IDENTIFIER_PATTERN.pattern}")
    return name


def validate_run_date(run_date: str) -> str:
    if not RUN_DATE_PATTERN.match(run_date or ""):
        raise ValueError(f"invalid run_date {run_date!r}: expected YYYY-MM-DD")
    return run_date


def validate_job_run_id(job_run_id: str) -> str:
    if not JOB_RUN_ID_PATTERN.match(job_run_id or ""):
        raise ValueError(f"invalid job_run_id {job_run_id!r}: expected {JOB_RUN_ID_PATTERN.pattern}")
    return job_run_id


def _split_sql(text: str) -> list[str]:
    """Split a DDL file on statement-terminating semicolons only.

    Column comments contain semicolons inside quoted literals, and `--` comments can too, so
    a plain `split(';')` would cut statements in half.
    """
    statements, current, in_string, in_comment = [], [], False, False
    index = 0
    while index < len(text):
        char = text[index]
        if in_comment:
            in_comment = char != "\n"
        elif in_string:
            if char == "'":
                if text[index + 1 : index + 2] == "'":  # escaped quote
                    current.append(char)
                    index += 1
                else:
                    in_string = False
        elif char == "'":
            in_string = True
        elif text[index : index + 2] == "--":
            in_comment = True
            index += 1
            current.append(" ")
            continue
        elif char == ";":
            statements.append("".join(current))
            current = []
            index += 1
            continue
        if not in_comment:
            current.append(char)
        index += 1
    statements.append("".join(current))
    return [statement.strip() for statement in statements if statement.strip()]


def ddl_statements(ddl_text: str, catalog: str = DEFAULT_CATALOG) -> list[dict]:
    """Split databricks/sql/estate_rollup_tables.sql into executable statements.

    The file writes `${catalog}`, substituted here, so the reviewed statement text is the
    text both the job task and the local runner execute, in whichever ow_tp catalog the
    caller was configured for. A SQL task cannot set a catalog, which is why the catalog is
    resolved in the notebook rather than left to the session default.
    """
    validate_identifier(catalog, "catalog")
    qualified = ddl_text.replace("${catalog}", catalog)
    return [
        {"name": f"ddl_{position}", "sql": body}
        for position, body in enumerate(_split_sql(qualified), start=1)
    ]


def _read_ddl(ddl_path: str) -> str | None:
    """The deployed DDL file's text, or None when no path was supplied."""
    if not ddl_path:
        return None
    with open(ddl_path, encoding="utf-8") as handle:
        return handle.read()


def _quoted(value: str) -> str:
    """A SQL string literal for an already-validated value."""
    return "'" + value.replace("'", "''") + "'"


def unit_specs(catalog: str = DEFAULT_CATALOG, ns: str = "demo") -> list[dict]:
    """One spec per converted unit: where its numbers come from and what green means.

    `measures` are scalar subqueries over that unit's own silver/gold tables. `result` and
    `detail` are SQL over those measures: `recon_result` is therefore computed from the
    evidence tables on every run, and there is nowhere for a hand-entered verdict to enter.

    `rows_in` is not the same unit of measure for every unit — the ingest unit counts files,
    the storage unit counts objects, the rest count records — so each spec states its unit of
    measure in `recon_detail` rather than leaving the number to be misread.

    The two sides of a parity identity must describe the *same* run. The units differ in how
    they persist history: `finance_billing_summary`, `search_reindex_summary`,
    `user_activity_report` and `audit_archive_manifest` keep one slice per business date and add
    to it, while the silver tables they are compared against are replaced per namespace on every
    run. Aggregating those gold tables over the whole namespace therefore holds only until a unit
    has published a second slice, after which a healthy unit reports red. Each accumulating gold
    table is scoped to its own latest slice with `_slice`, which is also why `storage_cleanup`
    takes the latest row by `generated_at`. `analytics_daily_summary` is deliberately *not*
    scoped: it is written `REPLACE WHERE ns`, so its several `summary_date` rows are all one run
    and are summed against the whole silver slice.
    """
    validate_ns(ns)
    validate_identifier(catalog, "catalog")
    n = _quoted(ns)

    def _slice(table: str, column: str) -> str:
        """Predicate restricting an accumulating table to the latest slice it holds."""
        return f"ns = {n} AND {column} = (SELECT max({column}) FROM {catalog}.{table} WHERE ns = {n})"

    finance_slice = _slice("gold.finance_billing_summary", "report_date")
    search_slice = _slice("gold.search_reindex_summary", "run_date")
    activity_slice = _slice("gold.user_activity_report", "report_date")
    audit_slice = _slice("gold.audit_archive_manifest", "run_date")
    audit_run_date = f"(SELECT max(run_date) FROM {catalog}.gold.audit_archive_manifest WHERE ns = {n})"

    return [
        {
            "unit": "sftp_ingest",
            "legacy_source": "etl/legacy-extra/jobs/sftp_ingest_poll.ksh",
            "language_vintage": "ksh (1998)",
            "measures": {
                # The ingest unit's own outputs are bronze, which this table may not read; the
                # per-file recon row the parse unit writes is the silver record of what the
                # ingest delivered, so the file-level accounting is taken from there.
                "rows_in": f"(SELECT count(*) FROM {catalog}.silver.custbill_file_recon WHERE ns = {n})",
                "rows_out": f"(SELECT count(*) FROM {catalog}.silver.custbill_file_recon WHERE ns = {n} AND recon_ok)",
                "rejected": f"(SELECT count(*) FROM {catalog}.silver.custbill_file_recon WHERE ns = {n} AND NOT recon_ok)",
                "declared": f"(SELECT coalesce(sum(declared_trailer_count), 0) FROM {catalog}.silver.custbill_file_recon WHERE ns = {n})",
                "slice_date": f"(SELECT max(date(reconciled_at)) FROM {catalog}.silver.custbill_file_recon WHERE ns = {n})",
            },
            "result": "CASE WHEN rows_in = 0 THEN 'blocked' WHEN rejected = 0 AND rows_out = rows_in THEN 'green' ELSE 'red' END",
            "detail": (
                "concat('unit=files; identity=files_reconciled=files_landed; ', rows_out, '=', rows_in, "
                "'; trailer_declared_records=', declared, "
                "'; evidence=silver.custbill_file_recon.recon_ok; slice_date=', coalesce(cast(slice_date AS STRING), 'none'), "
                "'; disclosure=the ingest unit writes only bronze, so its file accounting is read from the silver recon table')"
            ),
        },
        {
            "unit": "parse_custbill",
            "legacy_source": "etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh",
            "language_vintage": "bash (2009)",
            "measures": {
                "rows_in": f"(SELECT coalesce(sum(declared_trailer_count), 0) FROM {catalog}.silver.custbill_file_recon WHERE ns = {n})",
                "rows_out": f"(SELECT count(*) FROM {catalog}.silver.custbill_records WHERE ns = {n})",
                "rejected": f"(SELECT count(*) FROM {catalog}.silver.custbill_rejects WHERE ns = {n})",
                "files_failed": f"(SELECT count(*) FROM {catalog}.silver.custbill_file_recon WHERE ns = {n} AND NOT recon_ok)",
                "slice_date": f"(SELECT max(date(reconciled_at)) FROM {catalog}.silver.custbill_file_recon WHERE ns = {n})",
            },
            "result": (
                "CASE WHEN rows_in = 0 THEN 'blocked' "
                "WHEN rows_out + rejected = rows_in AND files_failed = 0 THEN 'green' ELSE 'red' END"
            ),
            "detail": (
                "concat('unit=records; identity=parsed+rejected=trailer_declared; ', rows_out, '+', rejected, '=', rows_in, "
                "'; files_failing_recon=', files_failed, "
                "'; evidence=silver.custbill_file_recon.recon_ok + silver.custbill_records + silver.custbill_rejects"
                "; slice_date=', coalesce(cast(slice_date AS STRING), 'none'))"
            ),
        },
        {
            "unit": "finance_report",
            "legacy_source": "etl/legacy-extra/jobs/finance_excel_report.pl",
            "language_vintage": "perl (2003)",
            "measures": {
                "rows_in": f"(SELECT count(*) FROM {catalog}.silver.custbill_records WHERE ns = {n})",
                "rows_out": f"(SELECT count(*) FROM {catalog}.gold.finance_billing_summary WHERE {finance_slice})",
                "summed": f"(SELECT coalesce(sum(record_count), 0) FROM {catalog}.gold.finance_billing_summary WHERE {finance_slice})",
                "total_amount": f"(SELECT coalesce(sum(total_amount), 0) FROM {catalog}.gold.finance_billing_summary WHERE {finance_slice})",
                "silver_amount": f"(SELECT coalesce(sum(amount), 0) FROM {catalog}.silver.custbill_records WHERE ns = {n})",
                "delivery": (
                    f"(SELECT max(delivery_status) FROM {catalog}.gold.finance_report_delivery WHERE ns = {n})"
                ),
                "slice_date": f"(SELECT max(report_date) FROM {catalog}.gold.finance_billing_summary WHERE ns = {n})",
            },
            "result": (
                "CASE WHEN rows_in = 0 OR rows_out = 0 THEN 'blocked' "
                "WHEN summed = rows_in AND total_amount = silver_amount THEN 'green' ELSE 'red' END"
            ),
            # `rejected` is the parsed records the summary failed to account for: the legacy
            # Perl job dropped a record whose currency field was blank and reported nothing.
            "rejected": "rows_in - summed",
            "detail": (
                "concat('unit=records; identity=sum(record_count)=parsed_records AND sum(total_amount)=sum(silver.amount); ', "
                "summed, '=', rows_in, ' AND ', cast(total_amount AS STRING), '=', cast(silver_amount AS STRING), "
                "'; summary_rows=', rows_out, "
                "'; evidence=gold.finance_billing_summary vs silver.custbill_records"
                "; disclosure=delivery_status=', coalesce(delivery, 'none'), "
                "'; slice_date=', coalesce(cast(slice_date AS STRING), 'none'))"
            ),
        },
        {
            "unit": "analytics_daily",
            "legacy_source": "etl/scripts/analytics_daily.py",
            "language_vintage": "python 2 era (2014)",
            "measures": {
                "rows_in": (
                    f"(SELECT count(*) FROM {catalog}.silver.analytics_events WHERE ns = {n}) + "
                    f"(SELECT count(*) FROM {catalog}.silver.analytics_events_rejects WHERE ns = {n})"
                ),
                "rows_out": f"(SELECT coalesce(sum(event_count), 0) FROM {catalog}.gold.analytics_daily_summary WHERE ns = {n})",
                "rejected": f"(SELECT count(*) FROM {catalog}.silver.analytics_events_rejects WHERE ns = {n})",
                "summary_rows": f"(SELECT count(*) FROM {catalog}.gold.analytics_daily_summary WHERE ns = {n})",
                "slice_date": f"(SELECT max(summary_date) FROM {catalog}.gold.analytics_daily_summary WHERE ns = {n})",
            },
            "result": (
                "CASE WHEN rows_in = 0 THEN 'blocked' "
                "WHEN rows_out + rejected = rows_in THEN 'green' ELSE 'red' END"
            ),
            "detail": (
                "concat('unit=events; identity=gold_event_count+rejected=silver+rejected; ', rows_out, '+', rejected, '=', rows_in, "
                "'; summary_rows=', summary_rows, "
                "'; evidence=gold.analytics_daily_summary vs silver.analytics_events(+rejects)"
                "; slice_date=', coalesce(cast(slice_date AS STRING), 'none'), "
                "'; disclosure=this unit has no committed per-unit recon report on this branch, so the verdict rests on the persisted parity evidence alone')"
            ),
        },
        {
            "unit": "audit_archive",
            "legacy_source": "etl/scripts/audit_archive_weekly.py",
            "language_vintage": "python (2015)",
            "measures": {
                "rows_in": f"(SELECT coalesce(max(candidate_count), 0) FROM {catalog}.gold.audit_archive_manifest WHERE {audit_slice})",
                "rows_out": f"(SELECT coalesce(max(archived_count), 0) FROM {catalog}.gold.audit_archive_manifest WHERE {audit_slice})",
                # The silver archive is a MERGE on (ns, event_id) and so is cumulative across
                # weeks; it is counted for the manifest's own run_date, not over all of history.
                "archived_silver": f"(SELECT count(*) FROM {catalog}.silver.audit_events_archived WHERE ns = {n} AND run_date = {audit_run_date})",
                "deleted": f"(SELECT coalesce(max(deleted_count), 0) FROM {catalog}.gold.audit_archive_manifest WHERE {audit_slice})",
                "verified": f"(SELECT max(verified) FROM {catalog}.gold.audit_archive_manifest WHERE {audit_slice})",
                "slice_date": f"(SELECT max(run_date) FROM {catalog}.gold.audit_archive_manifest WHERE ns = {n})",
            },
            "result": (
                "CASE WHEN rows_in = 0 THEN 'blocked' "
                "WHEN verified AND rows_out = rows_in AND archived_silver = rows_out THEN 'green' ELSE 'red' END"
            ),
            "rejected": "rows_in - rows_out",
            "detail": (
                "concat('unit=events; identity=archived=candidates AND silver_rows=archived; ', rows_out, '=', rows_in, "
                "' AND ', archived_silver, '=', rows_out, "
                "'; deleted_after_verification=', deleted, "
                "'; evidence=gold.audit_archive_manifest.verified"
                "; slice_date=', coalesce(cast(slice_date AS STRING), 'none'), "
                "'; disclosure=weekly unit, so its slice_date legitimately predates the orchestrator run_date')"
            ),
        },
        {
            "unit": "search_reindex",
            "legacy_source": "etl/scripts/search_reindex_weekly.py",
            "language_vintage": "python (2016)",
            "measures": {
                "rows_in": f"(SELECT coalesce(sum(source_count), 0) FROM {catalog}.gold.search_reindex_summary WHERE {search_slice})",
                "rows_out": f"(SELECT coalesce(sum(indexed_count), 0) FROM {catalog}.gold.search_reindex_summary WHERE {search_slice})",
                "silver_docs": f"(SELECT count(*) FROM {catalog}.silver.search_index_documents WHERE ns = {n})",
                # Scoped to the same slice: a historical failed reindex (the unit's own simulated
                # failure run is one) must not hold this unit red for every night afterwards.
                "unmatched": f"(SELECT count(*) FROM {catalog}.gold.search_reindex_summary WHERE {search_slice} AND NOT (counts_match AND swap_completed))",
                "slice_date": f"(SELECT max(run_date) FROM {catalog}.gold.search_reindex_summary WHERE ns = {n})",
            },
            "result": (
                "CASE WHEN rows_in = 0 THEN 'blocked' "
                "WHEN unmatched = 0 AND rows_out = rows_in AND silver_docs = rows_out THEN 'green' ELSE 'red' END"
            ),
            "rejected": "rows_in - rows_out",
            "detail": (
                "concat('unit=documents; identity=indexed=extracted AND silver_rows=indexed; ', rows_out, '=', rows_in, "
                "' AND ', silver_docs, '=', rows_out, "
                "'; entity_types_failing_flags=', unmatched, "
                "'; evidence=gold.search_reindex_summary.counts_match/swap_completed"
                "; slice_date=', coalesce(cast(slice_date AS STRING), 'none'))"
            ),
        },
        {
            "unit": "storage_cleanup",
            "legacy_source": "etl/scripts/storage_cleanup_daily.py",
            "language_vintage": "python 2 era (2014)",
            "measures": {
                # Several scenarios (including the deliberately truncated metadata extract the
                # unit's safety guard was demonstrated with) write a row for the same run_date.
                # The latest row by generated_at is the run this rollup describes; picking the
                # best-looking row instead would be exactly the legacy reporting defect.
                "rows_in": f"(SELECT objects_scanned FROM {catalog}.gold.storage_cleanup_savings WHERE ns = {n} ORDER BY generated_at DESC LIMIT 1)",
                "rows_out": f"(SELECT orphan_count FROM {catalog}.gold.storage_cleanup_savings WHERE ns = {n} ORDER BY generated_at DESC LIMIT 1)",
                "rejected": f"(SELECT quarantined_count FROM {catalog}.gold.storage_cleanup_savings WHERE ns = {n} ORDER BY generated_at DESC LIMIT 1)",
                "metadata_ok": f"(SELECT metadata_read_ok FROM {catalog}.gold.storage_cleanup_savings WHERE ns = {n} ORDER BY generated_at DESC LIMIT 1)",
                "dry_run": f"(SELECT dry_run FROM {catalog}.gold.storage_cleanup_savings WHERE ns = {n} ORDER BY generated_at DESC LIMIT 1)",
                "scenario": f"(SELECT scenario FROM {catalog}.gold.storage_cleanup_savings WHERE ns = {n} ORDER BY generated_at DESC LIMIT 1)",
                "silver_orphans": (
                    f"(SELECT count(*) FROM {catalog}.silver.storage_orphans WHERE ns = {n} "
                    f"AND scenario = (SELECT scenario FROM {catalog}.gold.storage_cleanup_savings WHERE ns = {n} ORDER BY generated_at DESC LIMIT 1))"
                ),
                "slice_date": f"(SELECT max(run_date) FROM {catalog}.gold.storage_cleanup_savings WHERE ns = {n})",
            },
            "result": (
                "CASE WHEN rows_in IS NULL OR rows_in = 0 THEN 'blocked' "
                "WHEN metadata_ok AND silver_orphans = rows_out THEN 'green' ELSE 'red' END"
            ),
            "detail": (
                "concat('unit=objects; identity=silver_orphan_rows=gold_orphan_count; ', silver_orphans, '=', rows_out, "
                "'; objects_scanned=', rows_in, '; metadata_read_ok=', metadata_ok, "
                "'; evidence=gold.storage_cleanup_savings.metadata_read_ok (latest row by generated_at, scenario=', scenario, ')"
                "; slice_date=', coalesce(cast(slice_date AS STRING), 'none'), "
                "'; disclosure=dry_run=', dry_run, ' so nothing was quarantined; this unit has no committed per-unit recon report on this branch')"
            ),
        },
        {
            "unit": "user_activity",
            "legacy_source": "etl/scripts/user_activity_daily.py",
            "language_vintage": "python (2017)",
            "measures": {
                "rows_in": f"(SELECT coalesce(sum(events), 0) FROM {catalog}.silver.user_activity_daily WHERE ns = {n})",
                "rows_out": f"(SELECT coalesce(sum(events), 0) FROM {catalog}.gold.user_activity_report WHERE {activity_slice})",
                "report_rows": f"(SELECT count(*) FROM {catalog}.gold.user_activity_report WHERE {activity_slice})",
                "upstream_fresh": f"(SELECT min(upstream_fresh) FROM {catalog}.gold.user_activity_report WHERE {activity_slice})",
                "last_status": (
                    f"(SELECT status FROM {catalog}.gold.user_activity_run_log WHERE ns = {n} "
                    "AND stage = 'pipeline' ORDER BY run_ts DESC LIMIT 1)"
                ),
                "slice_date": f"(SELECT max(report_date) FROM {catalog}.gold.user_activity_report WHERE ns = {n})",
            },
            "result": (
                "CASE WHEN rows_in = 0 OR report_rows = 0 THEN 'blocked' "
                "WHEN last_status = 'ok' AND upstream_fresh AND rows_out = rows_in THEN 'green' ELSE 'red' END"
            ),
            "rejected": "rows_in - rows_out",
            "detail": (
                "concat('unit=events; identity=gold_report_events=silver_events; ', rows_out, '=', rows_in, "
                "'; report_rows=', report_rows, '; upstream_fresh=', upstream_fresh, "
                "'; evidence=gold.user_activity_run_log latest pipeline status=', coalesce(last_status, 'none'), "
                "'; slice_date=', coalesce(cast(slice_date AS STRING), 'none'))"
            ),
        },
    ]


def gate_query(catalog: str = DEFAULT_CATALOG, ns: str = "demo", unit: str = "", source_table: str | None = None) -> str:
    """Row count of the evidence the named unit must have published for this namespace.

    `source_table` overrides the evidence relation. That is what the failure drill points at
    a table the estate does not have, so the failing upstream task fails for a real reason —
    a missing source — rather than a mocked exception.
    """
    validate_ns(ns)
    validate_identifier(catalog, "catalog")
    specs = {spec["unit"]: spec for spec in unit_specs(catalog, ns)}
    if unit not in specs:
        raise ValueError(f"unknown unit {unit!r}: expected one of {sorted(specs)}")
    if source_table:
        validate_identifier(source_table, "source table")
        return f"SELECT count(*) FROM {source_table} WHERE ns = {_quoted(ns)}"
    return f"SELECT {specs[unit]['measures']['rows_in']} AS rows_in"


def run_unit_gate(
    scalar: Callable[[str], object],
    catalog: str = DEFAULT_CATALOG,
    ns: str = "demo",
    unit: str = "",
    source_table: str | None = None,
    log: Callable[[str], None] = print,
) -> int:
    """Fail unless the unit published evidence for this namespace."""
    rows = int(scalar(gate_query(catalog, ns, unit, source_table)) or 0)
    if rows == 0:
        raise UnitEvidenceMissing(
            f"unit {unit} published no rows for ns={ns} "
            f"(evidence relation: {source_table or 'the unit''s own silver/gold tables'}); "
            "failing the task instead of rolling the unit up as a zero"
        )
    log(f"gate ok: unit={unit} ns={ns} rows={rows}")
    return rows


def _unit_select(spec: dict, ns: str, run_date: str, job_run_id: str) -> str:
    measures = ",\n    ".join(f"{sql} AS {name}" for name, sql in spec["measures"].items())
    rejected = spec.get("rejected", "rejected")
    return f"""
SELECT
  {_quoted(ns)}                       AS ns,
  DATE{_quoted(run_date)}             AS run_date,
  {_quoted(spec['unit'])}             AS unit,
  {_quoted(spec['legacy_source'])}    AS legacy_source,
  {_quoted(spec['language_vintage'])} AS language_vintage,
  coalesce(rows_in, 0)                AS rows_in,
  coalesce(rows_out, 0)               AS rows_out,
  coalesce({rejected}, 0)             AS rejected,
  {spec['result']}                    AS recon_result,
  {spec['detail']}                    AS recon_detail,
  {_quoted(job_run_id)}               AS job_run_id,
  current_timestamp()                 AS updated_at
FROM (
  SELECT
    {measures}
)
""".strip()


def rollup_statement(
    catalog: str = DEFAULT_CATALOG,
    ns: str = "demo",
    run_date: str = "1970-01-01",
    job_run_id: str = "",
) -> str:
    """The single atomic load of `gold.estate_daily_rollup` for one (ns, run_date)."""
    validate_ns(ns)
    validate_identifier(catalog, "catalog")
    validate_run_date(run_date)
    validate_job_run_id(job_run_id)
    selects = [_unit_select(spec, ns, run_date, job_run_id) for spec in unit_specs(catalog, ns)]
    body = "\nUNION ALL\n".join(selects)
    return (
        f"INSERT INTO {catalog}.gold.estate_daily_rollup "
        f"REPLACE WHERE ns = {_quoted(ns)} AND run_date = DATE{_quoted(run_date)}\n"
        f"{body}"
    )


def anomaly_statement(catalog: str = DEFAULT_CATALOG, ns: str = "demo") -> str:
    """The single atomic load of `gold.estate_anomalies` for one namespace.

    Two detectors read the converted tables; the manifest is LEFT JOINed, so a detected
    anomaly is still recorded when no manifest slice has been landed — it then says so
    instead of silently claiming traceability. Manifest kinds no converted unit ingests are
    recorded as coverage gaps, so an absent row never reads as an absent defect.
    """
    validate_ns(ns)
    validate_identifier(catalog, "catalog")
    n = _quoted(ns)
    kinds = ", ".join(_quoted(kind) for kind in DETECTED_ANOMALY_KINDS)
    trace = (
        "concat('; trace=', CASE WHEN m.kind IS NULL "
        f"THEN 'no seed-manifest slice landed for ns={ns}: traceability UNVERIFIED' "
        "ELSE concat('manifest kind=', m.kind, ' target=', m.target, ' planted=', m.planted_count, "
        "' generated_at=', m.manifest_generated_at, ' manifest_sha256=', m.manifest_sha256) END)"
    )

    orphaned_metadata = f"""
SELECT
  {n}                    AS ns,
  'storage_cleanup'      AS unit,
  'orphaned_metadata'    AS anomaly_type,
  concat('file_metadata row points at a storage key the seed never wrote: file_id=', f.file_id,
         ' storage_key=', f.storage_key, ' owner_id=', f.owner_id, {trace}) AS detail,
  current_timestamp()    AS detected_at
FROM {catalog}.bronze.file_metadata_raw f
LEFT JOIN {catalog}.bronze.seed_anomaly_manifest m
  ON m.ns = f.ns AND m.kind = 'orphaned_metadata'
WHERE f.ns = {n} AND f.storage_key LIKE concat({n}, '/missing/%')
""".strip()

    missing_hours = f"""
SELECT
  {n}                 AS ns,
  'analytics_daily'   AS unit,
  'missing_hours'     AS anomaly_type,
  concat('no events in silver for hour ', date_format(h.hour_start, "yyyy-MM-dd'T'HH:mm:ss'Z'"),
         ', inside the seeded span ', date_format(h.span_start, 'yyyy-MM-dd HH'), ' .. ',
         date_format(h.span_end, 'yyyy-MM-dd HH'), {trace}) AS detail,
  current_timestamp() AS detected_at
FROM (
  SELECT explode(sequence(span_start, span_end, INTERVAL 1 HOUR)) AS hour_start, span_start, span_end
  FROM (
    SELECT date_trunc('HOUR', min(event_ts)) AS span_start, date_trunc('HOUR', max(event_ts)) AS span_end
    FROM {catalog}.silver.analytics_events
    WHERE ns = {n}
  )
) h
LEFT JOIN {catalog}.bronze.seed_anomaly_manifest m
  ON m.ns = {n} AND m.kind = 'missing_hours'
WHERE NOT EXISTS (
  SELECT 1 FROM {catalog}.silver.analytics_events e
  WHERE e.ns = {n} AND date_trunc('HOUR', e.event_ts) = h.hour_start
)
""".strip()

    coverage_gaps = f"""
SELECT
  {n}                 AS ns,
  'seed_manifest'     AS unit,
  m.kind              AS anomaly_type,
  concat('manifest declares ', m.planted_count, ' anomalies in ', m.target,
         ', and no converted unit ingests that source, so the estate cannot detect them: ',
         'this row is the coverage gap, not a detected anomaly',
         '; trace=manifest kind=', m.kind, ' generated_at=', m.manifest_generated_at,
         ' manifest_sha256=', m.manifest_sha256) AS detail,
  current_timestamp() AS detected_at
FROM {catalog}.bronze.seed_anomaly_manifest m
WHERE m.ns = {n} AND m.kind NOT IN ({kinds})
""".strip()

    body = "\nUNION ALL\n".join([orphaned_metadata, missing_hours, coverage_gaps])
    return f"INSERT INTO {catalog}.gold.estate_anomalies REPLACE WHERE ns = {_quoted(ns)}\n{body}"


def summary_queries(catalog: str = DEFAULT_CATALOG, ns: str = "demo", run_date: str = "1970-01-01") -> dict[str, str]:
    """Queries the run asserts on, and that the recon script re-reads independently."""
    validate_ns(ns)
    validate_identifier(catalog, "catalog")
    validate_run_date(run_date)
    n, d = _quoted(ns), f"DATE{_quoted(run_date)}"
    return {
        "units": f"SELECT count(*) FROM {catalog}.gold.estate_daily_rollup WHERE ns = {n} AND run_date = {d}",
        "green": f"SELECT count(*) FROM {catalog}.gold.estate_daily_rollup WHERE ns = {n} AND run_date = {d} AND recon_result = 'green'",
        "not_green": (
            f"SELECT concat_ws(', ', collect_list(concat(unit, '=', recon_result))) "
            f"FROM {catalog}.gold.estate_daily_rollup WHERE ns = {n} AND run_date = {d} AND recon_result <> 'green'"
        ),
        "invalid_result": (
            f"SELECT count(*) FROM {catalog}.gold.estate_daily_rollup WHERE ns = {n} AND run_date = {d} "
            f"AND recon_result NOT IN ({', '.join(_quoted(value) for value in RECON_RESULTS)})"
        ),
        "anomalies": f"SELECT count(*) FROM {catalog}.gold.estate_anomalies WHERE ns = {n}",
        "untraceable_anomalies": (
            f"SELECT count(*) FROM {catalog}.gold.estate_anomalies WHERE ns = {n} AND detail LIKE '%traceability UNVERIFIED%'"
        ),
    }


def run_pipeline(
    execute: Callable[[str], object],
    scalar: Callable[[str], object],
    ddl_text: str | None = None,
    catalog: str = DEFAULT_CATALOG,
    ns: str = "demo",
    run_date: str = "1970-01-01",
    job_run_id: str = "",
    log: Callable[[str], None] = print,
) -> dict[str, object]:
    """Apply the DDL (optional), load both gold tables, then fail if a unit is not green."""
    expected_units = len(unit_specs(catalog, ns))
    if ddl_text is not None:
        for statement in ddl_statements(ddl_text, catalog):
            execute(statement["sql"])
        log(f"ddl applied to {catalog}")

    execute(rollup_statement(catalog, ns, run_date, job_run_id))
    log(f"estate_daily_rollup loaded for ns={ns} run_date={run_date}")
    execute(anomaly_statement(catalog, ns))
    log(f"estate_anomalies loaded for ns={ns}")

    queries = summary_queries(catalog, ns, run_date)
    counts = {key: scalar(queries[key]) for key in ("units", "green", "invalid_result", "anomalies", "untraceable_anomalies")}
    counts = {key: int(value or 0) for key, value in counts.items()}
    counts["not_green"] = scalar(queries["not_green"]) or ""

    if counts["units"] != expected_units:
        raise EstateNotReconciled(
            f"rollup wrote {counts['units']} unit rows for ns={ns} run_date={run_date}, expected {expected_units}"
        )
    if counts["invalid_result"]:
        raise EstateNotReconciled(f"{counts['invalid_result']} rollup rows carry a recon_result outside {RECON_RESULTS}")

    log(f"estate rollup for ns={ns} run_date={run_date}: {counts}")
    if counts["green"] != counts["units"]:
        raise EstateNotReconciled(
            f"{counts['units'] - counts['green']} of {counts['units']} units did not reconcile for "
            f"ns={ns} run_date={run_date}: {counts['not_green']}"
        )
    return counts


# COMMAND ----------

if __name__ == "__main__":
    dbutils = globals().get("dbutils")
    spark = globals().get("spark")
    if dbutils is None or spark is None:
        raise SystemExit(
            "this notebook runs as the ow_tp_estate_rollup task; "
            "use scripts/tp_databricks/run_estate_rollup.py locally"
        )

    dbutils.widgets.text("ns", "demo")
    dbutils.widgets.text("catalog", DEFAULT_CATALOG)
    dbutils.widgets.text("run_date", "")
    dbutils.widgets.text("job_run_id", "")
    dbutils.widgets.text("stage", "rollup")
    dbutils.widgets.text("unit", "")
    dbutils.widgets.text("gate_table", "")
    dbutils.widgets.text("ddl_path", "")

    job_ns = dbutils.widgets.get("ns")
    job_catalog = dbutils.widgets.get("catalog")
    # An empty run_date resolves to the run's own UTC date, as the legacy scripts' localtime
    # stamps did; the job passes {{job.start_time.iso_date}} so every task of a run, and every
    # retry of a task, publishes the same date.
    job_run_date = dbutils.widgets.get("run_date") or spark.sql("SELECT cast(current_date() AS STRING)").collect()[0][0]

    if dbutils.widgets.get("stage") == "unit_gate":
        run_unit_gate(
            scalar=lambda statement: spark.sql(statement).collect()[0][0],
            catalog=job_catalog,
            ns=job_ns,
            unit=dbutils.widgets.get("unit"),
            source_table=dbutils.widgets.get("gate_table") or None,
        )
    else:
        run_pipeline(
            execute=lambda statement: spark.sql(statement),
            scalar=lambda statement: spark.sql(statement).collect()[0][0],
            # The job passes the workspace path of the deployed, reviewed DDL file; applying it
            # here (rather than from a SQL task) is what lets `${catalog}` be resolved to the
            # catalog this run actually writes to.
            ddl_text=_read_ddl(dbutils.widgets.get("ddl_path")),
            catalog=job_catalog,
            ns=job_ns,
            run_date=job_run_date,
            job_run_id=dbutils.widgets.get("job_run_id"),
        )
