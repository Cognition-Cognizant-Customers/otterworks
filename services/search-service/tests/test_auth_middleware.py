"""Tests for the authentication middleware (service-token and gateway paths)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.config import AppConfig, AuthConfig, MeiliSearchConfig, SQSConfig
from app.main import create_app

SERVICE_TOKEN = "test-service-token-123"


@pytest.fixture()
def auth_app(mock_meilisearch_client: MagicMock):
    """Create a Flask test app with auth enforced and a service token configured."""
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
    with patch("app.services.meilisearch_client.meilisearch.Client") as mock_cls:
        mock_cls.return_value = mock_meilisearch_client
        flask_app = create_app(config)
        flask_app.config["TESTING"] = True
        yield flask_app


@pytest.fixture()
def auth_client(auth_app):
    return auth_app.test_client()


class TestServiceTokenAuth:
    """The service-token path must accept only the exact configured token."""

    def test_correct_service_token_is_accepted(self, auth_client):
        response = auth_client.get(
            "/api/v1/search/?q=otters",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
        )
        assert response.status_code == 200

    def test_wrong_service_token_is_rejected(self, auth_client):
        response = auth_client.get(
            "/api/v1/search/?q=otters",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert response.status_code == 401
        assert response.get_json() == {"error": "unauthorized"}

    def test_empty_bearer_token_is_rejected(self, auth_client):
        response = auth_client.get(
            "/api/v1/search/?q=otters",
            headers={"Authorization": "Bearer "},
        )
        assert response.status_code == 401
        assert response.get_json() == {"error": "unauthorized"}


class TestGatewayIdentityAuth:
    """The gateway path requires an X-User-ID header when no valid token is given."""

    def test_gateway_user_id_is_accepted(self, auth_client):
        response = auth_client.get(
            "/api/v1/search/?q=otters",
            headers={"X-User-ID": "user-123"},
        )
        assert response.status_code == 200

    def test_missing_credentials_are_rejected(self, auth_client):
        response = auth_client.get("/api/v1/search/?q=otters")
        assert response.status_code == 401
        assert response.get_json() == {"error": "unauthorized"}
