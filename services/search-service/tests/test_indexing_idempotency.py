"""Idempotency of indexing and reindexing, against an in-memory MeiliSearch.

The fake index is keyed by the ``id`` primary key, so a duplicated write is
observable as either one stored document (idempotent) or two (not).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.config import AppConfig, AuthConfig, MeiliSearchConfig, SQSConfig
from app.main import create_app
from app.services.indexer import Indexer
from app.services.meilisearch_client import MeiliSearchService
from tests.fakes import FakeClient, api_error

DOCUMENTS_INDEX = "test-otterworks-documents"
FILES_INDEX = "test-otterworks-files"
REINDEX_BATCH_SIZE = 500


@pytest.fixture()
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture()
def fake_service(fake_client: FakeClient) -> MeiliSearchService:
    with patch("app.services.meilisearch_client.meilisearch.Client") as mock_cls:
        mock_cls.return_value = fake_client
        return MeiliSearchService(
            MeiliSearchConfig(documents_index=DOCUMENTS_INDEX, files_index=FILES_INDEX)
        )


@pytest.fixture()
def fake_indexer(fake_service: MeiliSearchService) -> Indexer:
    return Indexer(fake_service)


@pytest.fixture()
def fake_backed_client(fake_client: FakeClient):
    """Flask test client wired to the in-memory MeiliSearch."""
    config = AppConfig(
        service_name="search-service-test",
        meilisearch=MeiliSearchConfig(
            documents_index=DOCUMENTS_INDEX, files_index=FILES_INDEX
        ),
        sqs=SQSConfig(enabled=False),
        auth=AuthConfig(service_token="", require_auth=False),
    )
    with patch("app.services.meilisearch_client.meilisearch.Client") as mock_cls:
        mock_cls.return_value = fake_client
        app = create_app(config)
    app.config["TESTING"] = True
    return app.test_client()


def source_responses(documents: list[dict[str, Any]], files: list[dict[str, Any]]):
    """Build a ``requests.get`` side effect for the two source services."""

    def _get(url: str, params: dict[str, Any] | None = None, timeout: int | None = None):
        page = (params or {}).get("page", 1)
        payload = documents if "documents" in url else files
        response = MagicMock()
        response.status_code = 200
        key = "documents" if "documents" in url else "files"
        response.json.return_value = {key: payload if page == 1 else []}
        return response

    return _get


class TestDocumentIndexingIdempotency:
    """Re-indexing the same id overwrites rather than duplicates."""

    def test_indexing_the_same_document_twice_stores_one_document(
        self, fake_indexer: Indexer, fake_client: FakeClient
    ):
        payload = {"id": "doc-1", "title": "Otter report", "owner_id": "user-1"}
        first = fake_indexer.index_document(payload)
        second = fake_indexer.index_document(payload)
        assert first == second
        assert list(fake_client.index(DOCUMENTS_INDEX).documents) == ["doc-1"]

    def test_reindexing_a_changed_document_replaces_it(
        self, fake_indexer: Indexer, fake_client: FakeClient
    ):
        fake_indexer.index_document({"id": "doc-1", "title": "Draft"})
        fake_indexer.index_document({"id": "doc-1", "title": "Final"})
        documents = fake_client.index(DOCUMENTS_INDEX).documents
        assert len(documents) == 1
        assert documents["doc-1"]["title"] == "Final"

    def test_indexing_the_same_file_twice_stores_one_file(
        self, fake_indexer: Indexer, fake_client: FakeClient
    ):
        payload = {"id": "file-1", "name": "otter.png", "owner_id": "user-1"}
        fake_indexer.index_file(payload)
        fake_indexer.index_file(payload)
        assert list(fake_client.index(FILES_INDEX).documents) == ["file-1"]

    def test_a_document_and_a_file_may_share_an_id(
        self, fake_indexer: Indexer, fake_client: FakeClient
    ):
        fake_indexer.index_document({"id": "shared-1", "title": "Otter report"})
        fake_indexer.index_file({"id": "shared-1", "name": "otter.png"})
        assert list(fake_client.index(DOCUMENTS_INDEX).documents) == ["shared-1"]
        assert list(fake_client.index(FILES_INDEX).documents) == ["shared-1"]

    def test_deleting_twice_reports_not_found_the_second_time(
        self, fake_indexer: Indexer, fake_client: FakeClient
    ):
        fake_indexer.index_document({"id": "doc-1", "title": "Otter report"})
        assert fake_indexer.remove("document", "doc-1")["status"] == "deleted"
        assert fake_indexer.remove("document", "doc-1")["status"] == "not_found"
        assert fake_client.index(DOCUMENTS_INDEX).documents == {}

    def test_deleting_an_unknown_id_is_not_an_error(self, fake_indexer: Indexer):
        assert fake_indexer.remove("file", "never-existed")["status"] == "not_found"

    def test_reindexing_after_a_delete_restores_a_single_copy(
        self, fake_indexer: Indexer, fake_client: FakeClient
    ):
        payload = {"id": "doc-1", "title": "Otter report"}
        fake_indexer.index_document(payload)
        fake_indexer.remove("document", "doc-1")
        fake_indexer.index_document(payload)
        assert list(fake_client.index(DOCUMENTS_INDEX).documents) == ["doc-1"]

    @pytest.mark.parametrize("doc_type", ["", "folder", "Document", "documents", None])
    def test_removing_an_unsupported_type_is_rejected(self, fake_indexer: Indexer, doc_type):
        with pytest.raises(ValueError, match="Invalid type"):
            fake_indexer.remove(doc_type, "doc-1")


class TestReindexIdempotency:
    """A full reindex is repeatable and leaves exactly one copy of each item."""

    def test_reindexing_twice_yields_one_document(
        self, fake_service: MeiliSearchService, fake_client: FakeClient
    ):
        documents = [{"id": "doc-1", "title": "Otter report", "type": "document"}]
        first = fake_service.reindex(documents=documents)
        second = fake_service.reindex(documents=documents)
        assert first == second
        assert first["status"] == "reindexed"
        assert first["indexed_counts"] == {"documents": 1, "files": 0}
        assert list(fake_client.index(DOCUMENTS_INDEX).documents) == ["doc-1"]

    def test_reindex_drops_documents_that_no_longer_exist_in_the_source(
        self, fake_service: MeiliSearchService, fake_client: FakeClient
    ):
        fake_service.reindex(documents=[{"id": "doc-1"}, {"id": "doc-2"}])
        fake_service.reindex(documents=[{"id": "doc-1"}])
        assert list(fake_client.index(DOCUMENTS_INDEX).documents) == ["doc-1"]

    def test_reindex_recreates_both_indices(
        self, fake_service: MeiliSearchService, fake_client: FakeClient
    ):
        fake_service.reindex()
        assert fake_client.deleted_indices == [DOCUMENTS_INDEX, FILES_INDEX]
        assert fake_client.created_indices == [DOCUMENTS_INDEX, FILES_INDEX]

    def test_reindex_without_data_leaves_empty_indices(
        self, fake_service: MeiliSearchService, fake_client: FakeClient
    ):
        fake_service.index_document({"id": "doc-1", "title": "Otter report"})
        result = fake_service.reindex()
        assert result["indexed_counts"] == {"documents": 0, "files": 0}
        assert fake_client.index(DOCUMENTS_INDEX).documents == {}

    @pytest.mark.parametrize(
        ("count", "expected_batches"),
        [
            (REINDEX_BATCH_SIZE - 1, 1),
            (REINDEX_BATCH_SIZE, 1),
            (REINDEX_BATCH_SIZE + 1, 2),
        ],
    )
    def test_bulk_indexing_batches_at_the_batch_size(
        self,
        fake_service: MeiliSearchService,
        fake_client: FakeClient,
        count: int,
        expected_batches: int,
    ):
        documents = [{"id": f"doc-{i}", "title": f"Otter {i}"} for i in range(count)]
        result = fake_service.reindex(documents=documents)
        index = fake_client.index(DOCUMENTS_INDEX)
        assert len(index.add_documents_calls) == expected_batches
        assert len(index.documents) == count
        assert result["indexed_counts"]["documents"] == count

    def test_duplicate_ids_within_one_reindex_collapse_to_one_document(
        self, fake_service: MeiliSearchService, fake_client: FakeClient
    ):
        documents = [
            {"id": "doc-1", "title": "First"},
            {"id": "doc-1", "title": "Second"},
        ]
        result = fake_service.reindex(documents=documents)
        assert result["indexed_counts"]["documents"] == 2
        assert list(fake_client.index(DOCUMENTS_INDEX).documents) == ["doc-1"]

    def test_missing_indices_are_tolerated_on_the_delete_pass(
        self, fake_service: MeiliSearchService, fake_client: FakeClient
    ):
        """A first-ever reindex has nothing to delete and must still succeed."""
        with patch.object(
            fake_client, "delete_index", side_effect=api_error("Index not found.", 404)
        ):
            result = fake_service.reindex(documents=[{"id": "doc-1"}])
        assert result["status"] == "reindexed"
        assert list(fake_client.index(DOCUMENTS_INDEX).documents) == ["doc-1"]

    def test_files_are_reindexed_alongside_documents(
        self, fake_service: MeiliSearchService, fake_client: FakeClient
    ):
        result = fake_service.reindex(
            documents=[{"id": "doc-1"}], files=[{"id": "file-1"}, {"id": "file-2"}]
        )
        assert result["indexed_counts"] == {"documents": 1, "files": 2}
        assert len(fake_client.index(FILES_INDEX).documents) == 2


class TestReindexEndpoint:
    """The admin reindex endpoint, crawling stubbed source services."""

    def test_reindexing_twice_through_the_api_yields_one_document(
        self, fake_backed_client, fake_client: FakeClient
    ):
        documents = [{"id": "doc-1", "title": "Otter report", "owner_id": "user-1"}]
        files = [{"id": "file-1", "name": "otter.png", "ownerId": "user-1"}]
        with patch(
            "app.services.indexer.requests.get", side_effect=source_responses(documents, files)
        ):
            first = fake_backed_client.post("/api/v1/search/reindex")
            second = fake_backed_client.post("/api/v1/search/reindex")
        assert first.status_code == 200
        assert first.get_json() == second.get_json()
        assert list(fake_client.index(DOCUMENTS_INDEX).documents) == ["doc-1"]
        assert list(fake_client.index(FILES_INDEX).documents) == ["file-1"]

    def test_reindex_empties_the_index_when_the_sources_are_empty(
        self, fake_backed_client, fake_client: FakeClient
    ):
        with patch(
            "app.services.indexer.requests.get",
            side_effect=source_responses([{"id": "doc-1", "title": "Otter"}], []),
        ):
            fake_backed_client.post("/api/v1/search/reindex")
        with patch("app.services.indexer.requests.get", side_effect=source_responses([], [])):
            response = fake_backed_client.post("/api/v1/search/reindex")
        assert response.get_json()["indexed_counts"] == {"documents": 0, "files": 0}
        assert fake_client.index(DOCUMENTS_INDEX).documents == {}

    def test_unreachable_source_service_still_recreates_the_index(
        self, fake_backed_client, fake_client: FakeClient
    ):
        """The index is cleared even though nothing could be fetched."""
        with patch(
            "app.services.indexer.requests.get",
            side_effect=requests.RequestException("document-service down"),
        ):
            response = fake_backed_client.post("/api/v1/search/reindex")
        assert response.status_code == 200
        assert response.get_json()["indexed_counts"] == {"documents": 0, "files": 0}
        assert fake_client.index(DOCUMENTS_INDEX).documents == {}

    def test_source_error_status_stops_the_crawl(self, fake_backed_client):
        error_response = MagicMock()
        error_response.status_code = 503
        with patch("app.services.indexer.requests.get", return_value=error_response):
            response = fake_backed_client.post("/api/v1/search/reindex")
        assert response.status_code == 200
        assert response.get_json()["indexed_counts"] == {"documents": 0, "files": 0}

    def test_indexed_document_is_searchable_and_not_duplicated(
        self, fake_backed_client, fake_client: FakeClient
    ):
        payload = {"id": "doc-1", "title": "Otter report", "content": "otters swim"}
        assert fake_backed_client.post("/api/v1/search/index/document", json=payload).status_code == 201
        assert fake_backed_client.post("/api/v1/search/index/document", json=payload).status_code == 201
        response = fake_backed_client.get("/api/v1/search/?q=otter&type=document")
        body = response.get_json()
        assert body["total"] == 1
        assert [hit["id"] for hit in body["results"]] == ["doc-1"]
