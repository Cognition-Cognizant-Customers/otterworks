"""WP-07: behaviour when MeiliSearch is unavailable or failing.

Each test asserts what the service *actually* does today, including whether the
upstream error text reaches the client. Two findings are recorded here as
strict-xfail tests.
"""

from __future__ import annotations

import pytest

from tests.conftest_wp07 import DOCUMENTS_INDEX, FILES_INDEX, build_app
from tests.fakes import (
    FakeMeiliClient,
    make_api_error,
    make_communication_error,
    make_timeout_error,
)


@pytest.fixture()
def meili() -> FakeMeiliClient:
    return FakeMeiliClient()


@pytest.fixture()
def client(meili: FakeMeiliClient):
    return build_app(meili).test_client()


def _fail_search(meili: FakeMeiliClient, error: Exception) -> None:
    for index_name in (DOCUMENTS_INDEX, FILES_INDEX):
        meili.index(index_name).search_error = error


class TestSearchDegradation:
    def test_connection_error_returns_500_without_leaking_upstream_detail(
        self, client, meili
    ):
        _fail_search(meili, make_communication_error("connection refused"))
        response = client.get("/api/v1/search/?q=test")
        assert response.status_code == 500
        assert response.get_json() == {"error": "Search failed"}

    def test_timeout_returns_500_without_leaking_upstream_detail(self, client, meili):
        _fail_search(meili, make_timeout_error("read timed out"))
        response = client.get("/api/v1/search/?q=test")
        assert response.status_code == 500
        assert "timed out" not in response.get_data(as_text=True)

    def test_upstream_5xx_is_reported_as_400_and_leaks_the_upstream_message(
        self, client, meili
    ):
        """Current behaviour: a MeiliSearch 5xx is surfaced as a client error.

        ``MeiliSearchService.search`` converts every ``MeilisearchApiError`` into
        ``ValueError("Invalid search filter: ...")`` and the handler maps
        ``ValueError`` to 400, so an upstream outage looks like bad user input
        and the upstream message is echoed back. See the two xfail tests below.
        """
        _fail_search(meili, make_api_error(500, "index is corrupted at /var/lib/meili"))
        response = client.get("/api/v1/search/?q=test")
        assert response.status_code == 400
        assert "Invalid search filter" in response.get_json()["error"]
        assert "/var/lib/meili" in response.get_json()["error"]

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "FINDING 1: app/services/meilisearch_client.py:search() maps every "
            "MeilisearchApiError to ValueError('Invalid search filter'), so a "
            "MeiliSearch 5xx is returned to the caller as HTTP 400 instead of "
            "502/503."
        ),
    )
    def test_upstream_5xx_should_not_be_a_client_error(self, client, meili):
        _fail_search(meili, make_api_error(500, "internal"))
        response = client.get("/api/v1/search/?q=test")
        assert response.status_code >= 500

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "FINDING 2: the upstream MeiliSearch error message (which can carry "
            "internal paths/index names) is interpolated into the 400 response "
            "body by app/api/search.py's `except ValueError` branch."
        ),
    )
    def test_upstream_error_text_should_not_reach_the_client(self, client, meili):
        _fail_search(meili, make_api_error(500, "index /var/lib/meili/documents is corrupted"))
        response = client.get("/api/v1/search/?q=test")
        assert "/var/lib/meili" not in response.get_data(as_text=True)

    def test_api_error_on_one_index_fails_the_whole_request(self, client, meili):
        """Partial availability is not tolerated: one bad index fails the query."""
        meili.index(FILES_INDEX).search_error = make_api_error(503, "unavailable")
        response = client.get("/api/v1/search/?q=test")
        assert response.status_code == 400


class TestSuggestDegradation:
    def test_suggest_masks_an_outage_with_an_empty_list(self, client, meili):
        """/suggest swallows upstream failures and returns 200 + no suggestions."""
        _fail_search(meili, make_communication_error())
        response = client.get("/api/v1/search/suggest?q=re")
        assert response.status_code == 200
        assert response.get_json() == {"suggestions": [], "query": "re"}

    def test_suggest_below_minimum_prefix_never_touches_meilisearch(self, client, meili):
        """Boundary: len(prefix) < 2 short-circuits before any upstream call."""
        response = client.get("/api/v1/search/suggest?q=r")
        assert response.status_code == 200
        assert meili.index(DOCUMENTS_INDEX).search_calls == []

    def test_suggest_at_minimum_prefix_length_queries_meilisearch(self, client, meili):
        response = client.get("/api/v1/search/suggest?q=re")
        assert response.status_code == 200
        assert meili.index(DOCUMENTS_INDEX).search_calls


class TestAdvancedSearchDegradation:
    def test_advanced_search_returns_500_on_upstream_api_error(self, client, meili):
        _fail_search(meili, make_api_error(500, "internal"))
        response = client.post("/api/v1/search/advanced", json={"q": "test"})
        assert response.status_code == 500
        assert response.get_json() == {"error": "Advanced search failed"}

    def test_advanced_search_returns_500_on_connection_error(self, client, meili):
        _fail_search(meili, make_communication_error())
        response = client.post("/api/v1/search/advanced", json={"q": "test"})
        assert response.status_code == 500


class TestReadinessAndStartup:
    def test_readiness_is_503_when_meilisearch_is_down(self):
        meili = FakeMeiliClient(healthy=False)
        client = build_app(meili).test_client()
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.get_json()["reason"] == "meilisearch_unavailable"

    def test_readiness_is_200_when_meilisearch_is_up(self, client):
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.get_json() == {"ready": True}

    def test_app_still_starts_when_index_creation_fails(self):
        """Startup must be non-fatal if MeiliSearch is unreachable."""
        meili = FakeMeiliClient(healthy=False)
        meili.wait_error = make_communication_error()
        app = build_app(meili)
        response = app.test_client().get("/health")
        assert response.status_code == 200
        assert response.get_json()["status"] == "alive"


class TestIndexingDegradation:
    def test_index_document_returns_500_when_the_task_fails(self, client, meili):
        meili.task_status = "failed"
        response = client.post(
            "/api/v1/search/index/document", json={"id": "doc-1", "title": "T"}
        )
        assert response.status_code == 500
        assert response.get_json() == {"error": "Failed to index document"}

    def test_index_file_returns_500_when_meilisearch_is_unreachable(self, client, meili):
        meili.wait_error = make_communication_error()
        response = client.post(
            "/api/v1/search/index/file", json={"id": "file-1", "name": "a.pdf"}
        )
        assert response.status_code == 500
        assert response.get_json() == {"error": "Failed to index file"}

    def test_delete_of_a_missing_document_is_404_not_500(self, client, meili):
        response = client.delete("/api/v1/search/index/document/does-not-exist")
        assert response.status_code == 404
        assert response.get_json()["status"] == "not_found"
