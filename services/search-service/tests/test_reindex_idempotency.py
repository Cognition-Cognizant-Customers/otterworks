"""WP-07: indexing/reindexing idempotency against a primary-key-aware fake.

The fake MeiliSearch index upserts on the ``id`` primary key, exactly like the
real engine, so "indexed twice -> one entry" is a real assertion rather than a
call-count check.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.indexer import Indexer
from tests.conftest_wp07 import (
    DOCUMENTS_INDEX,
    FILES_INDEX,
    build_app,
    build_service,
    seed_documents,
)
from tests.fakes import FakeMeiliClient, make_api_error


@pytest.fixture()
def meili() -> FakeMeiliClient:
    return FakeMeiliClient()


@pytest.fixture()
def client(meili: FakeMeiliClient):
    return build_app(meili).test_client()


@pytest.fixture()
def indexer(meili: FakeMeiliClient) -> Indexer:
    return Indexer(build_service(meili))


def _http_response(payload: dict, status: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    return response


class TestIndexIdempotency:
    def test_indexing_the_same_document_twice_yields_one_entry(self, indexer, meili):
        payload = {"id": "doc-1", "title": "Quarterly report", "owner_id": "user-1"}
        indexer.index_document(payload)
        indexer.index_document(payload)
        assert list(meili.index(DOCUMENTS_INDEX).documents) == ["doc-1"]

    def test_reindexing_a_document_updates_it_in_place(self, indexer, meili):
        indexer.index_document({"id": "doc-1", "title": "v1", "owner_id": "user-1"})
        indexer.index_document({"id": "doc-1", "title": "v2", "owner_id": "user-1"})
        stored = meili.index(DOCUMENTS_INDEX).documents
        assert len(stored) == 1
        assert stored["doc-1"]["title"] == "v2"

    def test_indexing_the_same_file_twice_yields_one_entry(self, indexer, meili):
        payload = {"id": "file-1", "name": "a.pdf", "owner_id": "user-1"}
        indexer.index_file(payload)
        indexer.index_file(payload)
        assert list(meili.index(FILES_INDEX).documents) == ["file-1"]

    def test_posting_the_same_document_twice_over_http_yields_one_entry(
        self, client, meili
    ):
        body = {"id": "doc-9", "title": "Duplicate", "owner_id": "user-1"}
        first = client.post("/api/v1/search/index/document", json=body)
        second = client.post("/api/v1/search/index/document", json=body)
        assert first.status_code == second.status_code == 201
        assert len(meili.index(DOCUMENTS_INDEX).documents) == 1

    def test_documents_and_files_with_the_same_id_do_not_collide(self, indexer, meili):
        indexer.index_document({"id": "shared-1", "title": "doc"})
        indexer.index_file({"id": "shared-1", "name": "file"})
        assert meili.index(DOCUMENTS_INDEX).documents["shared-1"]["type"] == "document"
        assert meili.index(FILES_INDEX).documents["shared-1"]["type"] == "file"

    def test_delete_is_idempotent_the_second_time_reports_not_found(
        self, indexer, meili
    ):
        indexer.index_document({"id": "doc-1", "title": "T"})
        assert indexer.remove("document", "doc-1")["status"] == "deleted"
        assert indexer.remove("document", "doc-1")["status"] == "not_found"

    def test_file_index_retries_once_on_the_lmdb_key_exists_error(self, meili):
        """The documented MDB_KEYEXIST workaround deletes then re-adds."""
        service = build_service(meili)
        calls = {"n": 0}
        original = meili.wait_for_task

        def flaky(task_uid, timeout_in_ms=5000):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("MeiliSearch task 1 failed: MDB_KEYEXIST")
            return original(task_uid, timeout_in_ms=timeout_in_ms)

        with patch.object(meili, "wait_for_task", side_effect=flaky):
            service.index_file({"id": "file-1", "name": "a.pdf"})

        assert list(meili.index(FILES_INDEX).documents) == ["file-1"]
        assert calls["n"] == 3  # failed add, delete, successful re-add

    def test_a_non_lmdb_task_failure_is_not_retried(self, meili):
        service = build_service(meili)
        meili.task_status = "failed"
        with pytest.raises(RuntimeError):
            service.index_file({"id": "file-1", "name": "a.pdf"})


class TestFullReindex:
    def test_reindex_twice_leaves_a_single_copy_of_each_document(self, meili):
        service = build_service(meili)
        documents = [{"id": "doc-1", "title": "one"}, {"id": "doc-2", "title": "two"}]
        files = [{"id": "file-1", "name": "a.pdf"}]

        first = service.reindex(documents=documents, files=files)
        second = service.reindex(documents=documents, files=files)

        assert first == second
        assert first["indexed_counts"] == {"documents": 2, "files": 1}
        assert len(meili.index(DOCUMENTS_INDEX).documents) == 2
        assert len(meili.index(FILES_INDEX).documents) == 1

    def test_reindex_drops_documents_that_no_longer_exist_upstream(self, meili):
        service = build_service(meili)
        seed_documents(meili, {"id": "stale-1", "title": "gone"})
        service.reindex(documents=[{"id": "doc-1", "title": "kept"}])
        assert list(meili.index(DOCUMENTS_INDEX).documents) == ["doc-1"]

    def test_reindex_without_data_leaves_empty_indices(self, meili):
        service = build_service(meili)
        seed_documents(meili, {"id": "stale-1", "title": "gone"})
        result = service.reindex()
        assert result["indexed_counts"] == {"documents": 0, "files": 0}
        assert meili.index(DOCUMENTS_INDEX).documents == {}

    def test_reindex_survives_a_missing_index(self, meili):
        """Deleting an index that is not there must not abort the reindex."""
        service = build_service(meili)
        with patch.object(
            meili, "delete_index", side_effect=make_api_error(404, "index_not_found")
        ):
            result = service.reindex(documents=[{"id": "doc-1", "title": "x"}])
        assert result["status"] == "reindexed"
        assert list(meili.index(DOCUMENTS_INDEX).documents) == ["doc-1"]

    def test_reindex_batches_large_document_sets(self, meili):
        """Boundary: 501 documents is one full batch of 500 plus a remainder."""
        service = build_service(meili)
        documents = [{"id": f"doc-{i:04d}", "title": "bulk"} for i in range(501)]
        result = service.reindex(documents=documents)
        assert result["indexed_counts"]["documents"] == 501
        assert len(meili.index(DOCUMENTS_INDEX).documents) == 501

    def test_http_reindex_pulls_from_the_source_services(self, client, meili):
        pages = {
            "http://document-service:8083/api/v1/documents/": [
                _http_response({"documents": [{"id": "doc-1", "title": "One"}]}),
                _http_response({"documents": []}),
            ],
            "http://file-service:8082/api/v1/files": [
                _http_response({"files": [{"id": "file-1", "name": "a.pdf"}]}),
                _http_response({"files": []}),
            ],
        }

        def fake_get(url, params=None, timeout=None):
            return pages[url].pop(0)

        with patch("app.services.indexer.requests.get", side_effect=fake_get):
            response = client.post("/api/v1/search/reindex")

        assert response.status_code == 200
        assert response.get_json()["indexed_counts"] == {"documents": 1, "files": 1}

    def test_reindex_still_succeeds_when_a_source_service_is_down(self, client, meili):
        with patch(
            "app.services.indexer.requests.get",
            side_effect=[_http_response({}, status=503), _http_response({}, status=503)],
        ):
            response = client.post("/api/v1/search/reindex")
        assert response.status_code == 200
        assert response.get_json()["indexed_counts"] == {"documents": 0, "files": 0}
