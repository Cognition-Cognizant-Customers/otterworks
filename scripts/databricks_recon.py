# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg2-binary", "boto3", "requests"]
# ///
"""Legacy-vs-Databricks reconciliation harness (tech-partnerships track).

Runs the legacy estate locally on deterministic fixtures, ships the SAME
inputs to the ow_tp lakehouse, runs the Terraform-managed Workflows jobs, and
compares outputs row/value-wise. Writes databricks/reports/databricks-recon.{md,json}
in the same spirit as procs/harness/oracle_parity.py.

Phases:
  custbill  — gen_sample_data.pl -> sftp_ingest_poll.ksh ->
              parse_custbill_fixedwidth.sh -> finance_excel_report.pl locally;
              same .dat files -> landing volume -> ow_tp_custbill_lakehouse job;
              silver rows vs .psv rows, gold rows vs finance CSV.
  python    — exports the seeded stores (make seed-legacy NS=<ns>): S3 event
              objects (byte-for-byte), DynamoDB file-metadata slice, Postgres
              documents; runs ow_tp_python_etl_wave; compares gold tables
              against a local recompute of the legacy aggregation semantics.

Usage:
  uv run scripts/databricks_recon.py --ns dev [--phases custbill,python]

Env: DATABRICKS_DEMO_HOST/DATABRICKS_DEMO_TOKEN (or DATABRICKS_HOST/TOKEN).
Never pass tokens on the command line.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "databricks" / "reports"
CATALOG = os.getenv("OW_TP_CATALOG", "ow_tp")
if not re.fullmatch(r"[a-z0-9_]{1,64}", CATALOG):
    sys.exit(f"invalid OW_TP_CATALOG: {CATALOG!r}")
DATA_LAKE_BUCKET = "otterworks-data-lake"
DYNAMO_TABLE = "otterworks-file-metadata"


def api() -> tuple[str, dict[str, str]]:
    host = os.getenv("DATABRICKS_DEMO_HOST") or os.getenv("DATABRICKS_HOST")
    token = os.getenv("DATABRICKS_DEMO_TOKEN") or os.getenv("DATABRICKS_TOKEN")
    if not host or not token:
        sys.exit("DATABRICKS_DEMO_HOST / DATABRICKS_DEMO_TOKEN must be set")
    return host.rstrip("/"), {"Authorization": f"Bearer {token}"}


HOST, HEADERS = api()


def dbx(method: str, path: str, **kwargs) -> dict:
    resp = requests.request(method, f"{HOST}{path}", headers=HEADERS, timeout=120, **kwargs)
    if resp.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}")
    return resp.json() if resp.text else {}


def upload_file(rel_path: str, data: bytes) -> None:
    """Stage an input file in the workspace landing area.

    The demo PAT has workspace scope but not the files scope, so inputs land
    under /Shared/ow_tp/landing/ via the workspace import API; the bronze
    ingest notebooks copy them into the UC landing volume.
    """
    import base64

    path = f"/Shared/{CATALOG}/landing/{rel_path}"
    dbx("POST", "/api/2.0/workspace/mkdirs", json={"path": path.rsplit("/", 1)[0]})
    dbx(
        "POST",
        "/api/2.0/workspace/import",
        json={
            "path": path,
            "format": "AUTO",
            "overwrite": True,
            "content": base64.b64encode(data).decode(),
        },
    )


def clear_staging(rel_dir: str) -> None:
    """Remove a per-namespace staging directory so stale files from an earlier
    run (e.g. a different seed scale) are never re-ingested."""
    try:
        dbx(
            "POST",
            "/api/2.0/workspace/delete",
            json={"path": f"/Shared/{CATALOG}/landing/{rel_dir}", "recursive": True},
        )
    except RuntimeError as exc:
        if "RESOURCE_DOES_NOT_EXIST" not in str(exc):
            raise


def find_job_id(name: str) -> int:
    jobs = dbx("GET", f"/api/2.1/jobs/list?name={name}").get("jobs", [])
    if not jobs:
        sys.exit(f"job {name} not found — run terraform apply first")
    return jobs[0]["job_id"]


def run_job(job_id: int, ns: str) -> None:
    run = dbx("POST", "/api/2.1/jobs/run-now", json={"job_id": job_id, "job_parameters": {"ns": ns}})
    run_id = run["run_id"]
    while True:
        state = dbx("GET", f"/api/2.1/jobs/runs/get?run_id={run_id}")["state"]
        life = state.get("life_cycle_state")
        if life in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
            result = state.get("result_state")
            if result != "SUCCESS":
                raise RuntimeError(f"job {job_id} run {run_id}: {life}/{result} {state.get('state_message', '')}")
            return
        time.sleep(15)


def warehouse_id() -> str:
    wid = os.getenv("DATABRICKS_WAREHOUSE_ID")
    if wid:
        return wid
    for wh in dbx("GET", "/api/2.0/sql/warehouses").get("warehouses", []):
        if wh.get("enable_serverless_compute"):
            return wh["id"]
    sys.exit("no serverless SQL warehouse found")


WAREHOUSE = warehouse_id()


def sql(statement: str) -> list[list[str | None]]:
    payload = {
        "warehouse_id": WAREHOUSE,
        "statement": statement,
        "wait_timeout": "50s",
        "on_wait_timeout": "CONTINUE",
        "disposition": "INLINE",
        "format": "JSON_ARRAY",
    }
    result = dbx("POST", "/api/2.0/sql/statements", json=payload)
    while result["status"]["state"] in ("PENDING", "RUNNING"):
        time.sleep(3)
        result = dbx("GET", f"/api/2.0/sql/statements/{result['statement_id']}")
    if result["status"]["state"] != "SUCCEEDED":
        raise RuntimeError(f"SQL failed: {result['status']}")
    return result.get("result", {}).get("data_array", [])


class Recon:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def check(self, phase: str, name: str, legacy, databricks, detail: str = "") -> None:
        status = "PASS" if legacy == databricks else "FAIL"
        entry = {"phase": phase, "check": name, "status": status, "detail": detail}
        if status == "FAIL":
            entry["legacy"] = _preview(legacy)
            entry["databricks"] = _preview(databricks)
        self.checks.append(entry)
        print(f"[{status}] {phase}/{name} {detail}")

    @property
    def failed(self) -> bool:
        return any(c["status"] == "FAIL" for c in self.checks)


def _preview(value):
    if isinstance(value, (list, set, tuple)):
        items = sorted(value) if isinstance(value, set) else list(value)
        return items[:20] + ([f"... {len(items) - 20} more"] if len(items) > 20 else [])
    return value


# ---------------------------------------------------------------- custbill


def run_legacy_custbill(ns: str, workdir: Path) -> tuple[dict[str, bytes], set, list]:
    env = {**os.environ, "OTTERWORKS_LEGACY_ROOT": str(workdir)}
    subprocess.run(
        ["perl", "etl/legacy-extra/tools/gen_sample_data.pl", ns], cwd=ROOT, env=env, check=True
    )
    drop = workdir / "sftp-drop" / "upload"
    inputs = {f.name: f.read_bytes() for f in sorted(drop.glob("CUSTBILL_*.dat"))}

    for job in (
        ["ksh", "etl/legacy-extra/jobs/sftp_ingest_poll.ksh"],
        ["bash", "etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh"],
        ["perl", "etl/legacy-extra/jobs/finance_excel_report.pl"],
    ):
        subprocess.run(job, cwd=ROOT, env=env, check=True, capture_output=True)

    psv_rows = []
    for psv in (workdir / "parsed").glob("*.psv"):
        for line in psv.read_text().splitlines():
            if line:
                psv_rows.append(tuple(line.split("|")))

    report = sorted((workdir / "reports").glob("finance_billing_*.csv"))[-1]
    csv_rows = [
        tuple(line.split(","))
        for line in report.read_text().splitlines()[1:]
        if line
    ]
    return inputs, psv_rows, csv_rows


def phase_custbill(recon: Recon, ns: str) -> None:
    workdir = Path(f"/tmp/otterworks-legacy-recon-{ns}")
    subprocess.run(["rm", "-rf", str(workdir)], check=True)
    inputs, psv_rows, csv_rows = run_legacy_custbill(ns, workdir)
    print(f"legacy custbill: {len(inputs)} files, {len(psv_rows)} parsed rows")

    clear_staging(f"{ns}/custbill")
    for name, data in inputs.items():
        upload_file(f"{ns}/custbill/{name}", data)
    run_job(find_job_id("ow_tp_custbill_lakehouse"), ns)

    # Sorted lists (not sets) so duplicated silver rows cannot mask a mismatch.
    silver = sorted(
        tuple(r)
        for r in sql(
            f"""SELECT customer_id, customer_name, date_format(billing_date, 'yyyy-MM-dd'),
                       format_number(amount, '0.00'), currency, record_type
                FROM `{CATALOG}`.silver.custbill_records WHERE ns = '{ns}'"""
        )
    )
    recon.check(
        "custbill",
        "silver_rows_match_legacy_psv",
        sorted(psv_rows),
        silver,
        f"({len(psv_rows)} legacy rows vs {len(silver)} silver rows)",
    )

    gold = [
        tuple(r)
        for r in sql(
            f"""SELECT currency, record_type, CAST(record_count AS STRING),
                       format_number(total_amount, '0.00')
                FROM `{CATALOG}`.gold.finance_billing_summary WHERE ns = '{ns}'
                ORDER BY currency, record_type"""
        )
    ]
    legacy_sorted = sorted(csv_rows)
    recon.check(
        "custbill",
        "gold_matches_legacy_finance_report",
        legacy_sorted,
        sorted(gold),
        f"({len(legacy_sorted)} aggregate rows)",
    )

    quarantined = int(sql(
        f"SELECT COUNT(*) FROM `{CATALOG}`.silver.custbill_quarantine WHERE ns = '{ns}'"
    )[0][0])
    recon.check("custbill", "clean_input_zero_quarantine", 0, quarantined)

    audits = sql(
        f"SELECT source_file, status FROM `{CATALOG}`.silver.custbill_file_audit WHERE ns = '{ns}'"
    )
    recon.check(
        "custbill",
        "trailer_counts_reconciled",
        sorted((name, "MATCHED") for name in inputs),
        sorted((r[0], r[1]) for r in audits),
    )


# ---------------------------------------------------------------- python wave


def aws_client(service: str):
    import boto3

    return boto3.client(
        service,
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


def export_events(ns: str) -> list[dict]:
    s3 = aws_client("s3")
    prefix = f"events/{ns}/"
    events = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=DATA_LAKE_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            body = s3.get_object(Bucket=DATA_LAKE_BUCKET, Key=obj["Key"])["Body"].read()
            rel = obj["Key"][len(prefix):]
            upload_file(f"{ns}/events/{rel}", body)
            for line in gzip.decompress(body).decode().splitlines():
                if line:
                    events.append(json.loads(line))
    return events


def export_file_metadata(ns: str) -> list[dict]:
    import boto3
    from boto3.dynamodb.conditions import Attr

    table = boto3.resource(
        "dynamodb",
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    ).Table(DYNAMO_TABLE)

    items = []
    kwargs = {"FilterExpression": Attr("ns").eq(ns)}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp["Items"])
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def plain(v):
        from decimal import Decimal

        if isinstance(v, Decimal):
            return int(v)
        return v

    rows = [{k: plain(v) for k, v in item.items()} for item in items]
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        for row in sorted(rows, key=lambda r: r["id"]):
            gz.write((json.dumps(row, sort_keys=True) + "\n").encode())
    upload_file(f"{ns}/file_metadata/file_metadata.jsonl.gz", buf.getvalue())
    return rows


def export_documents(ns: str) -> list[dict]:
    import psycopg2

    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "otterworks"),
        user=os.getenv("DB_USER", "otterworks"),
        password=os.getenv("DB_PASSWORD", "otterworks_dev"),
    )
    with conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT id, title, content_type, owner_id, is_deleted FROM otterworks_{ns}.documents ORDER BY id"
        )
        docs = [
            {
                "id": str(r[0]),
                "title": r[1],
                "content_type": r[2],
                "owner_id": str(r[3]),
                "is_deleted": bool(r[4]),
            }
            for r in cur.fetchall()
        ]
    conn.close()

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        for doc in docs:
            gz.write((json.dumps(doc, sort_keys=True) + "\n").encode())
    upload_file(f"{ns}/documents/documents.jsonl.gz", buf.getvalue())
    return docs


def phase_python(recon: Recon, ns: str) -> None:
    for sub in ("events", "file_metadata", "documents"):
        clear_staging(f"{ns}/{sub}")
    events = export_events(ns)
    metadata = export_file_metadata(ns)
    documents = export_documents(ns)
    print(
        f"exports: {len(events)} events, {len(metadata)} metadata items, {len(documents)} documents"
    )
    if not events or not metadata or not documents:
        sys.exit(f"seeded stores are empty for ns '{ns}' — run: make seed-legacy NS={ns}")

    run_job(find_job_id("ow_tp_python_etl_wave"), ns)

    # analytics_daily — legacy semantics: per-day totals + distinct users.
    daily = defaultdict(lambda: [0, set()])
    by_type = defaultdict(int)
    by_user = defaultdict(int)
    for ev in events:
        day = ev["occurred_at"][:10]
        user = ev.get("user_id") or "unknown"
        daily[day][0] += 1
        if user != "unknown":  # legacy excludes the placeholder from active_users
            daily[day][1].add(user)
        by_type[(day, ev["event_type"])] += 1
        by_user[(day, user)] += 1

    expected_daily = sorted((d, str(c), str(len(u))) for d, (c, u) in daily.items())
    got_daily = sorted(
        tuple(r)
        for r in sql(
            f"""SELECT date_format(event_date, 'yyyy-MM-dd'), CAST(total_events AS STRING),
                       CAST(unique_users AS STRING)
                FROM `{CATALOG}`.gold.analytics_daily_summary WHERE ns = '{ns}'"""
        )
    )
    recon.check("python", "analytics_daily_summary", expected_daily, got_daily,
                f"({len(expected_daily)} days)")

    expected_types = sorted((d, t, str(c)) for (d, t), c in by_type.items())
    got_types = sorted(
        tuple(r)
        for r in sql(
            f"""SELECT date_format(event_date, 'yyyy-MM-dd'), event_type, CAST(event_count AS STRING)
                FROM `{CATALOG}`.gold.analytics_event_type_daily WHERE ns = '{ns}'"""
        )
    )
    recon.check("python", "analytics_event_type_daily", expected_types, got_types,
                f"({len(expected_types)} day×type rows)")

    # audit_archive_weekly — events older than (max occurred_at − 90d).
    parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    max_ts = max(parse(ev["occurred_at"]) for ev in events)
    cutoff = max_ts - timedelta(days=90)
    expected_archived = sum(1 for ev in events if parse(ev["occurred_at"]) < cutoff)
    got = sql(
        f"""SELECT CAST(archived_events AS STRING), CAST(retained_events AS STRING)
            FROM `{CATALOG}`.gold.audit_archive_runs WHERE ns = '{ns}'"""
    )[0]
    recon.check("python", "audit_archive_counts",
                (str(expected_archived), str(len(events) - expected_archived)),
                tuple(got))

    # storage_cleanup_daily — dangling refs are s3_keys outside <ns>/files/.
    referenced = sum(1 for m in metadata if m["s3_key"].startswith(f"{ns}/files/"))
    dangling = len(metadata) - referenced
    trashed = sum(1 for m in metadata if m["is_trashed"])
    reclaimable = sum(int(m["size_bytes"]) for m in metadata if m["is_trashed"])
    got = sql(
        f"""SELECT CAST(total_objects AS STRING), CAST(referenced_objects AS STRING),
                   CAST(dangling_references AS STRING), CAST(trashed_objects AS STRING),
                   CAST(reclaimable_bytes AS STRING)
            FROM `{CATALOG}`.gold.storage_cleanup_report WHERE ns = '{ns}'"""
    )[0]
    recon.check("python", "storage_cleanup_report",
                (str(len(metadata)), str(referenced), str(dangling), str(trashed), str(reclaimable)),
                tuple(got),
                f"({dangling} planted dangling refs)")

    # search_reindex_weekly — index counts equal the source corpora.
    got = sql(
        f"""SELECT (SELECT COUNT(*) FROM `{CATALOG}`.gold.search_documents_index WHERE ns = '{ns}'),
                   (SELECT COUNT(*) FROM `{CATALOG}`.gold.search_files_index WHERE ns = '{ns}')"""
    )[0]
    recon.check("python", "search_index_counts",
                (str(len(documents)), str(len(metadata))), tuple(got))

    # user_activity_daily — 30-day window ending at the newest event date.
    window_start = (max_ts - timedelta(days=29)).date().isoformat()
    totals = defaultdict(lambda: [0, set()])
    for (day, user), count in by_user.items():
        if day >= window_start:
            totals[user][0] += count
            totals[user][1].add(day)
    expected_activity = sorted(
        (user, str(total), str(len(days))) for user, (total, days) in totals.items()
    )
    got_activity = sorted(
        tuple(r)
        for r in sql(
            f"""SELECT user_id, CAST(total_actions AS STRING), CAST(active_days AS STRING)
                FROM `{CATALOG}`.gold.user_activity_report WHERE ns = '{ns}'"""
        )
    )
    recon.check("python", "user_activity_totals", expected_activity, got_activity,
                f"({len(expected_activity)} users)")


# ---------------------------------------------------------------- report


def write_report(recon: Recon, ns: str, phases: list[str]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "namespace": ns,
        "phases": phases,
        "generated_by": "scripts/databricks_recon.py",
        "checks": recon.checks,
        "status": "FAIL" if recon.failed else "PASS",
    }
    (REPORT_DIR / "databricks-recon.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )

    lines = [
        "# Databricks lakehouse reconciliation report",
        "",
        f"- Namespace: `{ns}`",
        f"- Phases: {', '.join(phases)}",
        f"- Overall: **{payload['status']}**",
        "",
        "| Phase | Check | Status | Detail |",
        "|---|---|---|---|",
    ]
    for c in recon.checks:
        lines.append(f"| {c['phase']} | {c['check']} | {c['status']} | {c['detail']} |")
    for c in recon.checks:
        if c["status"] == "FAIL":
            lines += ["", f"## FAIL: {c['phase']}/{c['check']}", "",
                      f"- legacy: `{c['legacy']}`", f"- databricks: `{c['databricks']}`"]
    (REPORT_DIR / "databricks-recon.md").write_text("\n".join(lines) + "\n")
    print(f"report: {REPORT_DIR / 'databricks-recon.md'} ({payload['status']})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ns", required=True)
    parser.add_argument("--phases", default="custbill,python")
    args = parser.parse_args()

    ns = args.ns.lower()
    if not re.fullmatch(r"[a-z0-9_]{1,32}", ns):
        sys.exit(f"invalid namespace: {args.ns!r}")
    phases = [p.strip() for p in args.phases.split(",") if p.strip()]
    known = {"custbill", "python"}
    unknown = [p for p in phases if p not in known]
    if unknown or not phases:
        sys.exit(f"invalid --phases {args.phases!r}; known phases: {sorted(known)}")

    recon = Recon()
    runners = {"custbill": phase_custbill, "python": phase_python}
    for phase in phases:
        try:
            runners[phase](recon, ns)
        except Exception as exc:  # noqa: BLE001 — a failed job/SQL call is a FAIL, not a crash
            recon.check(phase, "phase_completed", "completed", f"aborted: {exc}")
    write_report(recon, ns, phases)
    return 1 if recon.failed else 0


if __name__ == "__main__":
    sys.exit(main())
