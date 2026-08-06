"""Tests for behaviour when a backing dependency is unreachable.

Covers MeiliSearch being down (the search backend) and Redis being down (only
used for chaos-flag lookups, so it must never affect a user-facing response).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import meilisearch.errors
import pytest
import redis as redis_lib

MEILI_DOWN = meilisearch.errors.MeilisearchCommunicationError("connection refused")


@pytest.fixture()
def meilisearch_down(mock_meilisearch_client: MagicMock) -> MagicMock:
    """Make every MeiliSearch call fail as if the server were unreachable."""
    index = mock_meilisearch_client.index.return_value
    index.search.side_effect = MEILI_DOWN
    index.add_documents.side_effect = MEILI_DOWN
    index.get_document.side_effect = MEILI_DOWN
    mock_meilisearch_client.health.side_effect = MEILI_DOWN
    return mock_meilisearch_client


@pytest.fixture()
def redis_down():
    """Make the chaos-flag Redis lookup fail as if Redis were unreachable."""
    failing_redis = MagicMock()
    failing_redis.exists.side_effect = redis_lib.ConnectionError("connection refused")
    with patch("app.api.search._get_redis", return_value=failing_redis):
        yield failing_redis


class TestMeiliSearchUnavailable:
    """Tests for requests served while MeiliSearch is unreachable."""

    def test_search_get_with_backend_unreachable_returns_json_error_not_stack_trace(
        self, client, meilisearch_down
    ):
        """A backend outage surfaces as a JSON error payload, never a rendered traceback."""
        response = client.get("/api/v1/search/?q=test")
        assert response.status_code == 500
        data = response.get_json()
        assert data == {"error": "Search failed"}

    def test_advanced_search_with_backend_unreachable_returns_json_error_not_stack_trace(
        self, client, meilisearch_down
    ):
        """The advanced path degrades the same way as the simple path."""
        response = client.post("/api/v1/search/advanced", json={"q": "test"})
        assert response.status_code == 500
        data = response.get_json()
        assert data == {"error": "Advanced search failed"}

    def test_suggest_with_backend_unreachable_returns_200_with_no_suggestions(
        self, client, meilisearch_down
    ):
        """Autocomplete degrades to an empty list instead of failing the request."""
        response = client.get("/api/v1/search/suggest?q=te")
        assert response.status_code == 200
        assert response.get_json() == {"suggestions": [], "query": "te"}

    def test_readiness_with_backend_unreachable_returns_503(self, client, meilisearch_down):
        """Readiness reports the dependency outage so traffic can be drained."""
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.get_json()["ready"] is False

    def test_liveness_with_backend_unreachable_returns_200(self, client, meilisearch_down):
        """Liveness stays green — the process itself is healthy."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.get_json()["status"] == "alive"

    def test_index_document_with_backend_unreachable_returns_json_error(self, client, meilisearch_down):
        """Indexing reports a JSON error rather than leaking the backend exception."""
        response = client.post(
            "/api/v1/search/index/document", json={"id": "doc-1", "title": "Quarterly report"}
        )
        assert response.status_code == 500
        assert response.get_json() == {"error": "Failed to index document"}

    def test_analytics_with_backend_unreachable_returns_200(self, client, meilisearch_down):
        """Analytics is served from memory and is unaffected by a backend outage."""
        response = client.get("/api/v1/search/analytics")
        assert response.status_code == 200
        assert "popular_queries" in response.get_json()

    @pytest.mark.skip(
        reason="DEFECT: a MeiliSearch outage is reported as 500 Internal Server Error; "
        "a dependency outage should be a 503 Service Unavailable so callers and "
        "load balancers can distinguish 'we are broken' from 'our backend is down'."
    )
    def test_search_get_with_backend_unreachable_returns_503(self, client, meilisearch_down):
        """A dependency outage should be reported as 503, not 500."""
        response = client.get("/api/v1/search/?q=test")
        assert response.status_code == 503


class TestRedisUnavailable:
    """Tests for requests served while the chaos-flag Redis is unreachable."""

    def test_suggest_with_redis_unreachable_returns_200(self, client, redis_down, mock_meilisearch_client):
        """Redis is not on the critical path: suggestions are still served."""
        mock_meilisearch_client.index.return_value.search.return_value = {
            "estimatedTotalHits": 1,
            "hits": [{"title": "Test Doc"}],
        }

        response = client.get("/api/v1/search/suggest?q=te")
        assert response.status_code == 200
        assert response.get_json()["suggestions"] == ["Test Doc"]

    def test_suggest_with_redis_and_backend_unreachable_returns_200(
        self, client, redis_down, meilisearch_down
    ):
        """With both dependencies down the endpoint still answers with an empty list."""
        response = client.get("/api/v1/search/suggest?q=te")
        assert response.status_code == 200
        assert response.get_json() == {"suggestions": [], "query": "te"}
