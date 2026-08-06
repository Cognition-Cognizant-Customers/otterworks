"""Tests for ``app.middleware.auth`` — missing, malformed and forged credentials.

The middleware accepts either a configured service token
(``Authorization: Bearer <token>``) or a gateway-injected ``X-User-ID``
header, and exempts the public ``/health`` and ``/metrics`` prefixes.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.config import AppConfig, AuthConfig, MeiliSearchConfig, SQSConfig
from app.main import create_app
from app.middleware.auth import PUBLIC_PREFIXES

SERVICE_TOKEN = "s3rv1ce-t0ken"
PROTECTED_PATH = "/api/v1/search/?q=otter"


@pytest.fixture()
def make_client(mock_meilisearch_client: MagicMock):
    """Build a test client whose auth configuration the test chooses."""

    def _make(*, require_auth: bool = True, service_token: str = SERVICE_TOKEN):
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
            auth=AuthConfig(service_token=service_token, require_auth=require_auth),
        )
        with patch("app.services.meilisearch_client.meilisearch.Client") as mock_cls:
            mock_cls.return_value = mock_meilisearch_client
            flask_app = create_app(config)
        flask_app.config["TESTING"] = True
        return flask_app.test_client()

    return _make


class TestMissingCredentials:
    """No credentials at all."""

    def test_search_without_credentials_is_rejected(self, make_client):
        response = make_client().get(PROTECTED_PATH)
        assert response.status_code == 401
        assert response.get_json() == {"error": "unauthorized"}

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/v1/search/?q=otter"),
            ("get", "/api/v1/search/suggest?q=ot"),
            ("post", "/api/v1/search/advanced"),
            ("get", "/api/v1/search/analytics"),
            ("post", "/api/v1/search/index/document"),
            ("post", "/api/v1/search/index/file"),
            ("delete", "/api/v1/search/index/document/doc-1"),
            ("post", "/api/v1/search/reindex"),
        ],
    )
    def test_every_non_public_route_requires_credentials(self, make_client, method, path):
        client = make_client()
        response = getattr(client, method)(path)
        assert response.status_code == 401

    def test_unknown_path_is_rejected_before_routing(self, make_client):
        """Auth runs as a before_request hook, so 401 wins over 404."""
        response = make_client().get("/api/v1/search/does-not-exist")
        assert response.status_code == 401

    def test_auth_disabled_allows_anonymous_access(self, make_client):
        response = make_client(require_auth=False).get(PROTECTED_PATH)
        assert response.status_code == 200


class TestPublicPrefixes:
    """Health and metrics stay reachable for probes."""

    @pytest.mark.parametrize("path", ["/health", "/health/ready", "/metrics"])
    def test_public_paths_skip_auth(self, make_client, path):
        response = make_client().get(path)
        assert response.status_code in (200, 503)
        assert response.status_code != 401

    def test_public_prefixes_are_matched_as_prefixes_not_exact_paths(self, make_client):
        """``/health...`` is exempt by prefix, so a bogus sibling 404s instead of 401ing.

        No route is served under such a path, so this is not an exposure — it is
        pinned because tightening the match to exact paths would change it.
        """
        assert PUBLIC_PREFIXES == ("/health", "/metrics")
        response = make_client().get("/healthz-not-a-real-route")
        assert response.status_code == 404


class TestServiceToken:
    """``Authorization: Bearer <token>`` handling."""

    def test_valid_service_token_is_accepted(self, make_client):
        response = make_client().get(
            PROTECTED_PATH, headers={"Authorization": f"Bearer {SERVICE_TOKEN}"}
        )
        assert response.status_code == 200

    def test_surrounding_whitespace_in_token_is_stripped(self, make_client):
        response = make_client().get(
            PROTECTED_PATH, headers={"Authorization": f"Bearer   {SERVICE_TOKEN}  "}
        )
        assert response.status_code == 200

    @pytest.mark.parametrize("scheme", ["bearer", "BEARER", "BeArEr"])
    def test_bearer_scheme_is_case_insensitive(self, make_client, scheme):
        response = make_client().get(
            PROTECTED_PATH, headers={"Authorization": f"{scheme} {SERVICE_TOKEN}"}
        )
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "header",
        [
            f"Bearer {SERVICE_TOKEN}x",
            f"Bearer {SERVICE_TOKEN[:-1]}",
            "Bearer ",
            "Bearer",
            f"Bearer  {SERVICE_TOKEN} extra",
            f"Basic {SERVICE_TOKEN}",
            f"Token {SERVICE_TOKEN}",
            SERVICE_TOKEN,
            f"Bearer {SERVICE_TOKEN.upper()}",
            "Bearer null",
        ],
    )
    def test_malformed_or_forged_authorization_headers_are_rejected(self, make_client, header):
        response = make_client().get(PROTECTED_PATH, headers={"Authorization": header})
        assert response.status_code == 401

    def test_token_supplied_as_query_parameter_is_not_accepted(self, make_client):
        response = make_client().get(f"/api/v1/search/?q=otter&token={SERVICE_TOKEN}")
        assert response.status_code == 401

    def test_token_is_rejected_when_no_token_is_configured(self, make_client):
        """With no service token configured the bearer path is unavailable."""
        response = make_client(service_token="").get(
            PROTECTED_PATH, headers={"Authorization": f"Bearer {SERVICE_TOKEN}"}
        )
        assert response.status_code == 401

    def test_empty_configured_token_does_not_match_empty_header(self, make_client):
        response = make_client(service_token="").get(
            PROTECTED_PATH, headers={"Authorization": "Bearer "}
        )
        assert response.status_code == 401


class TestGatewayIdentity:
    """``X-User-ID`` is the gateway-injected identity path."""

    def test_x_user_id_is_accepted_without_a_token(self, make_client):
        response = make_client(service_token="").get(
            PROTECTED_PATH, headers={"X-User-ID": "user-1"}
        )
        assert response.status_code == 200

    def test_x_user_id_is_trusted_without_verification(self, make_client):
        """Any caller that can reach the service directly can assert an identity.

        The service has no way to distinguish a gateway-set header from a
        client-set one; isolation depends on the gateway stripping it. Pinned
        so that adding verification is a deliberate, visible change.
        """
        response = make_client().get(
            PROTECTED_PATH, headers={"X-User-ID": "somebody-elses-id"}
        )
        assert response.status_code == 200

    def test_forged_token_still_passes_when_a_user_id_is_present(self, make_client):
        """A bad token is not a hard failure — the gateway path is tried next."""
        response = make_client().get(
            PROTECTED_PATH,
            headers={"Authorization": "Bearer forged", "X-User-ID": "user-1"},
        )
        assert response.status_code == 200

    @pytest.mark.parametrize("value", ["", " ", "\t", "   \t  "])
    def test_blank_x_user_id_is_rejected(self, make_client, value):
        response = make_client().get(PROTECTED_PATH, headers={"X-User-ID": value})
        assert response.status_code == 401
