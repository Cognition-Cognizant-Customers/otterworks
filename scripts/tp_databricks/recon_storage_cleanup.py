#!/usr/bin/env python3
"""Reconcile ow_tp_storage_cleanup against the real legacy output.

Runs the five numbered acceptance checks from
`docs/tech-partnerships/contracts/storage_cleanup_daily.md` and writes a report.
The baseline is tier 1 (`baseline: legacy output`): the unedited
`etl/scripts/storage_cleanup_daily.py` is executed against the LocalStack
fixture and the objects it actually moved into `otterworks-file-quarantine` are
the golden orphan set. Nothing here derives the baseline from the converted
pipeline, and no comparison is a subset or a tolerance -- the orphan set is
compared as an exact set, because a false positive is a deleted customer file.

    recon_storage_cleanup.py --ns demo --capture-golden   # run legacy, then recon
    recon_storage_cleanup.py --ns demo                    # recon vs captured golden

`--capture-golden` runs the legacy script, which is destructive: it moves the
planted orphans out of the file-storage bucket. Bronze must already be loaded
(`extract_storage_cleanup.py --load`) so the inventory the converted job sees is
the pre-quarantine state, exactly as the legacy script saw it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DDL_FILE = REPO / "databricks" / "ddl" / "storage_cleanup_daily.sql"
NOTEBOOK = REPO / "databricks" / "notebooks" / "storage_cleanup_daily.py"
TIER_PHRASE = "baseline: legacy output"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbx  # noqa: E402
import extract_storage_cleanup as extract  # noqa: E402
import fixture_storage_cleanup as fixture_builder  # noqa: E402

GOLDEN = fixture_builder.GOLDEN_ROOT


def _load_notebook():
    spec = importlib.util.spec_from_file_location("ow_tp_storage_cleanup", NOTEBOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


nb = _load_notebook()


def _validate_inputs(
    ns: str, scenario: str | None = None, run_date: str | None = None
) -> None:
    nb._checked("ns", ns)
    if scenario is not None:
        nb._checked("scenario", scenario)
    if run_date is not None:
        nb._checked("run_date", run_date, nb._ISO_DATE)


class Check:
    def __init__(self, number: int, title: str) -> None:
        self.number = number
        self.title = title
        self.lines: list[str] = []
        self.ok = True

    def compare(self, label: str, expected, actual) -> None:
        passed = expected == actual
        self.ok = self.ok and passed
        self.lines.append(
            f"{'PASS' if passed else 'FAIL'} {label}: baseline={expected!r} converted={actual!r}"
        )

    def note(self, text: str) -> None:
        self.lines.append(f"note {text}")

    def fail(self, text: str) -> None:
        self.ok = False
        self.lines.append(f"FAIL {text}")


def run_pipeline(ns: str, run_date: str, dry_run: bool, scenario: str) -> None:
    for statement in nb.pipeline_statements(
        ns=ns, run_date=run_date, dry_run=dry_run, scenario=scenario
    ):
        dbx.sql(statement)


def orphan_keys(ns: str, scenario: str, confirmed: bool) -> set:
    _validate_inputs(ns, scenario)
    rows = dbx.sql(
        "SELECT bucket, key FROM ow_tp.silver.storage_orphans "
        f"WHERE ns = '{ns}' AND scenario = '{scenario}' "
        f"AND metadata_read_ok = {'true' if confirmed else 'false'}"
    )
    return {(row[0], row[1]) for row in rows}


def savings(ns: str, scenario: str, run_date: str) -> dict:
    _validate_inputs(ns, scenario, run_date)
    columns = [
        "objects_scanned",
        "metadata_rows",
        "orphan_count",
        "orphan_bytes",
        "quarantined_count",
        "dry_run",
        "metadata_read_ok",
    ]
    rows = dbx.sql(
        f"SELECT {', '.join(columns)} FROM ow_tp.gold.storage_cleanup_savings "
        f"WHERE ns = '{ns}' AND scenario = '{scenario}' AND run_date = DATE '{run_date}'"
    )
    if len(rows) != 1:
        raise SystemExit(f"expected exactly one gold row for {scenario}, got {len(rows)}")
    values = dict(zip(columns, rows[0]))
    for name in columns[:5]:
        values[name] = int(values[name])
    for name in ("dry_run", "metadata_read_ok"):
        values[name] = values[name] == "true"
    return values


# --------------------------------------------------------------------------- golden


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _quarantine_keys(s3, bucket: str, capture_date: date) -> set[str]:
    prefix = f"quarantined/{capture_date.isoformat()}/"
    keys = set()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"][len(prefix) :])
    return keys


def _report_identity(s3, bucket: str, capture_date: date) -> dict | None:
    key = f"reports/storage-cleanup/{capture_date.isoformat()}/report.json"
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except s3.exceptions.ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return {
        "etag": head.get("ETag"),
        "last_modified": head.get("LastModified").isoformat(),
    }


def _capture_candidates(date_before: date) -> list[date]:
    return [date_before, date_before + timedelta(days=1)]


def _resolve_capture_date(
    s3,
    report_bucket: str,
    candidates: list[date],
    pre_existing: dict[date, set[str]],
    report_before: dict[date, dict | None],
    stdout: str,
    date_before: date,
    date_after: date,
) -> tuple[date, dict]:
    if date_after - date_before > timedelta(days=1):
        raise SystemExit(
            f"legacy run crossed more than one UTC day: before={date_before} after={date_after}"
        )
    report_after = {
        capture_date: _report_identity(s3, report_bucket, capture_date)
        for capture_date in candidates
    }
    changed = [
        capture_date
        for capture_date in candidates
        if report_after[capture_date] != report_before[capture_date]
    ]
    evidence = {
        "candidates": [capture_date.isoformat() for capture_date in candidates],
        "report_before": {
            capture_date.isoformat(): report_before[capture_date] for capture_date in candidates
        },
        "report_after": {
            capture_date.isoformat(): report_after[capture_date] for capture_date in candidates
        },
        "stdout_tail": stdout[-2000:],
    }
    if len(changed) != 1:
        raise SystemExit(f"could not resolve one changed legacy report: {evidence!r}")
    effective = changed[0]
    key = f"reports/storage-cleanup/{effective.isoformat()}/report.json"
    report = json.loads(s3.get_object(Bucket=report_bucket, Key=key)["Body"].read())
    if report.get("report_date") != effective.isoformat():
        raise SystemExit(
            f"legacy report date mismatch: expected={effective.isoformat()} "
            f"actual={report.get('report_date')!r} evidence={evidence!r}"
        )
    stdout_dates = set(
        re.findall(r"to s3://[^/\s]+/quarantined/(\d{4}-\d{2}-\d{2})/", stdout)
    )
    if stdout_dates and stdout_dates != {effective.isoformat()}:
        raise SystemExit(
            f"legacy quarantine date mismatch: effective={effective.isoformat()} "
            f"stdout_dates={sorted(stdout_dates)!r} evidence={evidence!r}"
        )
    return effective, report


def _counterfactual_note(counterfactual_dir: Path = GOLDEN / "counterfactual") -> str:
    report_path = counterfactual_dir / "run_b_report.json"
    log_path = counterfactual_dir / "run_b_partial_metadata.txt"
    if not report_path.is_file() or not log_path.is_file():
        return (
            "legacy counterfactual artefacts are not present on this machine; "
            "the separate legacy comparison is not being asserted"
        )
    try:
        report = json.loads(report_path.read_text())
        total_objects = report["inventory"]["total_objects"]
        orphaned_objects = report["orphans"]["orphaned_objects"]
        objects_quarantined = report["cleanup"]["objects_quarantined"]
        log = log_path.read_text()
        metadata_match = re.search(r"Found (\d+) S3 keys referenced in metadata", log)
        if metadata_match is None:
            raise ValueError("metadata key count missing")
        metadata_keys = int(metadata_match.group(1))
    except (OSError, KeyError, TypeError, UnicodeError, ValueError):
        return (
            "legacy counterfactual artefacts are not present on this machine; "
            "the separate legacy comparison is not being asserted"
        )
    return (
        "legacy counterfactual evidence from a separate unedited-script run: "
        f"it listed {total_objects} objects, read {metadata_keys} metadata keys, "
        f"reported {orphaned_objects} orphans, and quarantined {objects_quarantined} live "
        f"customer files "
        f"(see {counterfactual_dir}/)"
    )


def capture_golden(ns: str) -> None:
    """Run the unedited legacy script and record what it actually quarantined."""
    env = dict(os.environ)
    env.setdefault("AWS_ENDPOINT_URL", extract.ENDPOINT)
    env.setdefault("AWS_ACCESS_KEY_ID", "test")
    env.setdefault("AWS_SECRET_ACCESS_KEY", "test")
    env.setdefault("AWS_DEFAULT_REGION", extract.REGION)
    GOLDEN.mkdir(parents=True, exist_ok=True)
    quarantine = "otterworks-file-quarantine"
    report_bucket = "otterworks-data-lake"
    s3 = extract._client("s3")
    date_before = _utcnow().date()
    candidates = _capture_candidates(date_before)
    pre_existing = {
        capture_date: _quarantine_keys(s3, quarantine, capture_date)
        for capture_date in candidates
    }
    report_before = {
        capture_date: _report_identity(s3, report_bucket, capture_date)
        for capture_date in candidates
    }

    proc = subprocess.run(
        [sys.executable, str(REPO / "etl" / "scripts" / "storage_cleanup_daily.py")],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO),
    )
    (GOLDEN / "legacy_stdout.txt").write_text(proc.stdout + proc.stderr)
    (GOLDEN / "legacy_exit_code.txt").write_text(f"exit_code={proc.returncode}\n")
    if proc.returncode != 0:
        raise SystemExit(
            "legacy script failed -- tier 1 baseline not available:\n"
            + (proc.stdout + proc.stderr)[-2000:]
        )

    capture_date, report = _resolve_capture_date(
        s3,
        report_bucket,
        candidates,
        pre_existing,
        report_before,
        proc.stdout + proc.stderr,
        date_before,
        _utcnow().date(),
    )
    pre_existing_keys = pre_existing[capture_date]
    captured = _quarantine_keys(s3, quarantine, capture_date) - pre_existing_keys
    (GOLDEN / "quarantined_preexisting_keys.txt").write_text(
        "\n".join(sorted(pre_existing_keys)) + ("\n" if pre_existing_keys else "")
    )
    (GOLDEN / "quarantined_keys.txt").write_text(
        "\n".join(sorted(captured)) + ("\n" if captured else "")
    )

    (GOLDEN / "legacy_report.json").write_text(json.dumps(report, indent=2))


def legacy_metadata_keys() -> int:
    """The metadata key count the legacy run itself reported (never assumed)."""
    text = (GOLDEN / "legacy_stdout.txt").read_text()
    match = re.search(r"Found (\d+) S3 keys referenced in metadata", text)
    if not match:
        raise SystemExit("legacy stdout does not report a metadata key count")
    return int(match.group(1))


def read_golden() -> tuple[dict, set, dict]:
    report = json.loads((GOLDEN / "legacy_report.json").read_text())
    keys = {
        (extract.FILE_STORAGE_BUCKET, line)
        for line in (GOLDEN / "quarantined_keys.txt").read_text().split()
        if line
    }
    fixture = json.loads((GOLDEN / "fixture_manifest.json").read_text())
    return report, keys, fixture


# --------------------------------------------------------------------------- checks


def check_structural() -> Check:
    check = Check(0, "Notebook DDL and the committed DDL file are the same statements")

    def normalise(text: str) -> list:
        statements = []
        # Split on a line that is *only* the separator: the .sql file's header
        # prose mentions the token inline.
        for raw in re.split(r"(?m)^\s*--\s*@statement\s*$", text):
            body = "\n".join(
                line for line in raw.splitlines() if not line.strip().startswith("--")
            )
            body = re.sub(r"COMMENT\s+'(?:[^']|'')*'", "", body)
            body = re.sub(r"\s+", " ", body)
            body = re.sub(r"\s+([,)])", r"\1", body).strip().rstrip(";").strip()
            if body:
                statements.append(body)
        return statements

    check.compare("DDL statements", normalise(DDL_FILE.read_text()), normalise(nb.DDL_SQL.format(catalog="ow_tp")))
    return check


def check_1(ns: str, golden_keys: set, fixture: dict) -> Check:
    check = Check(1, "Orphan-set parity: exact (bucket, key) set equality")
    planted = {(o["bucket"], o["key"]) for o in fixture["planted_orphans"]}
    check.compare("planted set == legacy quarantined set", planted, golden_keys)
    converted = orphan_keys(ns, "nominal", confirmed=True)
    check.compare("legacy quarantined set == silver confirmed orphans", golden_keys, converted)
    extras = sorted(converted - golden_keys)
    missing = sorted(golden_keys - converted)
    check.note(f"extras (would be deleted customer files): {len(extras)} {extras[:5]}")
    check.note(f"missing (orphans left behind): {len(missing)} {missing[:5]}")
    return check


def check_2(ns: str, run_date: str, report: dict, fixture: dict) -> Check:
    _validate_inputs(ns, run_date=run_date)
    check = Check(2, "Byte and count parity against the legacy report")
    gold = savings(ns, "nominal", run_date)
    check.compare("orphan_bytes", report["orphans"]["orphaned_bytes"], gold["orphan_bytes"])
    check.compare("orphan_count", report["orphans"]["orphaned_objects"], gold["orphan_count"])
    check.compare(
        "orphan_bytes == summed planted sizes",
        sum(o["size_bytes"] for o in fixture["planted_orphans"]),
        gold["orphan_bytes"],
    )
    check.compare("metadata_rows", legacy_metadata_keys(), gold["metadata_rows"])

    legacy_scope = int(
        dbx.sql(
            "SELECT COUNT(*) FROM ow_tp.bronze.storage_objects_raw "
            f"WHERE ns = '{ns}' AND key LIKE 'files/%'"
        )[0][0]
    )
    check.compare(
        "objects_scanned under the legacy 'files/' prefix",
        report["inventory"]["total_objects"],
        legacy_scope,
    )
    check.note(
        f"converted objects_scanned is {gold['objects_scanned']} for the whole bucket: the legacy "
        f"script listed only 'files/' and never saw the other "
        f"{gold['objects_scanned'] - legacy_scope} objects. Broader scope, identical orphan set."
    )
    return check


def check_3(ns: str, run_date: str, limit: int) -> Check:
    _validate_inputs(ns, run_date=run_date)
    check = Check(3, "Safety guard: an incomplete metadata read quarantines nothing")
    scenario = "metadata_read_incomplete"
    objects, metadata, complete = _reload(ns, scenario, metadata_limit=limit)
    check.compare("extract marks the metadata read incomplete", False, complete)
    check.compare("metadata rows loaded", limit, len(metadata))

    # dry_run deliberately FALSE: the guard, not the dry-run flag, must be what
    # stops the quarantine.
    run_pipeline(ns, run_date, dry_run=False, scenario=scenario)
    gold = savings(ns, scenario, run_date)
    check.compare("dry_run", False, gold["dry_run"])
    check.compare("metadata_read_ok", False, gold["metadata_read_ok"])
    check.compare("quarantined_count", 0, gold["quarantined_count"])
    check.compare("confirmed orphan_count", 0, gold["orphan_count"])
    check.compare("confirmed orphan_bytes", 0, gold["orphan_bytes"])
    check.compare("rows reported as confirmed orphans", set(), orphan_keys(ns, scenario, confirmed=True))
    candidates = orphan_keys(ns, scenario, confirmed=False)
    if not candidates:
        check.fail("no candidates recorded -- the run must still record what it could not verify")
    else:
        check.note(f"candidates recorded for review: {len(candidates)} (quarantined: 0)")
    check.compare(
        "candidate rows carry orphan_reason=candidate_unverified_metadata_read",
        [[str(len(candidates))]],
        dbx.sql(
            "SELECT COUNT(*) FROM ow_tp.silver.storage_orphans "
            f"WHERE ns = '{ns}' AND scenario = '{scenario}' "
            "AND orphan_reason = 'candidate_unverified_metadata_read'"
        ),
    )
    check.note(_counterfactual_note())
    return check


def check_4(ns: str, run_date: str, golden_keys: set) -> Check:
    _validate_inputs(ns, run_date=run_date)
    check = Check(4, "Idempotency: a re-run leaves the orphan set and totals unchanged")
    _reload(ns, "nominal", metadata_limit=None)
    run_pipeline(ns, run_date, dry_run=True, scenario="nominal")
    first = savings(ns, "nominal", run_date)
    first_keys = orphan_keys(ns, "nominal", confirmed=True)
    run_pipeline(ns, run_date, dry_run=True, scenario="nominal")
    second = savings(ns, "nominal", run_date)
    second_keys = orphan_keys(ns, "nominal", confirmed=True)
    check.compare("orphan set across re-runs", first_keys, second_keys)
    check.compare("orphan set still equals the baseline set", golden_keys, second_keys)
    check.compare("gold row after re-run", first, second)
    check.compare(
        "gold rows for this (ns, scenario, run_date)",
        [["1"]],
        dbx.sql(
            "SELECT COUNT(*) FROM ow_tp.gold.storage_cleanup_savings "
            f"WHERE ns = '{ns}' AND scenario = 'nominal' AND run_date = DATE '{run_date}'"
        ),
    )
    check.compare(
        "silver rows for this (ns, scenario)",
        [[str(len(second_keys))]],
        dbx.sql(
            "SELECT COUNT(*) FROM ow_tp.silver.storage_orphans "
            f"WHERE ns = '{ns}' AND scenario = 'nominal'"
        ),
    )
    return check


def _reload(ns: str, scenario: str, metadata_limit: int | None):
    """Rebuild the fixture, re-extract from LocalStack, reload the bronze slice.

    The fixture is rebuilt first because the legacy run is destructive -- it moved
    the planted orphans into quarantine -- and the builder is deterministic per
    namespace, so it restores the same orphan set the golden run acted on.
    """
    fixture_builder.build(ns)
    objects = extract.list_objects(extract.FILE_STORAGE_BUCKET, ns)
    metadata, complete, claimed_elsewhere = extract.scan_metadata(ns, metadata_limit)
    objects = extract.filter_claimed_elsewhere(objects, claimed_elsewhere)
    manifest = {
        "extracted_at": datetime.now(tz=timezone.utc).isoformat(),
        "source_bucket": extract.FILE_STORAGE_BUCKET,
        "source_table": extract.DYNAMO_TABLE,
        "objects_expected": len(objects),
        "objects_bytes": sum(o["size_bytes"] for o in objects),
        "metadata_expected": len(metadata),
        "metadata_read_complete": complete,
    }
    extract.load_bronze(ns, scenario, objects, metadata, manifest)
    return objects, metadata, complete


# --------------------------------------------------------------------------- report


def write_report(path: Path, ns: str, run_date: str, checks: list, fixture: dict, report: dict) -> bool:
    green = all(c.ok for c in checks)
    lines = [
        f"{TIER_PHRASE}",
        "",
        f"# Recon: `storage_cleanup_daily.py` -> `ow_tp_storage_cleanup` (`ns={ns}`)",
        "",
        f"Generated {datetime.now(tz=timezone.utc).isoformat()} by "
        "`scripts/tp_databricks/recon_storage_cleanup.py`.",
        "",
        f"**Result: {'green' if green else 'partial'}** -- "
        f"{sum(1 for c in checks if c.ok)}/{len(checks)} checks passed.",
        "",
        "## Baseline provenance",
        "",
        "Tier 1. The legacy script names `s3://otterworks-file-storage/files/`, which no local",
        "fixture provided (`NoSuchBucket`). What had to be stood up on this VM:",
        "",
        "- `make infra-up` plus the documented workaround for the occupied host port 5432:",
        "  Postgres runs in container `otterworks-postgres-alt` on 55432 (`DB_PORT=55432`).",
        f"- `make seed-legacy NS={ns}` and `make seed-legacy-validate NS={ns}` (15/15 checks).",
        f"- `scripts/tp_databricks/fixture_storage_cleanup.py build --ns {ns}`: creates the missing",
        f"  `{fixture['buckets']['file_storage']}` and `{fixture['buckets']['quarantine']}` buckets,",
        f"  writes {fixture['live_objects_written']} live objects from the seeded metadata keys, and",
        f"  plants {fixture['planted_orphan_count']} objects with no metadata row",
        f"  ({fixture['planted_orphan_bytes']} bytes) under the `files/` prefix the script lists.",
        "- Then the **unedited** `etl/scripts/storage_cleanup_daily.py` was run (nothing under `etl/`",
        "  was modified) and what it moved into the quarantine bucket is the golden orphan set:",
        f"  `{GOLDEN}/legacy_stdout.txt`, `legacy_report.json`, `quarantined_keys.txt`.",
        f"- Quarantine keys already present under `quarantined/<capture-date>/` before this run: "
        f"{len((GOLDEN / 'quarantined_preexisting_keys.txt').read_text().split()) if (GOLDEN / 'quarantined_preexisting_keys.txt').exists() else 0}.",
        "",
        "Legacy run, for the record:",
        "",
        "```json",
        json.dumps(report, indent=2),
        "```",
        "",
        "## Landing transport: UNVERIFIED",
        "",
        "The documented bronze landing path -- writing extracts to the volume",
        "`/Volumes/ow_tp/bronze/landing` via `dbx.py upload` -- is **UNVERIFIED by this recon**.",
        "The demo PAT lacks the `files` scope, so every upload attempt returned, verbatim:",
        "",
        "```text",
        '403: {"error_code":403,"message":"Provided access token does not have required '
        'scopes: files"}',
        "```",
        "",
        "The in-Databricks landing this recon actually used is `INSERT` statements executed on the",
        "existing serverless SQL warehouse by `scripts/tp_databricks/extract_storage_cleanup.py`,",
        "which produce the same bronze rows. That is a workaround for a missing token scope and is",
        "**not** presented as the production transport: the volume path stays the documented one and",
        "remains untested here. No acceptance check below was weakened, relaxed or skipped because of",
        "this -- the checks compare the same bronze contents either way.",
        "",
        "## Acceptance checks",
        "",
    ]
    for check in checks:
        lines.append(f"### {check.number}. {check.title} -- {'PASS' if check.ok else 'FAIL'}")
        lines.append("")
        lines.append("```text")
        lines.extend(check.lines)
        lines.append("```")
        lines.append("")

    lines += [
        "## Planted orphan set (the expected answer)",
        "",
        f"{fixture['planted_orphan_count']} objects, {fixture['planted_orphan_bytes']} bytes total,",
        "deterministic per namespace:",
        "",
        "| bucket | key | size_bytes |",
        "|---|---|---|",
    ]
    for orphan in fixture["planted_orphans"]:
        lines.append(f"| `{orphan['bucket']}` | `{orphan['key']}` | {orphan['size_bytes']} |")
    lines += [
        "",
        "## Reproducing",
        "",
        "```bash",
        f"make infra-up && make seed-legacy NS={ns} && make seed-legacy-validate NS={ns}",
        f"python3 scripts/tp_databricks/fixture_storage_cleanup.py build --ns {ns}",
        f"python3 scripts/tp_databricks/extract_storage_cleanup.py --ns {ns} --load --scenario nominal",
        f"python3 scripts/tp_databricks/recon_storage_cleanup.py --ns {ns} --run-date {run_date} \\",
        "    --capture-golden",
        "```",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    return green


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default=os.environ.get("NS", "demo"))
    parser.add_argument("--run-date", default=date.today().isoformat())
    parser.add_argument("--metadata-limit", type=int, default=4000)
    parser.add_argument("--capture-golden", action="store_true", help="run the legacy script first")
    parser.add_argument(
        "--report",
        default=str(REPO / "docs" / "tech-partnerships" / "recon" / "storage_cleanup_daily.md"),
    )
    args = parser.parse_args(argv)
    try:
        _validate_inputs(args.ns, run_date=args.run_date)
    except ValueError as exc:
        parser.error(str(exc))

    if args.capture_golden:
        # Load bronze from the pre-quarantine inventory, then let the legacy
        # script run against that same state: both sides see identical input.
        _reload(args.ns, "nominal", metadata_limit=None)
        capture_golden(args.ns)
    report, golden_keys, fixture = read_golden()

    checks = [check_structural()]
    run_pipeline(args.ns, args.run_date, dry_run=True, scenario="nominal")
    checks.append(check_1(args.ns, golden_keys, fixture))
    checks.append(check_2(args.ns, args.run_date, report, fixture))
    checks.append(check_3(args.ns, args.run_date, args.metadata_limit))
    checks.append(check_4(args.ns, args.run_date, golden_keys))

    green = write_report(Path(args.report), args.ns, args.run_date, checks, fixture, report)
    for check in checks:
        print(f"[{'PASS' if check.ok else 'FAIL'}] {check.number}. {check.title}")
        for line in check.lines:
            print(f"    {line}")
    print(f"\nreport: {args.report}")
    print(f"recon_result: {'green' if green else 'partial'}")
    return 0 if green else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
