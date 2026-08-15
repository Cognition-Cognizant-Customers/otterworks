# Databricks notebook source
# MAGIC %md
# MAGIC # ow_tp_finance_report
# MAGIC
# MAGIC Converted replacement for `etl/legacy-extra/jobs/finance_excel_report.pl` (Perl 5.005,
# MAGIC no modules, 2004). What changes:
# MAGIC
# MAGIC | Legacy | Here |
# MAGIC |---|---|
# MAGIC | Row-by-row `%tot`/`%cnt` accumulation in Perl over `parsed/*.psv` | one `GROUP BY` against `ow_tp.silver.custbill_records` |
# MAGIC | CSV renamed to `.xls` | a real CSV, written with a `.csv` extension; the gold table is the system of record |
# MAGIC | `sendmail -t` pipe that no-ops when the binary is missing | delivery is attempted only if a transport is configured, with a durable pre-send attempt audit and a post-acceptance delivery confirmation in `ow_tp.gold.finance_report_delivery` |
# MAGIC | Recipients hardcoded (`jake@…`, gone since 2020) | distribution list read from the `ow_tp` secret scope |
# MAGIC | Hostname `if`-blocks choosing `/data/otterworks` vs `/data2/otterworks_uat` | job parameters + Unity Catalog volume paths |
# MAGIC | Lock file checked, never removed | `max_concurrent_runs = 1` plus delete-and-insert idempotency per `(ns, report_date)` |
# MAGIC | `$SIG{PIPE} = 'IGNORE'`, `2>/dev/null` everywhere | failures raise and fail the run |
# MAGIC
# MAGIC The SQL below is the single source of truth for the conversion: the job task runs it
# MAGIC through `spark.sql`, and `scripts/tp_databricks/run_finance_report.py` runs the exact
# MAGIC same statement list through the serverless SQL warehouse.

# COMMAND ----------

from __future__ import annotations

import re

CSV_HEADER = "Currency,RecordType,RecordCount,TotalAmount"

# Catalog, namespace and business date reach the statements as identifiers or literals that
# cannot be bound as parameters, so each is validated against a strict pattern before it is
# interpolated, and every free-text value goes through sql_literal(). Backslash is an escape
# character in Spark SQL literals, so escaping the quote alone would not contain a value.
_PATTERNS = {
    "catalog": re.compile(r"^[A-Za-z0-9_]{1,64}$"),
    "ns": re.compile(r"^[A-Za-z0-9_-]{1,64}$"),
    "report_date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
}

# Delivery statuses. DELIVERED means a transport accepted the report; the attempted and
# unconfirmed values make uncertain sends explicit instead of allowing retries to duplicate
# a report or silently claiming success.
STATUS_DELIVERED = "DELIVERED"
STATUS_ATTEMPTING = "DELIVERY_ATTEMPTED_UNCONFIRMED"
STATUS_UNCONFIRMED = "NOT_DELIVERED_ATTEMPT_UNCONFIRMED"
STATUS_NO_TRANSPORT = "NOT_DELIVERED_NO_TRANSPORT_CONFIGURED"
STATUS_NO_RECIPIENTS = "NOT_DELIVERED_NO_RECIPIENTS_CONFIGURED"


def sql_literal(value: str) -> str:
    """Render a value as a Spark SQL string literal, escaping backslashes and quotes."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def checked(**values: str) -> None:
    """Reject any run parameter that is not the shape its statements assume."""
    for name, value in values.items():
        if not _PATTERNS[name].fullmatch(value):
            raise ValueError(f"invalid {name} {value!r}: expected {_PATTERNS[name].pattern}")


def ddl_statements(catalog: str = "ow_tp") -> list[str]:
    """Idempotent DDL for the gold tables. Mirrors databricks/sql/finance_report_tables.sql."""
    checked(catalog=catalog)
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {catalog}.gold.finance_billing_summary (
          ns STRING NOT NULL COMMENT 'Demo namespace; every run is scoped to one namespace.',
          currency STRING NOT NULL COMMENT 'ISO currency from copybook CBCUST01 CURRENCY.',
          record_type STRING NOT NULL COMMENT 'INVOICE (legacy 01) or CREDIT (legacy 02).',
          record_count BIGINT NOT NULL COMMENT 'Records aggregated for the currency/record-type cell.',
          total_amount DECIMAL(18,2) NOT NULL COMMENT 'Exact decimal total; no float accumulation.',
          report_date DATE NOT NULL COMMENT 'Business date of the report run.',
          generated_at TIMESTAMP NOT NULL COMMENT 'When this row was produced.'
        )
        COMMENT 'Finance billing summary by currency and record type, aggregated in SQL from ow_tp.silver.custbill_records. Replaces finance_billing_<date>.xls.'
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {catalog}.gold.finance_report_delivery (
          ns STRING NOT NULL COMMENT 'Demo namespace.',
          report_date DATE NOT NULL COMMENT 'Business date of the report run.',
          artifact_path STRING COMMENT 'Volume path of the emitted artifact, NULL when nothing was written.',
          recipient_list STRING COMMENT 'Distribution list resolved from the ow_tp secret scope, never from code.',
          delivery_status STRING NOT NULL COMMENT 'DELIVERED, DELIVERY_ATTEMPTED_UNCONFIRMED, NOT_DELIVERED_ATTEMPT_UNCONFIRMED, or NOT_DELIVERED_<reason>.',
          delivered_at TIMESTAMP COMMENT 'Set only when delivery actually happened; NULL otherwise.'
        )
        COMMENT 'Delivery audit the legacy sendmail no-op never produced: what was written, to whom it was addressed, and whether it actually went out.'
        """,
    ]


def summary_statements(ns: str, report_date: str, catalog: str = "ow_tp") -> list[str]:
    """Rebuild the gold summary for one (ns, report_date).

    Delete-then-insert instead of a blind append: re-running the job replaces the run's
    rows rather than duplicating them, which is what the legacy job's never-removed lock
    file was pretending to protect against.

    Only record types 01/02 are aggregated. Anything else never reaches silver: the parse
    job quarantines it in silver.custbill_rejects, where it is visible, instead of the
    legacy report's silent `UNKNOWN(xx)` row.
    """
    checked(catalog=catalog, ns=ns, report_date=report_date)
    return [
        f"""
        DELETE FROM {catalog}.gold.finance_billing_summary
        WHERE ns = {sql_literal(ns)} AND report_date = DATE '{report_date}'
        """,
        f"""
        INSERT INTO {catalog}.gold.finance_billing_summary
          (ns, currency, record_type, record_count, total_amount, report_date, generated_at)
        SELECT
          ns,
          currency,
          CASE record_type WHEN '01' THEN 'INVOICE' WHEN '02' THEN 'CREDIT' END AS record_type,
          COUNT(*) AS record_count,
          SUM(amount) AS total_amount,
          DATE '{report_date}' AS report_date,
          current_timestamp() AS generated_at
        FROM {catalog}.silver.custbill_records
        WHERE ns = {sql_literal(ns)} AND record_type IN ('01', '02')
        GROUP BY ns, currency, record_type
        """,
    ]


def summary_select(ns: str, report_date: str, catalog: str = "ow_tp") -> str:
    """The report body, ordered like the legacy `sort keys %tot`: currency, then 01 before 02."""
    checked(catalog=catalog, ns=ns, report_date=report_date)
    return f"""
        SELECT currency, record_type, record_count, total_amount
        FROM {catalog}.gold.finance_billing_summary
        WHERE ns = {sql_literal(ns)} AND report_date = DATE '{report_date}'
        ORDER BY currency, CASE record_type WHEN 'INVOICE' THEN 0 ELSE 1 END
    """


def delivery_statements(
    ns: str,
    report_date: str,
    artifact_path: str | None,
    recipients: str | None,
    status: str,
    catalog: str = "ow_tp",
    delivered_at: object | None = None,
) -> list[str]:
    """Write the run's delivery audit row, replacing any previous row for the same run."""
    checked(catalog=catalog, ns=ns, report_date=report_date)
    artifact_sql = "NULL" if artifact_path is None else sql_literal(artifact_path)
    recipients_sql = "NULL" if not recipients else sql_literal(recipients)
    if delivered_at is not None:
        timestamp_text = str(delivered_at).replace("T", " ", 1)
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
            r"(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?",
            timestamp_text,
        ):
            raise ValueError(f"invalid delivered_at timestamp {delivered_at!r}")
        delivered_sql = f"TIMESTAMP {sql_literal(timestamp_text)}"
    else:
        delivered_sql = (
            "current_timestamp()"
            if status == STATUS_DELIVERED
            else "CAST(NULL AS TIMESTAMP)"
        )
    return [
        f"""
        DELETE FROM {catalog}.gold.finance_report_delivery
        WHERE ns = {sql_literal(ns)} AND report_date = DATE '{report_date}'
        """,
        f"""
        INSERT INTO {catalog}.gold.finance_report_delivery
          (ns, report_date, artifact_path, recipient_list, delivery_status, delivered_at)
        VALUES ({sql_literal(ns)}, DATE '{report_date}', {artifact_sql}, {recipients_sql},
                {sql_literal(status)}, {delivered_sql})
        """,
    ]


def render_csv(rows: list[tuple[str, str, int, str]]) -> str:
    """Render the report as real CSV. Same shape as the legacy file, honest extension."""
    lines = [CSV_HEADER]
    for currency, record_type, record_count, total_amount in rows:
        lines.append(f"{currency},{record_type},{int(record_count)},{total_amount}")
    return "\n".join(lines) + "\n"


def artifact_relpath(ns: str, report_date: str) -> str:
    """Volume-relative path of the emitted artifact (a .csv, because it is a CSV)."""
    return f"{ns}/reports/finance_billing_{report_date.replace('-', '')}.csv"


# COMMAND ----------


def _run() -> None:
    """Job entry point. Runs only inside Databricks; importable elsewhere for the SQL above."""
    import datetime

    dbutils.widgets.text("ns", "demo")
    dbutils.widgets.text("report_date", "")
    dbutils.widgets.text("catalog", "ow_tp")
    dbutils.widgets.text("secret_scope", "ow_tp")
    dbutils.widgets.text("recipients_secret_key", "finance_report_recipients")
    dbutils.widgets.text("smtp_secret_key", "finance_report_smtp_host")

    ns = dbutils.widgets.get("ns").strip()
    catalog = dbutils.widgets.get("catalog").strip()
    scope = dbutils.widgets.get("secret_scope").strip()
    report_date = dbutils.widgets.get("report_date").strip() or datetime.date.today().isoformat()

    checked(catalog=catalog, ns=ns, report_date=report_date)
    row_count = spark.sql(
        f"""
        SELECT COUNT(*) AS row_count
        FROM {catalog}.silver.custbill_records
        WHERE ns = {sql_literal(ns)} AND record_type IN ('01', '02')
        """
    ).collect()[0]["row_count"]
    if not row_count:
        raise RuntimeError(
            f"no billing rows in {catalog}.silver.custbill_records for ns={ns!r}: "
            "the upstream parse job has not produced data for this namespace"
        )

    for statement in ddl_statements(catalog) + summary_statements(ns, report_date, catalog):
        spark.sql(statement)

    rows = [
        (r["currency"], r["record_type"], r["record_count"], f"{r['total_amount']:.2f}")
        for r in spark.sql(summary_select(ns, report_date, catalog)).collect()
    ]
    artifact_path = f"/Volumes/{catalog}/bronze/landing/{artifact_relpath(ns, report_date)}"
    dbutils.fs.mkdirs(artifact_path.rsplit("/", 1)[0])
    with open(artifact_path, "w", encoding="utf-8") as handle:
        handle.write(render_csv(rows))

    recipients = _secret(scope, dbutils.widgets.get("recipients_secret_key").strip())
    transport = _secret(scope, dbutils.widgets.get("smtp_secret_key").strip())
    if recipients:
        recipients = _validated_recipients(recipients)
    if not recipients:
        status = STATUS_NO_RECIPIENTS
    elif not transport:
        # The legacy job piped to a sendmail binary that does not exist on modern hosts
        # and swallowed the failure. Here the absence of a transport is recorded, not hidden.
        status = STATUS_NO_TRANSPORT
    else:
        status = None

    existing = spark.sql(
        f"""
        SELECT recipient_list, delivery_status, delivered_at
        FROM {catalog}.gold.finance_report_delivery
        WHERE ns = {sql_literal(ns)} AND report_date = DATE '{report_date}'
        """
    ).collect()
    existing_row = existing[0] if existing else None

    def write_audit(audit_recipients: str | None, audit_status: str) -> None:
        for statement in delivery_statements(
            ns, report_date, artifact_path, audit_recipients, audit_status, catalog
        ):
            spark.sql(statement)

    if existing_row and existing_row["delivery_status"] == STATUS_DELIVERED:
        recipients = existing_row["recipient_list"]
        status = STATUS_DELIVERED
    elif existing_row and existing_row["delivery_status"] in (
        STATUS_ATTEMPTING,
        STATUS_UNCONFIRMED,
    ):
        recipients = existing_row["recipient_list"] or recipients
        status = STATUS_UNCONFIRMED
        write_audit(recipients, status)
        raise RuntimeError(
            f"delivery status is {STATUS_UNCONFIRMED} for ns={ns!r}, "
            f"report_date={report_date!r}; human confirmation is required before retrying"
        )
    elif recipients and transport:
        write_audit(recipients, STATUS_ATTEMPTING)
        status = _deliver(transport, recipients, artifact_path, report_date)
        write_audit(recipients, status)
    else:
        write_audit(recipients, status)

    print(f"finance report ns={ns} report_date={report_date} rows={len(rows)}")
    recipient_count = sum(1 for address in (recipients or "").split(",") if address.strip())
    print(
        f"artifact={artifact_path} delivery_status={status} "
        f"recipients_configured={recipient_count > 0} recipient_count={recipient_count}"
    )


def _secret(scope: str, key: str) -> str | None:
    """Read a managed config value from the secret scope; absent keys are not an error.

    Only "this key is not configured" is tolerated. An unreadable scope or an API error
    raises: recording it as NOT_DELIVERED_NO_..._CONFIGURED would be the legacy job's silent
    non-delivery wearing an audit row.
    """
    if not key:
        return None
    try:
        value = dbutils.secrets.get(scope=scope, key=key).strip()
    except Exception as exc:
        detail = str(exc)
        missing_key_message = f"Failed to get secret {key} for scope {scope}."
        if missing_key_message not in detail:
            raise
        return None
    return value or None


def _validated_recipients(recipients: str) -> str:
    addresses = [address.strip() for address in recipients.split(",")]
    if any(
        not address
        or address.count("@") != 1
        or any(character.isspace() for character in address)
        or not address.split("@", 1)[0]
        or not address.split("@", 1)[1]
        for address in addresses
    ):
        raise ValueError("invalid finance report recipient list")
    return ",".join(addresses)


def _deliver(smtp_host: str, recipients: str, artifact_path: str, report_date: str) -> str:
    """Hand the report to the configured SMTP transport. Failures raise, they do not vanish."""
    import smtplib
    from email.message import EmailMessage

    host, _, port = smtp_host.partition(":")
    recipients = _validated_recipients(recipients)
    message = EmailMessage()
    message["To"] = recipients
    message["From"] = "ow-tp-finance-report@otterworks.dev"
    message["Subject"] = f"[AUTO] Finance billing report {report_date}"
    message.set_content(f"Finance billing summary for {report_date}. Artifact: {artifact_path}")
    with open(artifact_path, "rb") as handle:
        message.add_attachment(
            handle.read(),
            maintype="text",
            subtype="csv",
            filename=artifact_path.rsplit("/", 1)[-1],
        )
    with smtplib.SMTP(host, int(port or 25), timeout=30) as smtp:
        smtp.ehlo()
        if not smtp.has_extn("starttls"):
            raise RuntimeError("SMTP transport does not advertise STARTTLS")
        smtp.starttls()
        smtp.ehlo()
        smtp.send_message(message)
    return STATUS_DELIVERED


try:  # only defined inside a Databricks task; importable as a plain module elsewhere
    dbutils  # noqa: B018
except NameError:
    pass
else:
    _run()
