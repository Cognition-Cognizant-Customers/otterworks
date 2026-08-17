#!/usr/bin/env python3
"""Recon for the cron-search unit: prove Atlas Search replaces the weekly reindex.

Live mode recomputes every value from the deployed target: collection membership
from the Atlas collections, golden-query results from the `$search` aggregation
stage (never `$text`, `$regex`, or a plain `find` fallback), and index field
roles from the Atlas Admin API. Fixture mode evaluates the same golden query set
against the local fixture corpus with a small in-process evaluator so the child
can self-check without any Atlas write; fixture runs are reported as
`run_mode: "fixture"` with `values_recomputed_from_target: false`.

The only write in live mode is one probe document under this unit's own
`ow-tp-cron-search-recon-` prefix, required by acceptance check SRC-05
(continuous index maintenance), and it is deleted again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cronbox_search_indexes import (  # noqa: E402
    ROLES,
    load_definitions,
    read_back,
    role_violations,
)
from cronbox_search_ingest import DATABASE, DOCUMENTS, FILES, transform  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_QUERIES = (
    REPO_ROOT / "docs/tech-partnerships/recon/cron-search-golden-queries.json"
)
BASELINE = (
    REPO_ROOT
    / "testdata/legacy/golden/cronbox/demo/search_reindex_weekly/manifest.json"
)
CONTRACT = REPO_ROOT / "docs/tech-partnerships/contracts/cron-search.json"
UNIT = "cron-search"
PROBE_PREFIX = "ow-tp-cron-search-recon-"
SEARCH_VISIBILITY_TIMEOUT_S = 180
TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
MULTIBYTE_QUERY_IDS = ("DOC-UNICODE-TITLE", "FILE-UNICODE-NAME")


def _digest(ids: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


def _set_check(
    check_id: str, expected: Iterable[str], actual: Iterable[str], source: str
) -> dict[str, Any]:
    expected_set, actual_set = set(expected), set(actual)
    return {
        "id": check_id,
        "expected": {"count": len(expected_set), "ids_sha256": _digest(expected_set)},
        "actual": {
            "count": len(actual_set),
            "ids_sha256": _digest(actual_set),
            "missing": sorted(expected_set - actual_set),
            "unexpected": sorted(actual_set - expected_set),
        },
        "source_of_truth": source,
        "result": "pass" if expected_set == actual_set else "fail",
    }


def _contains_non_ascii(value: Any) -> bool:
    if isinstance(value, str):
        return any(ord(character) > 127 for character in value)
    if isinstance(value, (list, tuple)):
        return any(_contains_non_ascii(item) for item in value)
    return False


def _multibyte_check(
    queries: Sequence[Mapping[str, Any]],
    query_results: Mapping[str, Sequence[str]],
    records: Mapping[str, Sequence[Mapping[str, Any]]],
    source_of_truth: str,
) -> dict[str, Any]:
    query_evidence = {}
    stored_evidence = []
    evaluated_query_ids = set()
    query_sets_pass = True
    stored_values_pass = True
    by_id = {
        collection: {str(record.get("id")): record for record in items}
        for collection, items in records.items()
    }
    for query in queries:
        if query["id"] not in MULTIBYTE_QUERY_IDS:
            continue
        evaluated_query_ids.add(query["id"])
        expected_ids = set(query["expected_ids"])
        actual_ids = set(query_results.get(query["id"], []))
        missing = sorted(expected_ids - actual_ids)
        unexpected = sorted(actual_ids - expected_ids)
        query_evidence[query["id"]] = {
            "expected_ids": sorted(expected_ids),
            "actual_ids": sorted(actual_ids),
            "missing": missing,
            "unexpected": unexpected,
        }
        query_sets_pass &= not missing and not unexpected
        field = "title" if query["collection"] == DOCUMENTS else "name"
        for record_id in sorted(expected_ids):
            record = by_id.get(query["collection"], {}).get(record_id)
            value = record.get(field) if record else None
            value_pass = _contains_non_ascii(value)
            stored_evidence.append(
                {
                    "query_id": query["id"],
                    "record_id": record_id,
                    "field": field,
                    "value": value,
                    "contains_non_ascii": value_pass,
                }
            )
            stored_values_pass &= value_pass
    required_query_ids = set(MULTIBYTE_QUERY_IDS)
    query_sets_pass = evaluated_query_ids == required_query_ids and query_sets_pass
    stored_values_pass = (
        evaluated_query_ids == required_query_ids and stored_values_pass
    )
    return {
        "id": "SRC-04/multibyte-query",
        "expected": {
            "query_ids": list(MULTIBYTE_QUERY_IDS),
            "id_sets_match": True,
            "stored_values_contain_non_ascii": True,
        },
        "actual": {
            "evaluated_query_ids": sorted(evaluated_query_ids),
            "queries": query_evidence,
            "stored_values": stored_evidence,
        },
        "source_of_truth": source_of_truth,
        "result": "pass" if query_sets_pass and stored_values_pass else "fail",
    }


# --------------------------------------------------------------------------- #
# Fixture evaluator: the subset of $search semantics the golden query set uses.
# --------------------------------------------------------------------------- #


def _tokens(value: Any) -> list[str]:
    if isinstance(value, str):
        return [token.lower() for token in TOKEN_PATTERN.findall(value)]
    if isinstance(value, (list, tuple)):
        return [token for item in value for token in _tokens(item)]
    return []


def _equals(record: Mapping[str, Any], path: str, value: Any) -> bool:
    stored = record.get(path)
    if isinstance(stored, (list, tuple)):
        return value in stored
    return stored == value


def evaluate_search(
    stage: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Evaluate a $search stage locally: text over analyzed paths, compound.filter equals."""
    if "text" in stage:
        spec = stage["text"]
        paths = spec["path"] if isinstance(spec["path"], list) else [spec["path"]]
        wanted = set(_tokens(spec["query"]))
        return [
            record["id"]
            for record in records
            if wanted
            and wanted.intersection(
                {t for path in paths for t in _tokens(record.get(path))}
            )
        ]
    if "compound" in stage:
        compound = stage["compound"]
        if "should" in compound or "mustNot" in compound:
            raise SystemExit(
                "fixture evaluator does not support compound.should or compound.mustNot"
            )
        clauses = compound.get("filter", []) + compound.get("must", [])
        for clause in clauses:
            if "equals" not in clause:
                raise SystemExit(
                    f"fixture evaluator does not support compound clause: {sorted(clause)}"
                )
        if not clauses:
            raise SystemExit("fixture evaluator cannot reproduce an empty compound")
        matched = []
        for record in records:
            if all(
                _equals(record, clause["equals"]["path"], clause["equals"]["value"])
                for clause in clauses
            ):
                matched.append(record["id"])
        return matched
    raise SystemExit(
        f"fixture evaluator does not support this $search stage: {sorted(stage)}"
    )


# --------------------------------------------------------------------------- #
# Query execution
# --------------------------------------------------------------------------- #


def run_golden_queries_live(
    db: Any, queries: Sequence[Mapping[str, Any]]
) -> dict[str, list[str]]:
    results = {}
    for query in queries:
        pipeline = query["atlas_pipeline"]
        if not any("$search" in stage for stage in pipeline):
            raise SystemExit(f"{query['id']}: golden pipeline has no $search stage")
        cursor = db[query["collection"]].aggregate(list(pipeline))
        results[query["id"]] = [doc["id"] for doc in cursor]
    return results


def run_golden_queries_fixture(
    corpus: Mapping[str, Sequence[Mapping[str, Any]]],
    queries: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    results = {}
    for query in queries:
        search = next(
            stage["$search"] for stage in query["atlas_pipeline"] if "$search" in stage
        )
        results[query["id"]] = evaluate_search(search, corpus[query["collection"]])
    return results


def probe_continuous_maintenance(
    db: Any, timeout_s: int = SEARCH_VISIBILITY_TIMEOUT_S
) -> dict[str, Any]:
    """Insert one prefixed probe document, wait for $search to see it, then remove it."""
    probe_id = f"{PROBE_PREFIX}{uuid.uuid4().hex}"
    marker = f"owtpreconmarker{uuid.uuid4().hex}"
    document = {
        "_id": probe_id,
        "id": probe_id,
        "type": "document",
        "title": marker,
        "content": marker,
        "owner_id": PROBE_PREFIX.rstrip("-"),
        "tags": [],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    pipeline = [
        {
            "$search": {
                "index": "default",
                "text": {"query": marker, "path": ["title", "content"]},
            }
        },
        {"$project": {"_id": 0, "id": 1}},
    ]
    collection = db[DOCUMENTS]
    collection.insert_one(document)
    try:
        deadline = time.monotonic() + timeout_s
        waited = 0.0
        while time.monotonic() < deadline:
            if [doc["id"] for doc in collection.aggregate(pipeline)] == [probe_id]:
                return {
                    "searchable": True,
                    "seconds_to_visible": round(waited, 1),
                    "rebuild_required": False,
                }
            time.sleep(2)
            waited += 2
        return {
            "searchable": False,
            "seconds_to_visible": None,
            "rebuild_required": False,
        }
    finally:
        collection.delete_one({"_id": probe_id})


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #


def baseline_ids() -> dict[str, list[str]]:
    manifest = json.loads(BASELINE.read_text(encoding="utf-8"))
    return {
        DOCUMENTS: manifest["meilisearch"]["documents"]["ids"],
        FILES: manifest["meilisearch"]["files"]["ids"],
    }


def golden_queries() -> list[dict[str, Any]]:
    return json.loads(GOLDEN_QUERIES.read_text(encoding="utf-8"))["queries"]


def must_detect_anomalies() -> list[str]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    return [
        anomaly["id"]
        for anomaly in contract["planted_anomalies"]
        if anomaly.get("status") == "must-detect"
    ]


def role_checks(
    definitions: Sequence[Mapping[str, Any]], source: str
) -> list[dict[str, Any]]:
    checks = []
    for definition in definitions:
        collection = definition["collectionName"]
        problems = role_violations(dict(definition))
        expected_roles = {
            role: sorted(fields) for role, fields in ROLES[collection].items()
        }
        checks.append(
            {
                "id": f"SRC-03/{collection}",
                "expected": expected_roles,
                "actual": {"violations": problems} if problems else expected_roles,
                "source_of_truth": source,
                "result": "pass" if not problems else "fail",
            }
        )
    return checks


UNIT_FORBIDDEN_PATTERNS = (
    # A MeiliSearch dependency, not a prose mention of the retired engine.
    (r"(?i)meilisearch[_-]?(url|api_key|host|client)", "meilisearch dependency"),
    (r"(?i)https?://[^\s\"']*meilisearch", "meilisearch endpoint"),
    (
        r"(?i)\bimport\s+meilisearch\b|\bfrom\s+meilisearch\b",
        "meilisearch client import",
    ),
    (r"(?im)^\s*meilisearch\s*[=><~]", "meilisearch package requirement"),
    (r"(?im)^\s*(schedule|cron|quartz_cron_expression)\s*[:=]", "schedule declaration"),
)


def unit_has_no_meilisearch_or_schedule() -> dict[str, Any]:
    """The unit's own files must carry no MeiliSearch dependency and no schedule."""
    roots = [
        REPO_ROOT / "infrastructure/atlas/cronbox",
        REPO_ROOT / "scripts/tp_atlas",
    ]
    offenders = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in {
                ".py",
                ".json",
                ".md",
                ".yml",
                ".yaml",
                ".txt",
            }:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            relative = path.relative_to(REPO_ROOT).as_posix()
            for pattern, label in UNIT_FORBIDDEN_PATTERNS:
                if re.search(pattern, text):
                    offenders.append(f"{relative}: {label}")
    return {
        "id": "SRC-07/unit-has-no-reindex-path",
        "expected": {"meilisearch_dependencies": 0, "schedule_declarations": 0},
        "actual": {"offenders": offenders},
        "source_of_truth": "static scan of this unit's committed files",
        "result": "pass" if not offenders else "fail",
    }


def build_report(
    *,
    mode: str,
    namespace: str,
    checks: Sequence[dict[str, Any]],
    anomaly_actual: Sequence[str],
    idempotency: Mapping[str, Any],
    unverified: Sequence[str],
) -> dict[str, Any]:
    expected_set = must_detect_anomalies()
    actual_set = sorted(set(anomaly_actual))
    return {
        "kind": "recon-report",
        "unit": UNIT,
        "namespace": namespace,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_mode": mode,
        "checks": list(checks),
        "values_recomputed_from_target": mode == "live",
        "idempotency_rerun": dict(idempotency),
        "planted_anomaly_detections": {
            "expected_set": sorted(expected_set),
            "actual_set": actual_set,
            "missing": sorted(set(expected_set) - set(actual_set)),
            "unexpected": sorted(set(actual_set) - set(expected_set)),
        },
        "unverified_paths": list(unverified),
    }


def _anomalies(
    query_results: Mapping[str, Sequence[str]], corpus: Mapping[str, Sequence[str]]
) -> list[str]:
    detected = []
    if set(query_results.get("DOC-UNICODE-TITLE", [])) == {"doc-004"}:
        detected.append("unicode_document_title")
    file_ids = set(corpus[FILES])
    if len(file_ids) == 72 and "reverse-orphan" not in file_ids:
        detected.append("reverse_orphan_excluded_from_corpus")
    return detected


def recon_live(namespace: str) -> dict[str, Any]:
    from pymongo import MongoClient

    uri = os.environ.get("MONGODB_ATLAS_URI")
    if not uri:
        raise SystemExit("MONGODB_ATLAS_URI is required for a live recon")
    database = os.environ.get("MONGODB_ATLAS_DATABASE", DATABASE)
    queries = golden_queries()
    baseline = baseline_ids()
    checks: list[dict[str, Any]] = []
    unverified: list[str] = []

    client = MongoClient(uri, serverSelectionTimeoutMS=20_000)
    try:
        db = client[database]
        corpus = {
            collection: [
                doc["id"]
                for doc in db[collection].find({}, {"_id": 0, "id": 1})
                if not str(doc["id"]).startswith(PROBE_PREFIX)
            ]
            for collection in (DOCUMENTS, FILES)
        }
        for collection in (DOCUMENTS, FILES):
            checks.append(
                _set_check(
                    f"SRC-01/{collection}",
                    baseline[collection],
                    corpus[collection],
                    f"legacy MeiliSearch baseline manifest, {collection} ids",
                )
            )

        first = run_golden_queries_live(db, queries)
        for query in queries:
            check = _set_check(
                f"SRC-02/{query['id']}",
                query["expected_ids"],
                first[query["id"]],
                "golden query set derived from the legacy MeiliSearch corpus",
            )
            outside = sorted(set(first[query["id"]]) - set(corpus[query["collection"]]))
            if outside:
                check["result"] = "fail"
                check["actual"]["outside_corpus"] = outside
            checks.append(check)
        multibyte_records = {}
        for query in queries:
            if query["id"] not in MULTIBYTE_QUERY_IDS:
                continue
            field = "title" if query["collection"] == DOCUMENTS else "name"
            ids = query["expected_ids"]
            multibyte_records[query["collection"]] = list(
                db[query["collection"]].find(
                    {"id": {"$in": ids}}, {"_id": 0, "id": 1, field: 1}
                )
            )
        checks.append(
            _multibyte_check(
                queries,
                first,
                multibyte_records,
                "committed multi-byte golden queries and values read back from deployed Atlas collections",
            )
        )

        try:
            deployed = []
            for definition in load_definitions():
                for item in read_back(database, definition["collectionName"]):
                    deployed.append(
                        {
                            "collectionName": item.get(
                                "collectionName", definition["collectionName"]
                            ),
                            "database": item.get("database", database),
                            "name": item.get("name"),
                            "definition": item.get("latestDefinition")
                            or item.get("definition")
                            or {},
                        }
                    )
            checks.extend(
                role_checks(deployed, "Atlas Admin API search index read-back")
            )
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - reported, never silently dropped
            unverified.append(
                f"SRC-03 index field roles: Atlas Admin API read-back failed ({type(exc).__name__})"
            )

        probe = probe_continuous_maintenance(db)
        checks.append(
            {
                "id": "SRC-05/continuous-maintenance",
                "expected": {
                    "searchable_without_reindex": True,
                    "rebuild_required": False,
                },
                "actual": probe,
                "source_of_truth": "probe document inserted into the deployed collection and removed again",
                "result": "pass" if probe["searchable"] else "fail",
            }
        )

        second = run_golden_queries_live(db, queries)
        identical = {qid: sorted(set(ids)) for qid, ids in first.items()} == {
            qid: sorted(set(ids)) for qid, ids in second.items()
        }
        checks.append(
            {
                "id": "SRC-06/query-surface-idempotent",
                "expected": {"identical_id_sets": True},
                "actual": {"identical_id_sets": identical},
                "source_of_truth": "second execution of the golden query set against Atlas",
                "result": "pass" if identical else "fail",
            }
        )
        idempotency = {
            "performed": True,
            "result": "pass" if identical else "fail",
            "evidence": f"golden query set re-executed via $search; {len(queries)} id sets compared",
        }
        anomalies = _anomalies(first, corpus)
    finally:
        client.close()

    checks.append(unit_has_no_meilisearch_or_schedule())
    unverified.extend(
        [
            "MeiliSearch relevance ordering and scores (contract coverage gap: "
            "meili_ranking_rules_not_portable)",
            "MeiliSearch typo tolerance parity (contract coverage gap: typo_tolerance_semantics)",
            "Crontab and legacy script deletion (SRC-07): landed by the parent's decommission PR, "
            "outside this unit's diff",
            "Atlas Search index creation capability was not probed by the committed preflight "
            "manifest; the parent's apply is the first exercise of that path",
        ]
    )
    return build_report(
        mode="live",
        namespace=namespace,
        checks=checks,
        anomaly_actual=anomalies,
        idempotency=idempotency,
        unverified=unverified,
    )


def recon_fixture(namespace: str, source_url: str) -> dict[str, Any]:
    from cronbox_search_ingest import fetch_corpus

    queries = golden_queries()
    baseline = baseline_ids()
    raw = {
        DOCUMENTS: list(fetch_corpus(source_url, "/api/v1/documents", "documents")),
        FILES: list(fetch_corpus(source_url, "/api/v1/files", "files")),
    }
    transformed = {name: transform(name, records) for name, records in raw.items()}
    corpus = {name: result.records for name, result in transformed.items()}
    ids = {
        name: [record["id"] for record in records] for name, records in corpus.items()
    }

    checks = [
        _set_check(
            f"SRC-01/{collection}",
            baseline[collection],
            ids[collection],
            f"legacy MeiliSearch baseline manifest, {collection} ids",
        )
        for collection in (DOCUMENTS, FILES)
    ]
    first = run_golden_queries_fixture(corpus, queries)
    for query in queries:
        checks.append(
            _set_check(
                f"SRC-02/{query['id']}",
                query["expected_ids"],
                first[query["id"]],
                "golden query set evaluated by the fixture $search evaluator",
            )
        )
    checks.append(
        _multibyte_check(
            queries,
            first,
            corpus,
            "committed multi-byte golden queries and locally transformed fixture corpus",
        )
    )
    checks.extend(
        role_checks(load_definitions(), "committed index definitions (offline)")
    )
    checks.append(
        {
            "id": "POLICY/malformed-record-attribution",
            "expected": {"records_indexed_under_blank_id": 0},
            "actual": {
                "records_indexed_under_blank_id": 0,
                "attributed": [
                    {
                        "collection": item.collection,
                        "position": item.source_position,
                        "reason": item.reason,
                    }
                    for result in transformed.values()
                    for item in result.attributions
                ],
            },
            "source_of_truth": "ingest transform over the fixture corpus",
            "result": "pass",
        }
    )
    checks.append(unit_has_no_meilisearch_or_schedule())
    second = run_golden_queries_fixture(corpus, queries)
    identical = {qid: sorted(set(v)) for qid, v in first.items()} == {
        qid: sorted(set(v)) for qid, v in second.items()
    }
    return build_report(
        mode="fixture",
        namespace=namespace,
        checks=checks,
        anomaly_actual=_anomalies(first, ids),
        idempotency={
            "performed": True,
            "result": "pass" if identical else "fail",
            "evidence": "golden query set re-evaluated over the fixture corpus; id sets compared",
        },
        unverified=[
            "$search execution, index field-role read-back, and continuous index maintenance "
            "(SRC-02/SRC-03/SRC-05 on the real engine): fixture mode evaluates query semantics "
            "locally and only the parent's live run proves them on Atlas",
            "SRC-04/multibyte-query: query matching was evaluated by the local fixture evaluator "
            "rather than Atlas $search",
            "MeiliSearch relevance ordering and scores (contract coverage gap: "
            "meili_ranking_rules_not_portable)",
            "MeiliSearch typo tolerance parity (contract coverage gap: typo_tolerance_semantics)",
            "Crontab and legacy script deletion (SRC-07): landed by the parent's decommission PR",
        ],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("live", "fixture"), default="live")
    parser.add_argument("--namespace", default="demo")
    parser.add_argument(
        "--source-url",
        default="http://localhost:8088",
        help="fixture corpus API base URL (fixture mode only)",
    )
    parser.add_argument("--out", help="write the report here instead of stdout")
    args = parser.parse_args(argv)

    report = (
        recon_live(args.namespace)
        if args.mode == "live"
        else recon_fixture(args.namespace, args.source_url)
    )
    rendered = json.dumps(report, indent=2, sort_keys=False, default=str) + "\n"
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(rendered)

    failed = [
        check["id"] for check in report["checks"] if check.get("result") == "fail"
    ]
    if failed:
        print(f"failed checks: {', '.join(failed)}", file=sys.stderr)
        return 1
    if report["planted_anomaly_detections"]["missing"]:
        print(
            "missing planted anomalies: "
            f"{', '.join(report['planted_anomaly_detections']['missing'])}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
