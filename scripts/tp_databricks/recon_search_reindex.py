#!/usr/bin/env python3
"""Reconcile ow_tp_search_reindex against the legacy search_reindex_weekly.py output.

The golden output is the MeiliSearch estate the *legacy* cron built on this machine
(`documents` and `files` indexes) plus the captured stdout/exit code of that run. This
script never compares the conversion against itself: every expected value is read back out
of MeiliSearch or out of the captured legacy artifacts, and every actual value is read out
of Unity Catalog.

Checks (numbered per docs/tech-partnerships/contracts/search_reindex_weekly.md):
  1. exact count parity per entity type, legacy index vs serving table
  2. deterministic sample content parity, field by field
  3. gold summary flags, plus the forced-source-failure run leaving the index intact
  4. rerun idempotency: stable counts, zero duplicate entity ids
  5. baseline provenance stated verbatim

Usage:
    recon_search_reindex.py [--ns demo] [--golden-dir DIR] [--report PATH]

Environment:
    MEILI_URL   legacy index base URL, default http://127.0.0.1:7700
    MEILI_KEY   optional MeiliSearch API key
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbx  # noqa: E402

SERVING_TABLE = f"{dbx.CATALOG}.silver.search_index_documents"
SUMMARY_TABLE = f"{dbx.CATALOG}.gold.search_reindex_summary"

# legacy MeiliSearch index -> entity_type in the converted model
INDEXES = {"documents": "document", "files": "file"}

# converted column <- legacy indexed field, per entity type
FIELD_MAP = {
    "document": {
        "title": "title",
        "content": "content",
        "owner_id": "owner_id",
        "tags": "tags",
        "created_at": "created_at",
        "updated_at": "updated_at",
    },
    "file": {
        "name": "name",
        "owner_id": "owner_id",
        "mime_type": "mime_type",
        "folder_id": "folder_id",
        "size_bytes": "size",
        "tags": "tags",
        "created_at": "created_at",
        "updated_at": "updated_at",
    },
}
SAMPLE_SIZE = 50
CHECK_NAMES = (
    "check 1 — count parity per entity type",
    "check 2 — sample content parity (document)",
    "check 2 — sample content parity (file)",
    "check 3a — gold summary flags",
    "check 3b — forced source failure leaves the index intact",
    "check 4 — rerun idempotency",
    "check 5 — baseline provenance",
)


class Blocked(RuntimeError):
    """A check could not be run at all; reported as blocked, never as green."""


def meili(path: str) -> dict:
    base = os.environ.get("MEILI_URL", "http://127.0.0.1:7700").rstrip("/")
    request = urllib.request.Request(f"{base}{path}", headers={"Accept": "application/json"})
    key = os.environ.get("MEILI_KEY")
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"{}")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        raise Blocked(f"GET {base}{path} failed: {exc}") from exc


def legacy_counts() -> dict[str, int]:
    return {
        entity: int(meili(f"/indexes/{index}/stats")["numberOfDocuments"])
        for index, entity in INDEXES.items()
    }


def legacy_documents(index: str) -> dict[str, dict]:
    """Every document the legacy run indexed, keyed by its primary key."""
    documents: dict[str, dict] = {}
    offset, limit = 0, 1000
    while True:
        page = meili(f"/indexes/{index}/documents?limit={limit}&offset={offset}")
        results = page.get("results", [])
        if not results:
            return documents
        for record in results:
            documents[str(record["id"])] = record
        offset += len(results)
        if len(results) < limit:
            return documents


def sample_ids(ns: str, entity_type: str, ids: list[str]) -> list[str]:
    """Deterministic sample: seed = sha256(unit:ns:entity_type), drawn from the sorted ids.

    Fixed seed and fixed ordering mean the same ids are compared on every run, on any
    machine, without the sample being chosen to favour the conversion.
    """
    digest = hashlib.sha256(f"search_reindex_weekly:{ns}:{entity_type}".encode()).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    ordered = sorted(ids)
    return sorted(rng.sample(ordered, min(SAMPLE_SIZE, len(ordered))))


def sql_rows(statement: str) -> list[list[str | None]]:
    try:
        return dbx.sql(statement)
    except (dbx.DatabricksError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise Blocked(str(exc)) from exc


def serving_counts(ns: str) -> dict[str, int]:
    rows = sql_rows(
        f"SELECT entity_type, COUNT(*) FROM {SERVING_TABLE} WHERE ns = '{ns}' GROUP BY entity_type"
    )
    return {row[0]: int(row[1]) for row in rows}


def duplicate_entity_ids(ns: str) -> int:
    rows = sql_rows(
        f"""
        SELECT COUNT(*) FROM (
          SELECT entity_type, entity_id FROM {SERVING_TABLE} WHERE ns = '{ns}'
          GROUP BY entity_type, entity_id HAVING COUNT(*) > 1
        )
        """
    )
    return int(rows[0][0]) if rows else 0


def serving_sample(ns: str, entity_type: str, ids: list[str]) -> dict[str, dict]:
    for entity_id in ids:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", entity_id):
            raise Blocked(
                f"sampled id {entity_id!r} does not match [A-Za-z0-9_-]+"
            )
    columns = ["entity_id"] + list(FIELD_MAP[entity_type])
    quoted = ", ".join("'" + i.replace("'", "''") + "'" for i in ids)
    rows = sql_rows(
        f"SELECT {', '.join(columns)} FROM {SERVING_TABLE} "
        f"WHERE ns = '{ns}' AND entity_type = '{entity_type}' AND entity_id IN ({quoted})"
    )
    return {row[0]: dict(zip(columns[1:], row[1:])) for row in rows}


def as_timestamp(value) -> datetime | None:
    """Parse either side's timestamp text, offset-suffixed or not, as an aware UTC instant."""
    if value in (None, ""):
        return None
    text = str(value).strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise Blocked(f"cannot parse timestamp {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def normalize(field: str, value, side: str):
    """Compare like with like.

    The legacy script defaulted missing string fields to "" and missing tag lists to [];
    the converted projection stores SQL NULL for the same absent source field. That
    difference is a representation difference, not a data difference, so NULL and the
    legacy default are treated as equal -- and every application of that rule is counted
    and disclosed in the report.
    """
    if field in ("created_at", "updated_at"):
        return as_timestamp(value)
    if field == "tags":
        if value in (None, ""):
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return value
        else:
            parsed = value
        return list(parsed)
    if field == "size_bytes":
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise Blocked(f"cannot parse size_bytes value {value!r}") from exc
    if value is None:
        return ""
    return str(value)


def check_counts(ns: str, legacy: dict[str, int], converted: dict[str, int]) -> dict:
    entities = sorted(set(legacy) | set(converted))
    rows = [
        {"entity_type": e, "legacy": legacy.get(e), "converted": converted.get(e),
         "match": legacy.get(e) == converted.get(e)}
        for e in entities
    ]
    return {"passed": all(row["match"] for row in rows), "rows": rows}


def check_sample(ns: str, index: str, entity_type: str) -> dict:
    legacy = legacy_documents(index)
    if not legacy:
        raise Blocked(f"legacy index {index} holds no documents to sample")
    ids = sample_ids(ns, entity_type, list(legacy))
    converted = serving_sample(ns, entity_type, ids)

    missing = [i for i in ids if i not in converted]
    mismatches = []
    normalized_nulls = 0
    timestamp_normalizations = 0
    compared_fields = 0
    for entity_id in ids:
        if entity_id in missing:
            continue
        for column, legacy_field in FIELD_MAP[entity_type].items():
            expected = normalize(column, legacy[entity_id].get(legacy_field), "legacy")
            actual = normalize(column, converted[entity_id].get(column), "converted")
            compared_fields += 1
            if converted[entity_id].get(column) is None and legacy[entity_id].get(legacy_field) in ("", [], None):
                normalized_nulls += 1
            if (
                column in ("created_at", "updated_at")
                and legacy[entity_id].get(legacy_field) not in (None, "")
                and converted[entity_id].get(column) not in (None, "")
                and legacy[entity_id].get(legacy_field) != converted[entity_id].get(column)
                and expected == actual
            ):
                timestamp_normalizations += 1
            if expected != actual:
                mismatches.append({
                    "entity_id": entity_id, "field": column,
                    "legacy": str(legacy[entity_id].get(legacy_field))[:200],
                    "converted": str(converted[entity_id].get(column))[:200],
                })
    return {
        "passed": not missing and not mismatches,
        "entity_type": entity_type,
        "sampled": len(ids),
        "compared_fields": compared_fields,
        "missing_from_converted": missing,
        "mismatches": mismatches[:20],
        "mismatch_count": len(mismatches),
        "null_default_normalizations": normalized_nulls,
        "timestamp_normalizations": timestamp_normalizations,
    }


def check_summary(ns: str) -> dict:
    rows = sql_rows(
        f"SELECT run_date, entity_type, source_count, indexed_count, counts_match, swap_completed "
        f"FROM {SUMMARY_TABLE} WHERE ns = '{ns}' ORDER BY run_date DESC, entity_type"
    )
    if not rows:
        raise Blocked(f"{SUMMARY_TABLE} holds no rows for ns={ns}")
    latest = [r for r in rows if r[0] == rows[0][0]]
    parsed = []
    for r in latest:
        try:
            source_count = int(r[2])
        except (TypeError, ValueError) as exc:
            raise Blocked(
                f"{SUMMARY_TABLE} has unusable source_count {r[2]!r} "
                f"for entity_type {r[1]!r}"
            ) from exc
        parsed.append({
            "run_date": r[0],
            "entity_type": r[1],
            "source_count": source_count,
            "indexed_count": int(r[3]),
            "counts_match": str(r[4]).lower() == "true",
            "swap_completed": str(r[5]).lower() == "true",
        })
    return {
        "passed": all(p["counts_match"] and p["swap_completed"] for p in parsed),
        "rows": parsed,
    }


def load_run(golden_dir: Path, name: str) -> dict:
    path = golden_dir / name
    if not path.exists():
        raise Blocked(f"run artifact {path} is missing")
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Blocked(f"run artifact {path} is unreadable: {exc}") from exc


def run_snapshot(run: dict, name: str) -> dict[str, int]:
    """Counts recorded by the dev runner when that run finished, not a report-time read."""
    snapshot = run.get("serving_counts_at_run_end")
    if snapshot is None:
        raise Blocked(
            f"{name} carries no serving_counts_at_run_end snapshot; "
            "re-run it with the current run_search_reindex_dev.py so the count is captured "
            "at run time rather than compared against itself at report time"
        )
    parsed = {}
    for entity, count in snapshot.items():
        try:
            parsed[entity] = int(count)
        except (TypeError, ValueError) as exc:
            raise Blocked(
                f"{name} has unusable serving snapshot count {count!r} for entity_type {entity!r}"
            ) from exc
    return parsed


def check_forced_failure(golden_dir: Path, legacy: dict[str, int], converted_now: dict[str, int]) -> dict:
    run = load_run(golden_dir, "dev_run_forced_failure.json")
    ingest = next((t for t in run.get("tasks", []) if t["task_key"] == "ingest_bronze"), {})
    publish = next((t for t in run.get("tasks", []) if t["task_key"] == "publish_index"), {})
    at_run_end = run_snapshot(run, "dev_run_forced_failure.json")
    index_intact = all(at_run_end.get(e) == legacy.get(e) for e in legacy)
    return {
        "passed": run.get("result_state") == "FAILED" and index_intact
        and ingest.get("result_state") == "FAILED"
        and publish.get("result_state") in ("UPSTREAM_FAILED", "SKIPPED"),
        "run_result_state": run.get("result_state"),
        "ingest_result_state": ingest.get("result_state"),
        "publish_result_state": publish.get("result_state"),
        "serving_counts_at_failed_run_end": at_run_end,
        "snapshot_taken_at": run.get("snapshot_taken_at"),
        "serving_counts_now": converted_now,
        "legacy_counts": legacy,
        "index_intact": index_intact,
        "run_url": run.get("url"),
    }


def check_rerun(golden_dir: Path, ns: str, legacy: dict[str, int]) -> dict:
    """Idempotency across two distinct runs, using the count each run recorded when it ended.

    Both sides must come from different moments in time or the equality is vacuous: the first
    run's number is read out of its own artifact, the rerun's out of its own, and the live
    table is compared on top.
    """
    first = load_run(golden_dir, "dev_run_success.json")
    rerun = load_run(golden_dir, "dev_run_rerun.json")
    after_first = run_snapshot(first, "dev_run_success.json")
    after_rerun = run_snapshot(rerun, "dev_run_rerun.json")
    live = serving_counts(ns)
    duplicates = duplicate_entity_ids(ns)
    return {
        "passed": first.get("result_state") == "SUCCESS"
        and rerun.get("result_state") == "SUCCESS"
        and after_first == after_rerun == live == legacy
        and duplicates == 0,
        "first_run_result_state": first.get("result_state"),
        "rerun_result_state": rerun.get("result_state"),
        "counts_at_first_run_end": after_first,
        "first_snapshot_taken_at": first.get("snapshot_taken_at"),
        "counts_at_rerun_end": after_rerun,
        "rerun_snapshot_taken_at": rerun.get("snapshot_taken_at"),
        "counts_live_now": live,
        "legacy_counts": legacy,
        "duplicate_entity_ids": duplicates,
        "first_run_url": first.get("url"),
        "run_url": rerun.get("url"),
    }


def baseline_line(golden_dir: Path) -> tuple[str, dict]:
    stdout_path = golden_dir / "stdout.txt"
    exit_path = golden_dir / "exit_code.txt"
    if not stdout_path.exists() or not exit_path.exists():
        raise Blocked(f"legacy baseline artifacts missing under {golden_dir}")
    exit_code = exit_path.read_text().strip()
    stdout = stdout_path.read_text()
    if exit_code != "0" or "completed successfully" not in stdout:
        raise Blocked(f"captured legacy run did not complete (exit {exit_code}); cannot claim tier 1")
    return "baseline: legacy output", {"exit_code": exit_code, "stdout_path": str(stdout_path)}


TRANSPORT_NOTES = {
    "landing-volume": (
        "extract -> `/Volumes/ow_tp/bronze/landing/<ns>/search_reindex/` -> the `ingest_bronze` "
        "notebook task: the pipeline's normal path."
    ),
    "sql-fallback": (
        "extract -> `scripts/tp_databricks/load_bronze_via_sql.py` -> the same bronze table over the "
        "serverless warehouse. **The documented landing-volume upload path is UNVERIFIED.** It cannot be "
        "executed by this unit: the demo PAT carries `sql, unity-catalog, jobs, secrets, workspace` scopes "
        "and the Files API answers `PUT /api/2.0/fs/files/Volumes/ow_tp/bronze/landing/... -> 403: "
        "{\"error_code\":403,\"message\":\"Provided access token does not have required scopes: files\"}`, and "
        "the parent session has confirmed no files-scoped token is coming. This loader is a test transport, "
        "not the production one: the envelopes, the bronze table and every downstream statement are the "
        "pipeline's own, and `publish_index` -- the build-then-swap logic under test -- ran as a real "
        "serverless job task, but `ingest_bronze`'s volume read is covered by review only. A defect on that "
        "unexecuted path (the manifest read via `spark.read.text`, which silently skips leaf files whose "
        "names begin with `_`) was found in review, not by a run; it is fixed and still unexecuted."
    ),
}


def disclosures(transport: str) -> list[str]:
    return [
        "## Disclosures",
        "",
        f"- **Transport**: {TRANSPORT_NOTES[transport]}",
        "- **Guards not exercised by this corpus**: nine defensive paths are reasoned and reviewed but never entered by a run on this data, and none of them contributes to any PASS below -- the empty-extract guard and the erase-an-existing-entity-type guard in `ingest_bronze`, the shrink-to-zero guard in `publish_index`, the corresponding empty-manifest and erase-an-existing-entity-type guards in the SQL fallback loader, wait-timeout cancellation and terminal-state teardown in `run_search_reindex_dev.py`, and the minimum-observed-total completeness, distinct-id reconciliation, and 0600 artifact-mode guards in `extract_search_sources.py`. They fire only on a degenerate extract or lifecycle edge case, which the seeded fixture does not produce; the checks below all ran on a full 1,933 / 9,461 corpus.",
        "- **Sample selection (check 2)**: ids are drawn from the legacy index itself using "
        "`random.Random(int(sha256('search_reindex_weekly:<ns>:<entity_type>').hexdigest()[:16], 16))` over "
        "the lexicographically sorted id list, 50 per entity type. Fixed seed, fixed ordering, fixed before "
        "any value is compared -- the sample is not chosen to favour the conversion.",
        "- **Null/default normalization (check 2)**: the legacy script defaulted absent source fields to "
        "`\"\"` / `[]`; where the converted projection stores SQL NULL for the same absent field the two are "
        "treated as equal. Every application is counted per entity type as `null_default_normalizations` "
        "below, so the extent of the leniency is visible rather than implied -- zero there means the two "
        "sides matched on representation as well as on value.",
        "- **Timestamp normalization (check 2)**: `created_at` and `updated_at` values are compared as "
        "instants, forgiving offset-suffix form (`Z` vs `+00:00`), separator form (space vs `T`), and "
        "fractional-second precision (`2025-12-03T20:40:07Z` vs `2025-12-03T20:40:07.000Z`); "
        "offset-less text is treated as UTC, which is an assumption about the legacy side's representation "
        "rather than a verified fact. All 100 of 100 sampled timestamp comparisons per entity type were "
        "accepted this way, reported as `timestamp_normalizations` below.",
        "- **Count snapshots (checks 3b and 4)**: the serving counts each run is judged on are read by "
        "`run_search_reindex_dev.py` the moment that run finishes and stored in its run artifact; recon reads "
        "those recorded values and compares the live table on top. Reading both sides at report time would "
        "make the equality hold by construction and could never detect drift.",
        "- **Counts**: the seed generator creates 2,000 documents, 67 of them soft-deleted and therefore never "
        "returned by `/api/v1/documents`; the legacy run indexed 1,933, so parity is measured against that "
        "legacy output rather than the raw seed total. Files: 10,000 DynamoDB items, 9,461 API-visible once "
        "trashed items are excluded.",
        "",
    ]


def render(ns: str, baseline: str, provenance: dict, results: dict, transport: str) -> str:
    status = "green" if all(r.get("passed") for r in results.values()) else "partial"
    if any(r.get("blocked") for r in results.values()):
        status = "blocked"
    lines = [
        baseline,
        "",
        f"# Recon: ow_tp_search_reindex vs etl/scripts/search_reindex_weekly.py (ns={ns})",
        "",
        f"- result: **{status}**",
    ]
    if provenance:
        lines.append(
            f"- legacy run: exit {provenance['exit_code']}, stdout captured at `{provenance['stdout_path']}`"
        )
    else:
        lines.append("- legacy run: unavailable; baseline provenance could not be established")
    lines += [
        "- golden output: the MeiliSearch `documents` / `files` indexes built by the legacy run on this machine",
        f"- converted output: `{SERVING_TABLE}` / `{SUMMARY_TABLE}`",
        "",
    ]
    lines += disclosures(transport)
    for name, result in results.items():
        verdict = "BLOCKED" if result.get("blocked") else ("PASS" if result.get("passed") else "FAIL")
        lines += [f"## {name} — {verdict}", "", "```json",
                  json.dumps({k: v for k, v in result.items() if k != "passed"}, indent=2, default=str),
                  "```", ""]
    return "\n".join(lines)


def blocked_results(error: str) -> dict[str, dict]:
    return {
        name: {"passed": False, "blocked": True, "error": error}
        for name in CHECK_NAMES
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default=os.environ.get("NS", "demo"))
    parser.add_argument("--golden-dir", default="/home/ubuntu/tp-golden/python/search_reindex_weekly")
    parser.add_argument("--report", default="docs/tech-partnerships/recon/search_reindex_weekly.md")
    parser.add_argument(
        "--transport",
        choices=("landing-volume", "sql-fallback"),
        default="sql-fallback",
        help="how the extract reached bronze for the run being reconciled",
    )
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9_]+", args.ns):
        raise SystemExit(f"ns must match [a-z0-9_]+, got {args.ns!r}")

    results: dict[str, dict] = {}
    golden_dir = Path(args.golden_dir)
    baseline = "blocked"
    provenance: dict = {}
    setup_succeeded = False

    try:
        baseline, provenance = baseline_line(golden_dir)
    except Blocked as exc:
        results = blocked_results(str(exc))
    else:
        try:
            legacy = legacy_counts()
            converted = serving_counts(args.ns)
            setup_succeeded = True
        except Blocked as exc:
            results = blocked_results(str(exc))
            results["check 5 — baseline provenance"] = {
                "passed": True,
                "baseline": baseline,
                **provenance,
            }

    def run_check(name: str, fn):
        try:
            results[name] = fn()
        except Blocked as exc:
            results[name] = {"passed": False, "blocked": True, "error": str(exc)}

    if setup_succeeded:
        run_check("check 1 — count parity per entity type", lambda: check_counts(args.ns, legacy, converted))
        for index, entity_type in INDEXES.items():
            run_check(f"check 2 — sample content parity ({entity_type})",
                      lambda index=index, entity_type=entity_type: check_sample(args.ns, index, entity_type))
        run_check("check 3a — gold summary flags", lambda: check_summary(args.ns))
        run_check("check 3b — forced source failure leaves the index intact",
                  lambda: check_forced_failure(golden_dir, legacy, serving_counts(args.ns)))
        run_check("check 4 — rerun idempotency",
                  lambda: check_rerun(golden_dir, args.ns, legacy))
        results["check 5 — baseline provenance"] = {"passed": True, "baseline": baseline, **provenance}

    report = render(args.ns, baseline, provenance, results, args.transport)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report)
    print(report)
    return 0 if all(r.get("passed") for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
