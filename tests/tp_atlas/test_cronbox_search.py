from __future__ import annotations

import copy
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from bson import Binary
from pymongo import ReplaceOne
import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "tp_atlas"
sys.path.insert(0, str(SCRIPTS))

from cronbox_search_indexes import load_definitions, role_violations  # noqa: E402
from cronbox_search_ingest import (  # noqa: E402
    fetch_corpus,
    transform_document,
    transform_file,
    upsert,
)
import cronbox_search_recon as recon  # noqa: E402
from cronbox_search_recon import evaluate_search  # noqa: E402


def test_transform_document_uses_fallback_and_attributes_missing_ids() -> None:
    transformed = transform_document(
        {
            "id": "doc-fallback",
            "title": "Title",
            "created_at": "2026-01-15T01:02:03-05:00",
            "updated_at": "2026-01-15T06:02:03Z",
        },
        4,
    )
    assert transformed["_id"] == "doc-fallback"
    assert transformed["id"] == "doc-fallback"
    assert transformed["created_at"] == datetime(
        2026, 1, 15, 6, 2, 3, tzinfo=timezone.utc
    )

    attributed = transform_document({"document_id": "  ", "id": ""}, 7)
    assert attributed.collection == "documents"
    assert attributed.source_position == 7
    assert attributed.reason == "missing_or_empty_id"


def test_transform_file_normalizes_binary_and_size() -> None:
    transformed = transform_file(
        {
            "file_id": "file-1",
            "file_name": b"\xff\xfe",
            "mime_type": "text/plain",
            "size_bytes": "42",
            "tags": [b"ok", b"\xff"],
            "created_at": "2026-01-15T00:00:00",
        },
        2,
    )
    assert transformed["size"] == 42
    assert "name" not in transformed
    assert transformed["name__binary"] == Binary(b"\xff\xfe")
    assert transformed["tags"] == ["ok"]
    assert transformed["tags.1__binary"] == Binary(b"\xff")
    assert transformed["created_at"] == datetime(2026, 1, 15, tzinfo=timezone.utc)

    attributed = transform_file({"file_id": None}, 9)
    assert attributed.source_position == 9
    assert attributed.reason == "missing_or_empty_id"


def test_upsert_builds_replace_one_upserts_and_empty_is_noop() -> None:
    operations = []

    class Collection:
        def bulk_write(self, writes, ordered):
            operations.extend(writes)
            assert ordered is False
            return SimpleNamespace(matched_count=1, upserted_ids={"file-2": "file-2"})

    assert upsert(Collection(), []) == {"matched": 0, "upserted": 0}
    assert upsert(Collection(), [{"_id": "file-2", "id": "file-2"}]) == {
        "matched": 1,
        "upserted": 1,
    }
    assert len(operations) == 1
    assert isinstance(operations[0], ReplaceOne)
    assert operations[0]._filter == {"_id": "file-2"}
    assert operations[0]._doc == {"_id": "file-2", "id": "file-2"}
    assert operations[0]._upsert is True


def test_fetch_corpus_accepts_collection_and_items_envelopes(monkeypatch) -> None:
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def get(url, *, params, timeout):
        del url, timeout
        calls.append(params)
        if params["page"] == 1:
            return Response(current_payload)
        return Response({key: []})

    monkeypatch.setattr("requests.get", get)

    for current_payload, expected in [
        ({"documents": [{"id": "document-1"}]}, [{"id": "document-1"}]),
        ({"items": [{"id": "document-2"}]}, [{"id": "document-2"}]),
    ]:
        calls.clear()
        key = "documents"
        assert (
            list(fetch_corpus("http://source", "/api/v1/documents", key, 1)) == expected
        )
        assert [call["page"] for call in calls] == [1, 2]


def test_committed_definitions_preserve_roles_and_removed_mapping_fails() -> None:
    definitions = load_definitions()
    assert definitions
    assert all(not role_violations(definition) for definition in definitions)

    broken = copy.deepcopy(
        next(item for item in definitions if item["collectionName"] == "documents")
    )
    del broken["definition"]["mappings"]["fields"]["owner_id"]
    violations = role_violations(broken)
    assert any("documents.owner_id" in violation for violation in violations)


def test_fixture_evaluator_text_and_compound_filter_equals() -> None:
    records = [
        {
            "id": "doc-1",
            "title": "Delta report",
            "content": "Content body",
            "owner_id": "user-1",
        },
        {
            "id": "doc-2",
            "title": "Other",
            "content": "Content body",
            "owner_id": "user-2",
        },
    ]
    assert evaluate_search(
        {"text": {"query": "Delta", "path": ["title", "content"]}}, records
    ) == ["doc-1"]
    assert evaluate_search(
        {"compound": {"filter": [{"equals": {"path": "owner_id", "value": "user-2"}}]}},
        records,
    ) == ["doc-2"]


def test_fixture_evaluator_text_matches_any_query_term() -> None:
    records = [
        {"id": "doc-1", "title": "Delta report"},
        {"id": "doc-2", "title": "Other"},
    ]
    assert evaluate_search(
        {"text": {"query": "Delta missing", "path": "title"}}, records
    ) == ["doc-1"]


def test_fixture_evaluator_rejects_unsupported_compound_clause() -> None:
    with pytest.raises(SystemExit, match="compound clause"):
        evaluate_search(
            {"compound": {"filter": [{"text": {"query": "Delta", "path": "title"}}]}},
            [{"id": "doc-1", "title": "Delta"}],
        )


def test_fixture_evaluator_rejects_empty_compound() -> None:
    with pytest.raises(SystemExit, match="empty compound"):
        evaluate_search({"compound": {}}, [{"id": "doc-1"}])


def test_fixture_report_labels_multibyte_and_attribution_checks(monkeypatch) -> None:
    queries = [
        {
            "id": "DOC-UNICODE-TITLE",
            "collection": "documents",
            "atlas_pipeline": [
                {"$search": {"text": {"query": "Δocument", "path": "title"}}}
            ],
            "expected_ids": ["doc-004"],
        },
        {
            "id": "FILE-UNICODE-NAME",
            "collection": "files",
            "atlas_pipeline": [
                {"$search": {"text": {"query": "Fichier", "path": "name"}}}
            ],
            "expected_ids": ["file-007"],
        },
    ]
    source = {
        "documents": [{"id": "doc-004", "title": "Δocument ☕"}],
        "files": [{"id": "file-007", "name": "Fichier Δ ☕"}],
    }

    def fetch(_base_url, path, _key):
        return iter(source["documents" if "documents" in path else "files"])

    monkeypatch.setattr(recon, "golden_queries", lambda: queries)
    monkeypatch.setattr(
        recon,
        "baseline_ids",
        lambda: {"documents": ["doc-004"], "files": ["file-007"]},
    )
    monkeypatch.setattr("cronbox_search_ingest.fetch_corpus", fetch)

    report = recon.recon_fixture("demo", "http://source")
    check_ids = {check["id"] for check in report["checks"]}
    assert "SRC-04/multibyte-query" in check_ids
    assert "POLICY/malformed-record-attribution" in check_ids
    assert "SRC-04/attribution" not in check_ids
    multibyte = next(
        check for check in report["checks"] if check["id"] == "SRC-04/multibyte-query"
    )
    assert "locally transformed fixture corpus" in multibyte["source_of_truth"]
    assert "target" not in multibyte["source_of_truth"]
    assert any(
        entry.startswith("SRC-04/multibyte-query:")
        for entry in report["unverified_paths"]
    )


def test_multibyte_check_fails_for_ascii_folded_stored_value() -> None:
    queries = [
        {
            "id": "DOC-UNICODE-TITLE",
            "collection": "documents",
            "expected_ids": ["doc-004"],
        },
        {
            "id": "FILE-UNICODE-NAME",
            "collection": "files",
            "expected_ids": ["file-007"],
        },
    ]
    check = recon._multibyte_check(
        queries,
        {
            "DOC-UNICODE-TITLE": ["doc-004"],
            "FILE-UNICODE-NAME": ["file-007"],
        },
        {
            "documents": [{"id": "doc-004", "title": "Document coffee"}],
            "files": [{"id": "file-007", "name": "Fichier Δ ☕"}],
        },
        "committed multi-byte golden queries and locally transformed fixture corpus",
    )
    assert check["id"] == "SRC-04/multibyte-query"
    assert check["result"] == "fail"


def test_multibyte_check_fails_when_a_required_query_is_missing() -> None:
    check = recon._multibyte_check(
        [
            {
                "id": "DOC-UNICODE-TITLE",
                "collection": "documents",
                "expected_ids": ["doc-004"],
            }
        ],
        {"DOC-UNICODE-TITLE": ["doc-004"]},
        {"documents": [{"id": "doc-004", "title": "Δocument ☕"}]},
        "committed multi-byte golden queries and locally transformed fixture corpus",
    )
    assert check["result"] == "fail"
    assert check["actual"]["evaluated_query_ids"] == ["DOC-UNICODE-TITLE"]


def test_multibyte_check_fails_when_an_expected_record_is_missing() -> None:
    check = recon._multibyte_check(
        [
            {
                "id": "DOC-UNICODE-TITLE",
                "collection": "documents",
                "expected_ids": ["doc-004"],
            },
            {
                "id": "FILE-UNICODE-NAME",
                "collection": "files",
                "expected_ids": ["file-007"],
            },
        ],
        {
            "DOC-UNICODE-TITLE": ["doc-004"],
            "FILE-UNICODE-NAME": ["file-007"],
        },
        {
            "documents": [],
            "files": [{"id": "file-007", "name": "Fichier Δ ☕"}],
        },
        "committed multi-byte golden queries and locally transformed fixture corpus",
    )
    assert check["result"] == "fail"
    assert check["actual"]["stored_values"][0]["contains_non_ascii"] is False
