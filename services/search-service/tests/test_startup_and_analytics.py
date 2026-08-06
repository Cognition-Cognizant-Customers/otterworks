"""WP-07: app wiring, analytics bookkeeping, and the chaos-flag suggest path."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from app.services import meilisearch_client
from app.services.meilisearch_client import (
    get_search_analytics,
    record_search_analytics,
)
from tests.conftest_wp07 import build_app, build_config, fake_meili, seed_documents
from tests.fakes import FakeMeiliClient, make_communication_error

ENV_VARS = (
    "MEILISEARCH_URL",
    "MEILISEARCH_API_KEY",
    "MEILISEARCH_DOCUMENTS_INDEX",
    "MEILISEARCH_FILES_INDEX",
    "SQS_ENABLED",
    "SQS_QUEUE_URL",
    "REQUIRE_AUTH",
    "SEARCH_SERVICE_TOKEN",
    "PORT",
    "LOG_LEVEL",
)


@pytest.fixture()
def meili() -> FakeMeiliClient:
    return FakeMeiliClient()


@pytest.fixture()
def client(meili: FakeMeiliClient):
    return build_app(meili).test_client()


@pytest.fixture()
def clean_analytics() -> Iterator[None]:
    with meilisearch_client._analytics_lock:
        saved = dict(meilisearch_client._search_analytics)
        saved["queries"] = list(saved["queries"])
        meilisearch_client._search_analytics.update(
            {"queries": [], "total_searches": 0, "total_results": 0}
        )
    try:
        yield
    finally:
        with meilisearch_client._analytics_lock:
            meilisearch_client._search_analytics.update(saved)


class TestAppWiring:
    def test_default_config_is_read_from_the_environment(self, meili, monkeypatch):
        for name in ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        with fake_meili(meili):
            from app.main import create_app

            app = create_app()
        assert app.config["APP_CONFIG"].port == 8087
        assert "SQS_CONSUMER" not in app.config

    def test_sqs_consumer_is_started_when_enabled(self, meili):
        config = build_config(sqs_enabled=True)
        object.__setattr__(
            config.sqs, "queue_url", "https://sqs.us-east-1.amazonaws.com/0/search"
        )
        with patch("boto3.client", return_value=MagicMock()):
            app = build_app(meili, config)
            consumer = app.config["SQS_CONSUMER"]
            try:
                assert consumer._running is True
                assert consumer._thread is not None
            finally:
                consumer.stop()
        assert consumer._running is False

    def test_sqs_consumer_is_absent_when_disabled(self, meili):
        app = build_app(meili, build_config(sqs_enabled=False))
        assert "SQS_CONSUMER" not in app.config

    def test_cors_headers_are_emitted_for_the_web_app_origin(self, client):
        response = client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"

    def test_metrics_endpoint_exposes_the_search_counter(self, client):
        client.get("/api/v1/search/?q=metric-probe")
        body = client.get("/metrics").get_data(as_text=True)
        assert "search_service_searches_total" in body


class TestAnalyticsBookkeeping:
    def test_counters_accumulate_across_queries(self, clean_analytics):
        record_search_analytics("alpha", 2)
        record_search_analytics("alpha", 4)
        record_search_analytics("beta", 0)
        analytics = get_search_analytics()
        assert analytics.total_searches == 3
        assert analytics.popular_queries[0] == {"query": "alpha", "count": 2}
        assert analytics.zero_result_queries == [{"query": "beta", "count": 1}]
        assert analytics.avg_results_per_query == 2.0

    def test_average_is_zero_when_nothing_has_been_searched(self, clean_analytics):
        analytics = get_search_analytics()
        assert analytics.avg_results_per_query == 0.0
        assert analytics.popular_queries == []

    def test_only_the_top_twenty_queries_are_reported(self, clean_analytics):
        for i in range(25):
            record_search_analytics(f"query-{i:02d}", 0)
        assert len(get_search_analytics().popular_queries) == 20
        assert len(get_search_analytics().zero_result_queries) == 20

    @pytest.mark.parametrize(
        ("recorded", "expected_retained"),
        [(2, 2), (3, 3), (4, 3)],
        ids=["max-1", "max", "max+1"],
    )
    def test_the_query_ring_buffer_is_trimmed_at_its_cap(
        self, clean_analytics, recorded, expected_retained
    ):
        """Boundary trio around MAX_ANALYTICS_ENTRIES (patched to 3)."""
        with patch.object(meilisearch_client, "MAX_ANALYTICS_ENTRIES", 3):
            for i in range(recorded):
                record_search_analytics(f"q-{i}", 1)
        with meilisearch_client._analytics_lock:
            retained = list(meilisearch_client._search_analytics["queries"])
        assert len(retained) == expected_retained
        assert retained[-1]["query"] == f"q-{recorded - 1}"
        # The counter is not trimmed, only the sampled query list.
        assert get_search_analytics().total_searches == recorded

    def test_a_failed_search_is_not_counted(self, client, meili, clean_analytics):
        meili.index("wp07-documents").search_error = make_communication_error()
        assert client.get("/api/v1/search/?q=doomed").status_code == 500
        assert get_search_analytics().total_searches == 0


class TestSuggestChaosFlag:
    """The suggest handler has a Redis-gated chaos path (see AGENTS.md).

    These tests pin both sides of the flag; the 500 is the intended injected
    failure for bug-hunt labs, not a defect to fix.
    """

    def _redis(self, flag_set: bool) -> MagicMock:
        fake = MagicMock()
        fake.exists.return_value = 1 if flag_set else 0
        return fake

    def test_suggest_is_healthy_when_the_chaos_flag_is_clear(self, client, meili):
        seed_documents(meili, {"id": "doc-1", "title": "alpha memo", "owner_id": "u"})
        with patch("app.api.search._get_redis", return_value=self._redis(False)):
            response = client.get("/api/v1/search/suggest?q=alpha")
        assert response.status_code == 200
        assert response.get_json()["suggestions"] == ["alpha memo"]

    def test_suggest_fails_when_the_chaos_flag_is_set(self, meili):
        seed_documents(meili, {"id": "doc-1", "title": "alpha memo", "owner_id": "u"})
        app = build_app(meili)
        # Let Flask turn the injected error into a 500 the way it would in
        # production instead of re-raising it into the test.
        app.config["PROPAGATE_EXCEPTIONS"] = False
        with patch("app.api.search._get_redis", return_value=self._redis(True)):
            response = app.test_client().get("/api/v1/search/suggest?q=alpha")
        assert response.status_code == 500

    def test_chaos_suggest_fails_even_with_an_empty_index(self, meili):
        app = build_app(meili)
        app.config["PROPAGATE_EXCEPTIONS"] = False
        with patch("app.api.search._get_redis", return_value=self._redis(True)):
            response = app.test_client().get("/api/v1/search/suggest?q=alpha")
        assert response.status_code == 500

    def test_an_unreachable_redis_leaves_chaos_inactive(self, client, meili):
        """Fail-open: a Redis outage must not disable suggestions."""
        broken = MagicMock()
        broken.exists.side_effect = ConnectionError("redis down")
        with patch("app.api.search._get_redis", return_value=broken):
            response = client.get("/api/v1/search/suggest?q=alpha")
        assert response.status_code == 200


class TestIndexApiErrorPaths:
    def test_index_document_with_an_empty_body_is_400(self, client):
        response = client.post(
            "/api/v1/search/index/document", json={}, content_type="application/json"
        )
        assert response.status_code == 400
        assert response.get_json() == {"error": "Request body is required"}

    def test_analytics_returns_500_when_the_store_cannot_be_read(self, client):
        with patch(
            "app.api.search.get_search_analytics", side_effect=RuntimeError("boom")
        ):
            response = client.get("/api/v1/search/analytics")
        assert response.status_code == 500
        assert response.get_json() == {"error": "Failed to retrieve analytics"}

    def test_index_file_with_an_empty_body_is_400(self, client):
        response = client.post(
            "/api/v1/search/index/file", json={}, content_type="application/json"
        )
        assert response.status_code == 400

    def test_delete_returns_500_when_meilisearch_fails(self, client, meili):
        seed_documents(meili, {"id": "doc-1", "title": "T", "owner_id": "u"})
        meili.wait_error = make_communication_error()
        response = client.delete("/api/v1/search/index/document/doc-1")
        assert response.status_code == 500
        assert response.get_json() == {"error": "Failed to remove from index"}

    def test_reindex_returns_500_when_meilisearch_fails(self, client, meili):
        with patch.object(meili, "create_index", side_effect=RuntimeError("boom")):
            response = client.post("/api/v1/search/reindex")
        assert response.status_code == 500
        assert response.get_json() == {"error": "Failed to reindex"}
