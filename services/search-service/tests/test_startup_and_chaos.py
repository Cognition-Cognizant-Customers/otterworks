"""Application wiring and the chaos-flag path in the suggest endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.api import search as search_api
from app.config import AppConfig, AuthConfig, MeiliSearchConfig, SQSConfig
from app.main import create_app

CHAOS_KEY = "chaos:search-service:suggest_500"


def build_app(mock_client: MagicMock, config: AppConfig, *, testing: bool = True):
    with patch("app.services.meilisearch_client.meilisearch.Client") as mock_cls:
        mock_cls.return_value = mock_client
        app = create_app(config)
    app.config["TESTING"] = testing
    return app


def open_config(**overrides) -> AppConfig:
    defaults: dict = {
        "service_name": "search-service-test",
        "meilisearch": MeiliSearchConfig(
            documents_index="test-otterworks-documents",
            files_index="test-otterworks-files",
        ),
        "sqs": SQSConfig(enabled=False),
        "auth": AuthConfig(service_token="", require_auth=False),
    }
    defaults.update(overrides)
    return AppConfig(**defaults)


class TestAppFactory:
    """``create_app`` wiring."""

    def test_default_configuration_is_used_when_none_is_supplied(
        self, mock_meilisearch_client: MagicMock
    ):
        with patch("app.services.meilisearch_client.meilisearch.Client") as mock_cls:
            mock_cls.return_value = mock_meilisearch_client
            app = create_app()
        assert app.config["APP_CONFIG"].service_name == "search-service"
        assert "SEARCH_SERVICE" in app.config

    def test_sqs_consumer_is_not_started_when_disabled(self, mock_meilisearch_client: MagicMock):
        app = build_app(mock_meilisearch_client, open_config())
        assert "SQS_CONSUMER" not in app.config

    def test_sqs_consumer_is_started_when_enabled(self, mock_meilisearch_client: MagicMock):
        config = open_config(
            sqs=SQSConfig(
                enabled=True,
                queue_url="https://sqs.us-east-1.amazonaws.com/000000000000/search-index",
                endpoint_url="http://localstack:4566",
                max_messages=5,
            )
        )
        with patch("app.main.SQSConsumer") as consumer_cls:
            app = build_app(mock_meilisearch_client, config)
        consumer_cls.assert_called_once()
        assert consumer_cls.call_args.kwargs["queue_url"] == config.sqs.queue_url
        assert consumer_cls.call_args.kwargs["max_messages"] == 5
        consumer_cls.return_value.start.assert_called_once_with()
        assert app.config["SQS_CONSUMER"] is consumer_cls.return_value

    def test_request_metrics_are_recorded_for_api_routes(self, client):
        client.get("/api/v1/search/?q=otter")
        body = client.get("/metrics").get_data(as_text=True)
        assert 'endpoint="/api/v1/search/"' in body

    def test_health_requests_are_excluded_from_request_metrics(self, client):
        client.get("/health")
        body = client.get("/metrics").get_data(as_text=True)
        assert 'endpoint="/health"' not in body


class TestChaosFlag:
    """``chaos:search-service:suggest_500`` reproduces the documented incident."""

    def test_suggest_returns_500_when_the_chaos_flag_is_set(
        self, mock_meilisearch_client: MagicMock
    ):
        """Injected failure from docs/runbooks/search-suggest-500.md.

        The enrichment path reads ``_rankingScore``, which MeiliSearch does not
        return unless it is requested, so the handler raises KeyError. This is a
        chaos scenario in the golden app (scripts/bug-catalog.yaml), not a
        regression to fix — the test pins it so it cannot disappear silently.
        """
        app = build_app(mock_meilisearch_client, open_config(), testing=False)
        with patch.object(search_api, "_chaos_active", return_value=True):
            response = app.test_client().get("/api/v1/search/suggest?q=ot")
        assert response.status_code == 500

    def test_suggest_is_healthy_when_the_chaos_flag_is_clear(self, client):
        with patch.object(search_api, "_chaos_active", return_value=False):
            response = client.get("/api/v1/search/suggest?q=ot")
        assert response.status_code == 200

    def test_chaos_flag_is_read_from_redis(self):
        redis = MagicMock()
        redis.exists.return_value = 1
        with patch.object(search_api, "_get_redis", return_value=redis):
            assert search_api._chaos_active(CHAOS_KEY) is True
        redis.exists.assert_called_once_with(CHAOS_KEY)

    def test_chaos_flag_defaults_to_inactive_when_redis_is_unreachable(self):
        with patch.object(search_api, "_get_redis", side_effect=OSError("no redis")):
            assert search_api._chaos_active(CHAOS_KEY) is False

    def test_redis_client_is_created_once_and_reused(self):
        with patch.object(search_api, "_redis_client", None), patch(
            "app.api.search.redis_lib.Redis"
        ) as redis_cls:
            first = search_api._get_redis()
            second = search_api._get_redis()
        assert first is second
        redis_cls.assert_called_once()


class TestAnalyticsFailure:
    """The analytics endpoint's own error path."""

    def test_analytics_failure_returns_500(self, client):
        with patch.object(
            search_api, "get_search_analytics", side_effect=RuntimeError("boom")
        ):
            response = client.get("/api/v1/search/analytics")
        assert response.status_code == 500
        assert response.get_json() == {"error": "Failed to retrieve analytics"}


class TestIndexPayloadValidation:
    """Index endpoints reject empty and malformed bodies."""

    @pytest.mark.parametrize("path", ["/api/v1/search/index/document", "/api/v1/search/index/file"])
    def test_empty_json_object_is_rejected(self, client, path: str):
        response = client.post(path, json={})
        assert response.status_code == 400
        assert response.get_json() == {"error": "Request body is required"}

    @pytest.mark.parametrize("path", ["/api/v1/search/index/document", "/api/v1/search/index/file"])
    def test_json_null_literal_is_rejected(self, client, path: str):
        response = client.post(path, data="null", content_type="application/json")
        assert response.status_code == 400
        assert response.get_json() == {"error": "Request body is required"}

    @pytest.mark.parametrize("path", ["/api/v1/search/index/document", "/api/v1/search/index/file"])
    def test_unparsable_json_is_rejected(self, client, path: str):
        response = client.post(path, data="{oops", content_type="application/json")
        assert response.status_code == 400

    @pytest.mark.parametrize("path", ["/api/v1/search/index/document", "/api/v1/search/index/file"])
    def test_non_json_content_type_is_unsupported_media_type(self, client, path: str):
        response = client.post(path, data="id=doc-1", content_type="text/plain")
        assert response.status_code == 415
