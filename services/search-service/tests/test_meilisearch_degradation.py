"""How the API degrades when MeiliSearch is unavailable or erroring.

Every case pins today's behaviour: which endpoints fail loudly, which fail
silently, and which stay up because they do not touch the backend.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import meilisearch
import pytest

from app.config import AppConfig, AuthConfig, MeiliSearchConfig, SQSConfig
from app.main import create_app
from tests.fakes import api_error

UNAVAILABLE = meilisearch.errors.MeilisearchCommunicationError("connection refused")


@pytest.fixture()
def unavailable_client(client, mock_meilisearch_client: MagicMock):
    """Test client whose MeiliSearch backend refuses every call."""
    mock_meilisearch_client.health.side_effect = UNAVAILABLE
    index = mock_meilisearch_client.index.return_value
    index.search.side_effect = UNAVAILABLE
    index.add_documents.side_effect = UNAVAILABLE
    index.delete_document.side_effect = UNAVAILABLE
    index.get_document.side_effect = UNAVAILABLE
    mock_meilisearch_client.delete_index.side_effect = UNAVAILABLE
    mock_meilisearch_client.get_index.side_effect = UNAVAILABLE
    mock_meilisearch_client.create_index.side_effect = UNAVAILABLE
    return client


class TestReadOperationsWhenUnavailable:
    """Search paths surface the outage differently from one another."""

    def test_search_returns_500(self, unavailable_client):
        response = unavailable_client.get("/api/v1/search/?q=otter")
        assert response.status_code == 500
        assert response.get_json() == {"error": "Search failed"}

    def test_advanced_search_returns_500(self, unavailable_client):
        response = unavailable_client.post("/api/v1/search/advanced", json={"q": "otter"})
        assert response.status_code == 500
        assert response.get_json() == {"error": "Advanced search failed"}

    def test_suggest_degrades_to_an_empty_list_with_200(self, unavailable_client):
        """Autocomplete fails open so the search bar keeps working."""
        response = unavailable_client.get("/api/v1/search/suggest?q=ot")
        assert response.status_code == 200
        assert response.get_json() == {"suggestions": [], "query": "ot"}

    def test_analytics_stays_available(self, unavailable_client):
        """Analytics are computed in-process and do not touch MeiliSearch."""
        response = unavailable_client.get("/api/v1/search/analytics")
        assert response.status_code == 200
        assert "total_searches" in response.get_json()

    def test_liveness_stays_green(self, unavailable_client):
        assert unavailable_client.get("/health").status_code == 200

    def test_readiness_reports_the_dependency(self, unavailable_client):
        response = unavailable_client.get("/health/ready")
        assert response.status_code == 503
        assert response.get_json() == {"ready": False, "reason": "meilisearch_unavailable"}

    def test_metrics_stay_available(self, unavailable_client):
        assert unavailable_client.get("/metrics").status_code == 200


class TestWriteOperationsWhenUnavailable:
    """Indexing endpoints fail loudly rather than pretending to succeed."""

    def test_index_document_returns_500(self, unavailable_client):
        response = unavailable_client.post(
            "/api/v1/search/index/document", json={"id": "doc-1", "title": "Otter"}
        )
        assert response.status_code == 500
        assert response.get_json() == {"error": "Failed to index document"}

    def test_index_file_returns_500(self, unavailable_client):
        response = unavailable_client.post(
            "/api/v1/search/index/file", json={"id": "file-1", "name": "otter.png"}
        )
        assert response.status_code == 500

    def test_delete_reports_not_found_when_the_lookup_fails(self, client, mock_meilisearch_client):
        """A backend error on the existence check is indistinguishable from 404."""
        mock_meilisearch_client.index.return_value.get_document.side_effect = api_error(
            "Document `doc-1` not found.", 404
        )
        response = client.delete("/api/v1/search/index/document/doc-1")
        assert response.status_code == 404
        assert response.get_json()["status"] == "not_found"

    def test_delete_returns_500_when_the_delete_itself_fails(
        self, client, mock_meilisearch_client
    ):
        index = mock_meilisearch_client.index.return_value
        index.get_document.return_value = {"id": "doc-1"}
        index.delete_document.side_effect = UNAVAILABLE
        response = client.delete("/api/v1/search/index/document/doc-1")
        assert response.status_code == 500

    def test_reindex_returns_500(self, unavailable_client):
        with patch("app.services.indexer.requests.get", side_effect=OSError("no route")):
            response = unavailable_client.post("/api/v1/search/reindex")
        assert response.status_code == 500
        assert response.get_json() == {"error": "Failed to reindex"}


class TestTaskFailures:
    """MeiliSearch accepted the task but the task itself failed."""

    def test_failed_index_task_becomes_a_500(self, client, mock_meilisearch_client):
        mock_meilisearch_client.wait_for_task.return_value = MagicMock(
            status="failed", error={"code": "internal"}
        )
        response = client.post(
            "/api/v1/search/index/document", json={"id": "doc-1", "title": "Otter"}
        )
        assert response.status_code == 500

    def test_dict_shaped_task_result_is_understood(self, client, mock_meilisearch_client):
        mock_meilisearch_client.wait_for_task.return_value = {
            "status": "failed",
            "error": {"code": "internal"},
        }
        response = client.post(
            "/api/v1/search/index/document", json={"id": "doc-1", "title": "Otter"}
        )
        assert response.status_code == 500

    def test_lmdb_key_exists_is_retried_once_after_a_delete(self, client, mock_meilisearch_client):
        """Known MeiliSearch delete-then-re-add bug: the file add is retried."""
        results = [
            MagicMock(status="failed", error="MDB_KEYEXIST: Key/data pair already exists"),
            MagicMock(status="succeeded", error=None),
            MagicMock(status="succeeded", error=None),
        ]
        mock_meilisearch_client.wait_for_task.side_effect = results
        response = client.post(
            "/api/v1/search/index/file", json={"id": "file-1", "name": "otter.png"}
        )
        assert response.status_code == 201
        index = mock_meilisearch_client.index.return_value
        index.delete_document.assert_called_once_with("file-1")
        assert index.add_documents.call_count == 2

    def test_other_task_failures_are_not_retried(self, client, mock_meilisearch_client):
        mock_meilisearch_client.wait_for_task.return_value = MagicMock(
            status="failed", error="index_not_found"
        )
        response = client.post(
            "/api/v1/search/index/file", json={"id": "file-1", "name": "otter.png"}
        )
        assert response.status_code == 500
        mock_meilisearch_client.index.return_value.delete_document.assert_not_called()


class TestStartupDegradation:
    """The service must boot even with the backend down."""

    def test_app_starts_when_indices_cannot_be_created(self, mock_meilisearch_client):
        mock_meilisearch_client.get_index.side_effect = UNAVAILABLE
        config = AppConfig(
            service_name="search-service-test",
            meilisearch=MeiliSearchConfig(
                documents_index="test-otterworks-documents",
                files_index="test-otterworks-files",
            ),
            sqs=SQSConfig(enabled=False),
            auth=AuthConfig(service_token="", require_auth=False),
        )
        with patch("app.services.meilisearch_client.meilisearch.Client") as mock_cls:
            mock_cls.return_value = mock_meilisearch_client
            app = create_app(config)
        app.config["TESTING"] = True
        assert app.test_client().get("/health").status_code == 200

    def test_ping_reports_false_instead_of_raising(self, meilisearch_service, mock_meilisearch_client):
        mock_meilisearch_client.health.side_effect = UNAVAILABLE
        assert meilisearch_service.ping() is False

    def test_ping_reports_true_when_reachable(self, meilisearch_service):
        assert meilisearch_service.ping() is True
