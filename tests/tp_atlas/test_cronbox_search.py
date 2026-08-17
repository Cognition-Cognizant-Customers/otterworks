from __future__ import annotations

import copy
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from bson import Binary
from pymongo import ReplaceOne

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "tp_atlas"
sys.path.insert(0, str(SCRIPTS))

from cronbox_search_indexes import load_definitions, role_violations  # noqa: E402
from cronbox_search_ingest import transform_document, transform_file, upsert  # noqa: E402
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
