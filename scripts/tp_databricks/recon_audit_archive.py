#!/usr/bin/env python3
"""Reconcile the converted `ow_tp_audit_archive` job against the legacy baseline.

The baseline is the archive artifact an actual run of
`etl/scripts/audit_archive_weekly.py` wrote to (LocalStack) S3 Glacier --
`audit_events.jsonl.gz` plus its compliance `report.json` -- captured under
`--baseline-dir`. Nothing here derives an expectation from the converted job's
own output.

Checks (numbered as in docs/tech-partnerships/contracts/audit_archive_weekly.md):

1. selection parity  -- event_id set in silver == event_id set in the legacy archive
2. count parity      -- manifest candidate/archived counts == silver rows == legacy count
3. retention safety  -- no purge without verified, and the archive is still readable
4. idempotency       -- a second run archives nothing new and duplicates nothing
5. provenance        -- the report states the baseline tier verbatim

Usage:
    recon_audit_archive.py --ns demo --run-date 2026-08-01 \
        --baseline-dir /home/ubuntu/tp-golden/python/audit_archive_weekly \
        [--rerun-job ow_tp_audit_archive] [--report docs/.../audit_archive_weekly.md]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dbx  # noqa: E402  (local module, same directory)

BASELINE_TIERS = {
    "legacy output": "baseline: legacy output",
    "seed manifest": "baseline: seed manifest",
    "blocked": "blocked",
}


class Check:
    def __init__(self, number: int, name: str):
        self.number = number
        self.name = name
        self.status = "blocked"
        self.detail: dict[str, object] = {}
        self.error: str | None = None

    def record(self, passed: bool, **detail) -> None:
        self.status = "pass" if passed else "FAIL"
        self.detail.update(detail)

    def block(self, error: str, **detail) -> None:
        self.status = "blocked"
        self.error = error
        self.detail.update(detail)


def sha256_of_set(values) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode()).hexdigest()


def read_baseline(baseline_dir: str) -> dict:
    """The legacy archive artifact: event ids, count, compliance report."""
    archive = os.path.join(baseline_dir, "audit_events.jsonl.gz")
    report_path = os.path.join(baseline_dir, "report.json")
    event_ids, timestamps = [], []
    with gzip.open(archive, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                event_ids.append(record["event_id"])
                timestamps.append(record["timestamp"])
    with open(report_path, encoding="utf-8") as handle:
        report = json.load(handle)
    with open(archive, "rb") as handle:
        artifact_sha256 = hashlib.sha256(handle.read()).hexdigest()
    return {
        "archive_path": archive,
        "artifact_sha256": artifact_sha256,
        "event_ids": event_ids,
        "count": len(event_ids),
        "unique": len(set(event_ids)),
        "min_ts": min(timestamps),
        "max_ts": max(timestamps),
        "id_set_sha256": sha256_of_set(event_ids),
        "report": report,
    }


def cutoff_literal(run_date: date, retention_days: int) -> str:
    return (run_date - timedelta(days=retention_days)).strftime("%Y-%m-%d 00:00:00")


def silver_state(catalog: str, ns: str, cutoff: str) -> dict:
    rows = dbx.sql(f"""
        SELECT count(*), count(DISTINCT event_id), max(event_ts), min(event_ts),
               count(CASE WHEN raw_payload IS NULL OR archived_at IS NULL THEN 1 END)
        FROM {catalog}.silver.audit_events_archived
        WHERE ns = '{ns}' AND event_ts < TIMESTAMP '{cutoff}'
    """)[0]
    ids = [r[0] for r in dbx.sql(f"""
        SELECT event_id FROM {catalog}.silver.audit_events_archived
        WHERE ns = '{ns}' AND event_ts < TIMESTAMP '{cutoff}'
        ORDER BY event_id
    """)]
    return {
        "rows": int(rows[0]),
        "distinct_event_ids": int(rows[1]),
        "max_event_ts": rows[2],
        "min_event_ts": rows[3],
        "incomplete_rows": int(rows[4]),
        "event_ids": ids,
        "id_set_sha256": sha256_of_set(ids),
    }


def manifest_rows(catalog: str, ns: str, run_date: date) -> list[dict]:
    columns = [
        "ns", "run_date", "cutoff_ts", "candidate_count", "archived_count",
        "deleted_count", "verified", "retention_days",
    ]
    rows = dbx.sql(f"""
        SELECT {', '.join(columns)}
        FROM {catalog}.gold.audit_archive_manifest
        WHERE ns = '{ns}' AND run_date = DATE '{run_date.isoformat()}'
    """)
    return [dict(zip(columns, row)) for row in rows]


def run_checks(args) -> tuple[list[Check], dict]:
    catalog, ns = args.catalog, args.ns
    run_date = date.fromisoformat(args.run_date)
    cutoff = cutoff_literal(run_date, args.retention_days)

    baseline = read_baseline(args.baseline_dir)
    silver = silver_state(catalog, ns, cutoff)
    manifest = manifest_rows(catalog, ns, run_date)

    checks = [
        Check(1, "selection parity: archived event_id set == legacy archive set"),
        Check(2, "count parity: manifest counts == silver rows == legacy count"),
        Check(3, "retention safety: no purge without verified; archive still readable"),
        Check(4, "idempotency: a second run archives nothing new and duplicates nothing"),
        Check(5, "provenance: baseline tier stated verbatim"),
    ]

    # ---- 1. selection parity -------------------------------------------------
    baseline_ids, silver_ids = set(baseline["event_ids"]), set(silver["event_ids"])
    checks[0].record(
        baseline_ids == silver_ids,
        legacy_count=baseline["count"],
        converted_count=silver["rows"],
        legacy_id_set_sha256=baseline["id_set_sha256"],
        converted_id_set_sha256=silver["id_set_sha256"],
        missing_from_converted=sorted(baseline_ids - silver_ids)[:10],
        missing_count=len(baseline_ids - silver_ids),
        extra_in_converted=sorted(silver_ids - baseline_ids)[:10],
        extra_count=len(silver_ids - baseline_ids),
        cutoff_ts=cutoff,
        legacy_cutoff=baseline["report"]["retention_policy"]["cutoff_date"],
        boundary_max_archived_event_ts=str(silver["max_event_ts"]),
        legacy_max_archived_ts=baseline["max_ts"],
    )

    # ---- 2. count parity ----------------------------------------------------
    if len(manifest) != 1:
        checks[1].block(
            f"expected exactly one manifest row for ({ns}, {run_date}), found {len(manifest)}",
            manifest_rows=manifest,
        )
    else:
        row = manifest[0]
        candidate, archived = int(row["candidate_count"]), int(row["archived_count"])
        checks[1].record(
            candidate == archived == silver["rows"] == baseline["count"],
            legacy_events_archived=baseline["report"]["results"]["events_archived"],
            legacy_events_scanned=baseline["report"]["results"]["events_scanned"],
            manifest_candidate_count=candidate,
            manifest_archived_count=archived,
            silver_rows=silver["rows"],
            silver_distinct_event_ids=silver["distinct_event_ids"],
        )

    # ---- 3. retention safety ------------------------------------------------
    unverified_purges = int(dbx.sql(f"""
        SELECT count(*) FROM {catalog}.gold.audit_archive_manifest
        WHERE deleted_count > 0 AND NOT verified
    """)[0][0])
    orphans = int(dbx.sql(f"""
        SELECT count(*) FROM (
          SELECT ns, event_id FROM {catalog}.bronze.audit_events_raw
          WHERE ns = '{ns}' AND event_ts < TIMESTAMP '{cutoff}'
        ) AS b
        LEFT ANTI JOIN {catalog}.silver.audit_events_archived AS a
          ON a.ns = b.ns AND a.event_id = b.event_id
    """)[0][0])
    readable = int(dbx.sql(f"""
        SELECT count(*) FROM {catalog}.silver.audit_events_archived
        WHERE ns = '{ns}' AND event_ts < TIMESTAMP '{cutoff}'
          AND event_id IS NOT NULL AND raw_payload IS NOT NULL
          AND archived_at IS NOT NULL AND retention_days = {args.retention_days}
    """)[0][0])
    purged = int(dbx.sql(f"""
        SELECT count(*) FROM (
          SELECT ns, event_id FROM {catalog}.silver.audit_events_archived
          WHERE ns = '{ns}' AND event_ts < TIMESTAMP '{cutoff}'
        ) AS a
        LEFT ANTI JOIN {catalog}.bronze.audit_events_raw AS b
          ON b.ns = a.ns AND b.event_id = a.event_id
    """)[0][0])
    manifest_deleted = int(manifest[0]["deleted_count"]) if len(manifest) == 1 else -1
    manifest_verified = str(manifest[0]["verified"]).lower() == "true" if len(manifest) == 1 else False
    checks[2].record(
        unverified_purges == 0
        and orphans == 0
        and readable == baseline["count"]
        and silver["incomplete_rows"] == 0
        and purged == manifest_deleted
        and (manifest_deleted == 0 or manifest_verified),
        manifest_rows_with_unverified_purge=unverified_purges,
        source_candidates_without_archive_row=orphans,
        archive_rows_readable_after_purge=readable,
        archive_rows_with_missing_payload_or_provenance=silver["incomplete_rows"],
        source_rows_purged=purged,
        manifest_deleted_count=manifest_deleted,
        manifest_verified=manifest_verified,
        legacy_events_deleted_from_source=baseline["report"]["results"]["events_deleted_from_source"],
    )

    # ---- 4. idempotency -----------------------------------------------------
    if not args.rerun_job:
        checks[3].block("no --rerun-job given, so no second run was executed")
    else:
        before = {
            "silver_rows": silver["rows"],
            "silver_id_set_sha256": silver["id_set_sha256"],
            "manifest": manifest,
        }
        run = dbx.run_job(args.rerun_job, {
            "ns": ns,
            "run_date": args.run_date,
            "retention_days": str(args.retention_days),
            "catalog": catalog,
        })
        result_state = run.get("state", {}).get("result_state")
        after_silver = silver_state(catalog, ns, cutoff)
        after_manifest = manifest_rows(catalog, ns, run_date)
        duplicates = int(dbx.sql(f"""
            SELECT count(*) FROM (
              SELECT event_id FROM {catalog}.silver.audit_events_archived
              WHERE ns = '{ns}' GROUP BY event_id HAVING count(*) > 1
            )
        """)[0][0])
        unchanged_manifest = (
            len(after_manifest) == 1
            and len(before["manifest"]) == 1
            and after_manifest[0] == before["manifest"][0]
        )
        checks[3].record(
            result_state == "SUCCESS"
            and after_silver["rows"] == before["silver_rows"]
            and after_silver["id_set_sha256"] == before["silver_id_set_sha256"]
            and duplicates == 0
            and unchanged_manifest,
            rerun_job=args.rerun_job,
            rerun_result_state=result_state,
            rerun_url=run.get("run_page_url"),
            silver_rows_before=before["silver_rows"],
            silver_rows_after=after_silver["rows"],
            silver_id_set_sha256_before=before["silver_id_set_sha256"],
            silver_id_set_sha256_after=after_silver["id_set_sha256"],
            duplicate_event_ids=duplicates,
            manifest_rows_after=len(after_manifest),
            manifest_before=before["manifest"],
            manifest_after=after_manifest,
        )

    # ---- 5. provenance ------------------------------------------------------
    checks[4].record(
        args.baseline_tier in BASELINE_TIERS,
        tier=BASELINE_TIERS.get(args.baseline_tier, "unknown"),
        legacy_stdout=os.path.join(args.baseline_dir, "legacy_stdout.txt"),
        legacy_archive_artifact_sha256=baseline["artifact_sha256"],
    )

    context = {
        "ns": ns,
        "run_date": args.run_date,
        "retention_days": args.retention_days,
        "cutoff_ts": cutoff,
        "catalog": catalog,
        "baseline": {k: v for k, v in baseline.items() if k != "event_ids"},
        "silver": {k: v for k, v in silver.items() if k != "event_ids"},
        "manifest": manifest,
    }
    return checks, context


def render_report(checks: list[Check], context: dict, tier: str, aging: str, execution: str) -> str:
    verdict = (
        "green" if all(c.status == "pass" for c in checks)
        else "blocked" if any(c.status == "blocked" for c in checks) and not any(c.status == "FAIL" for c in checks)
        else "partial"
    )
    lines = [
        f"{BASELINE_TIERS[tier]}",
        "",
        "# Recon: `audit_archive_weekly.py` -> `ow_tp_audit_archive`",
        "",
        f"- recon_result: **{verdict}**",
        f"- baseline artifact: `{context['baseline']['archive_path']}` "
        f"(sha256 `{context['baseline']['artifact_sha256']}`), "
        f"{context['baseline']['count']} events",
        f"- namespace `{context['ns']}`, run_date `{context['run_date']}`, "
        f"retention_days `{context['retention_days']}`, cutoff `{context['cutoff_ts']}` (exclusive)",
        "",
        "## How the baseline was produced",
        "",
        aging,
        "",
        "## How the converted job was executed",
        "",
        execution,
        "",
        "## Acceptance checks",
        "",
        "| # | Check | Result |",
        "|---|---|---|",
    ]
    for check in checks:
        lines.append(f"| {check.number} | {check.name} | **{check.status}** |")
    lines.append("")
    for check in checks:
        lines.append(f"### {check.number}. {check.name} -- {check.status}")
        lines.append("")
        if check.error:
            lines += [f"blocked: {check.error}", ""]
        lines.append("```json")
        lines.append(json.dumps(check.detail, indent=2, default=str))
        lines.append("```")
        lines.append("")
    lines += [
        "## Context",
        "",
        "```json",
        json.dumps(context, indent=2, default=str),
        "```",
        "",
        f"_Generated by `scripts/tp_databricks/recon_audit_archive.py` at "
        f"{datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}._",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--run-date", required=True, help="execution date the legacy run used (UTC)")
    parser.add_argument("--retention-days", type=int, default=90)
    parser.add_argument("--catalog", default="ow_tp")
    parser.add_argument("--baseline-dir", default="/home/ubuntu/tp-golden/python/audit_archive_weekly")
    parser.add_argument("--baseline-tier", default="legacy output", choices=sorted(BASELINE_TIERS))
    parser.add_argument("--aging-note", default="", help="verbatim description of how events were aged")
    parser.add_argument("--execution-note", default="", help="verbatim description of how the converted job was run")
    parser.add_argument("--rerun-job", default=None, help="job to re-run for the idempotency check")
    parser.add_argument("--report", default=None, help="write the markdown report here")
    args = parser.parse_args()

    checks, context = run_checks(args)
    report = render_report(checks, context, args.baseline_tier, args.aging_note, args.execution_note)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(report)
    print(report)
    return 0 if all(c.status == "pass" for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
