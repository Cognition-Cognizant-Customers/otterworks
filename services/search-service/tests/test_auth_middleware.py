"""Tests for the authentication middleware.

The middleware accepts either a configured service token presented as
``Authorization: Bearer <token>`` or an ``X-User-ID`` header injected by the
API gateway. Health and metrics stay public.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.config import AppConfig, AuthConfig, MeiliSearchConfig, SQSConfig
from app.main import create_app

SERVICE_TOKEN = "test-service-token"


@pytest.fixture()
def authed_client(mock_meilisearch_client: MagicMock):
    """Flask test client for an app with authentication enforced."""
    config = AppConfig(
        service_name="search-service-test",
        port=8087,
        debug=True,
        log_level="DEBUG",
        meilisearch=MeiliSearchConfig(
            url="http://localhost:7700",
            api_key="",
            documents_index="test-otterworks-documents",
            files_index="test-otterworks-files",
        ),
        sqs=SQSConfig(enabled=False),
        auth=AuthConfig(service_token=SERVICE_TOKEN, require_auth=True),
    )
    mock_meilisearch_client.index.return_value.search.return_value = {
        "estimatedTotalHits": 0,
        "hits": [],
    }
    with patch("app.services.meilisearch_client.meilisearch.Client") as mock_cls:
        mock_cls.return_value = mock_meilisearch_client
        flask_app = create_app(config)
        flask_app.config["TESTING"] = True
        yield flask_app.test_client()


class TestAuthRejection:
    """Requests without acceptable credentials are rejected."""

    def test_search_without_any_credentials_returns_401(self, authed_client):
        """No Authorization header and no X-User-ID is unauthorized."""
        response = authed_client.get("/api/v1/search/?q=test")
        assert response.status_code == 401
        assert response.get_json() == {"error": "unauthorized"}

    @pytest.mark.parametrize(
        "header_value",
        [
            "",
            "Bearer",
            "Bearer ",
            SERVICE_TOKEN,
            f"Basic {SERVICE_TOKEN}",
            f"Token {SERVICE_TOKEN}",
            f"Bearer{SERVICE_TOKEN}",
        ],
    )
    def test_search_with_malformed_bearer_header_returns_401(self, authed_client, header_value: str):
        """An Authorization header that is not a well-formed Bearer token is unauthorized."""
        response = authed_client.get(
            "/api/v1/search/?q=test", headers={"Authorization": header_value}
        )
        assert response.status_code == 401
        assert response.get_json() == {"error": "unauthorized"}

    @pytest.mark.parametrize(
        "token",
        [
            "forged-token",
            f"{SERVICE_TOKEN}-expired",
            SERVICE_TOKEN.upper(),
            "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjF9.not-a-real-signature",
        ],
    )
    def test_search_with_forged_token_returns_401(self, authed_client, token: str):
        """A bearer token that does not match the configured service token is unauthorized."""
        response = authed_client.get(
            "/api/v1/search/?q=test", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401
        assert response.get_json() == {"error": "unauthorized"}

    def test_search_with_blank_user_id_header_returns_401(self, authed_client):
        """A whitespace-only X-User-ID does not count as a gateway identity."""
        response = authed_client.get("/api/v1/search/?q=test", headers={"X-User-ID": "   "})
        assert response.status_code == 401

    def test_index_endpoint_without_credentials_returns_401(self, authed_client):
        """Auth is enforced on the indexing endpoints too, not just search."""
        response = authed_client.post("/api/v1/search/index/document", json={"id": "doc-1"})
        assert response.status_code == 401


class TestAuthAcceptance:
    """Requests with acceptable credentials pass the middleware."""

    def test_search_with_valid_service_token_returns_200(self, authed_client):
        """The configured service token is accepted."""
        response = authed_client.get(
            "/api/v1/search/?q=test", headers={"Authorization": f"Bearer {SERVICE_TOKEN}"}
        )
        assert response.status_code == 200

    def test_search_with_lowercase_bearer_scheme_returns_200(self, authed_client):
        """The Bearer scheme is matched case-insensitively, per RFC 7235."""
        response = authed_client.get(
            "/api/v1/search/?q=test", headers={"Authorization": f"bearer {SERVICE_TOKEN}"}
        )
        assert response.status_code == 200

    def test_search_with_gateway_user_id_returns_200(self, authed_client):
        """An X-User-ID injected by the gateway is accepted without a token."""
        response = authed_client.get("/api/v1/search/?q=test", headers={"X-User-ID": "user-1"})
        assert response.status_code == 200

    def test_missing_query_still_returns_400_when_authenticated(self, authed_client):
        """Authentication runs before validation, so an authed bad request is a 400."""
        response = authed_client.get("/api/v1/search/", headers={"X-User-ID": "user-1"})
        assert response.status_code == 400


class TestAuthPublicPaths:
    """Health and metrics are exempt from authentication."""

    @pytest.mark.parametrize("path", ["/health", "/health/ready", "/metrics"])
    def test_public_path_without_credentials_is_not_401(self, authed_client, path: str):
        """Public paths never return 401 even with auth enabled."""
        response = authed_client.get(path)
        assert response.status_code != 401
