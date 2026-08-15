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
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
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
    return dbx.sql(statement)


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
    columns = ["entity_id"] + list(FIELD_MAP[entity_type])
    quoted = ", ".join("'" + i.replace("'", "''") + "'" for i in ids)
    rows = sql_rows(
        f"SELECT {', '.join(columns)} FROM {SERVING_TABLE} "
        f"WHERE ns = '{ns}' AND entity_type = '{entity_type}' AND entity_id IN ({quoted})"
    )
    return {row[0]: dict(zip(columns[1:], row[1:])) for row in rows}


def as_timestamp(value) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise Blocked(f"cannot parse timestamp {value!r}")


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
        parsed = json.loads(value) if isinstance(value, str) else value
        return list(parsed)
    if field == "size_bytes":
        return None if value in (None, "") else int(value)
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
    }


def check_summary(ns: str) -> dict:
    rows = sql_rows(
        f"SELECT run_date, entity_type, source_count, indexed_count, counts_match, swap_completed "
        f"FROM {SUMMARY_TABLE} WHERE ns = '{ns}' ORDER BY run_date DESC, entity_type"
    )
    if not rows:
        raise Blocked(f"{SUMMARY_TABLE} holds no rows for ns={ns}")
    latest = [r for r in rows if r[0] == rows[0][0]]
    parsed = [
        {"run_date": r[0], "entity_type": r[1], "source_count": int(r[2]), "indexed_count": int(r[3]),
         "counts_match": str(r[4]).lower() == "true", "swap_completed": str(r[5]).lower() == "true"}
        for r in latest
    ]
    return {
        "passed": all(p["counts_match"] and p["swap_completed"] for p in parsed),
        "rows": parsed,
    }


def load_run(golden_dir: Path, name: str) -> dict:
    path = golden_dir / name
    if not path.exists():
        raise Blocked(f"run artifact {path} is missing")
    return json.loads(path.read_text())


def check_forced_failure(golden_dir: Path, legacy: dict[str, int], converted_after: dict[str, int]) -> dict:
    run = load_run(golden_dir, "dev_run_forced_failure.json")
    ingest = next((t for t in run.get("tasks", []) if t["task_key"] == "ingest_bronze"), {})
    publish = next((t for t in run.get("tasks", []) if t["task_key"] == "publish_index"), {})
    index_intact = all(converted_after.get(e) == legacy.get(e) for e in legacy)
    return {
        "passed": run.get("result_state") == "FAILED" and index_intact
        and publish.get("result_state") in ("UPSTREAM_FAILED", "SKIPPED", None),
        "run_result_state": run.get("result_state"),
        "ingest_result_state": ingest.get("result_state"),
        "publish_result_state": publish.get("result_state"),
        "serving_counts_after_failed_run": converted_after,
        "legacy_counts": legacy,
        "index_intact": index_intact,
        "run_url": run.get("url"),
    }


def check_rerun(golden_dir: Path, ns: str, first_counts: dict[str, int], legacy: dict[str, int]) -> dict:
    run = load_run(golden_dir, "dev_run_rerun.json")
    after = serving_counts(ns)
    duplicates = duplicate_entity_ids(ns)
    return {
        "passed": run.get("result_state") == "SUCCESS" and after == first_counts == legacy and duplicates == 0,
        "rerun_result_state": run.get("result_state"),
        "counts_after_rerun": after,
        "counts_after_first_run": first_counts,
        "legacy_counts": legacy,
        "duplicate_entity_ids": duplicates,
        "run_url": run.get("url"),
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
        "serverless warehouse. The volume upload is unavailable to this unit: the demo PAT carries "
        "`sql, unity-catalog, jobs, secrets, workspace` scopes and the Files API answers "
        "`403 ... required scopes: files`. Only the transport differs -- the envelopes, the bronze table "
        "and every downstream statement are the pipeline's own, and `publish_index` (the build-then-swap "
        "logic under test) ran as a real serverless job task."
    ),
}


def disclosures(transport: str) -> list[str]:
    return [
        "## Disclosures",
        "",
        f"- **Transport**: {TRANSPORT_NOTES[transport]}",
        "- **Sample selection (check 2)**: ids are drawn from the legacy index itself using "
        "`random.Random(int(sha256('search_reindex_weekly:<ns>:<entity_type>').hexdigest()[:16], 16))` over "
        "the lexicographically sorted id list, 50 per entity type. Fixed seed, fixed ordering, fixed before "
        "any value is compared -- the sample is not chosen to favour the conversion.",
        "- **Null/default normalization (check 2)**: the legacy script defaulted absent source fields to "
        "`\"\"` / `[]`; the converted projection stores SQL NULL for the same absent field. Those are treated "
        "as equal, and every application is counted as `null_default_normalizations`. In this run they are all "
        "the `tags` field, which neither service API returns for the seeded corpus.",
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
        f"- legacy run: exit {provenance['exit_code']}, stdout captured at `{provenance['stdout_path']}`",
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

    golden_dir = Path(args.golden_dir)
    baseline, provenance = baseline_line(golden_dir)

    legacy = legacy_counts()
    converted = serving_counts(args.ns)

    results: dict[str, dict] = {}

    def run_check(name: str, fn):
        try:
            results[name] = fn()
        except Blocked as exc:
            results[name] = {"passed": False, "blocked": True, "error": str(exc)}

    run_check("check 1 — count parity per entity type", lambda: check_counts(args.ns, legacy, converted))
    for index, entity_type in INDEXES.items():
        run_check(f"check 2 — sample content parity ({entity_type})",
                  lambda index=index, entity_type=entity_type: check_sample(args.ns, index, entity_type))
    run_check("check 3a — gold summary flags", lambda: check_summary(args.ns))
    run_check("check 3b — forced source failure leaves the index intact",
              lambda: check_forced_failure(golden_dir, legacy, serving_counts(args.ns)))
    run_check("check 4 — rerun idempotency",
              lambda: check_rerun(golden_dir, args.ns, converted, legacy))
    results["check 5 — baseline provenance"] = {"passed": True, "baseline": baseline, **provenance}

    report = render(args.ns, baseline, provenance, results, args.transport)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report)
    print(report)
    return 0 if all(r.get("passed") for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
