"""WP-07: authentication middleware (app/middleware/auth.py).

The middleware accepts either a shared service token or a gateway-injected
``X-User-ID`` header; it performs no JWT verification of its own. These tests
pin both the accept and the reject paths, including the header-trust question.
"""

from __future__ import annotations

import pytest

from tests.conftest_wp07 import DOCUMENTS_INDEX, build_app, build_config
from tests.fakes import FakeMeiliClient

SERVICE_TOKEN = "svc-token-value"
# A structurally valid but long-expired JWT (exp 2018-01-18). Never signed by
# anything this service knows about — it is here to show the middleware does no
# JWT validation at all, it only string-compares the service token.
EXPIRED_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJ1c2VyLTEiLCJleHAiOjE1MTYyMzkwMjJ9."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)
SEARCH_PATH = "/api/v1/search/?q=test"


@pytest.fixture()
def meili() -> FakeMeiliClient:
    return FakeMeiliClient()


@pytest.fixture()
def client(meili: FakeMeiliClient):
    """Auth enforced, with a service token configured."""
    config = build_config(require_auth=True, service_token=SERVICE_TOKEN)
    return build_app(meili, config).test_client()


@pytest.fixture()
def tokenless_client(meili: FakeMeiliClient):
    """Auth enforced but no service token configured (local-dev shape)."""
    config = build_config(require_auth=True, service_token="")
    return build_app(meili, config).test_client()


class TestPublicEndpoints:
    @pytest.mark.parametrize("path", ["/health", "/health/ready", "/metrics"])
    def test_public_paths_need_no_credentials(self, client, path):
        assert client.get(path).status_code in (200, 503)

    def test_public_prefix_match_is_a_prefix_not_an_exact_path(self, client):
        """FINDING 5 (low): PUBLIC_PREFIXES is matched with str.startswith.

        Any future route beginning with ``/health`` or ``/metrics`` — e.g.
        ``/metrics-admin`` — would be unauthenticated by accident. Today no
        such route exists, so the request 404s (rather than 401s), which is
        what this test pins.
        """
        assert client.get("/healthz-not-a-route").status_code == 404
        assert client.get("/metrics-admin").status_code == 404


class TestMissingCredentials:
    def test_search_without_credentials_is_401(self, client):
        response = client.get(SEARCH_PATH)
        assert response.status_code == 401
        assert response.get_json() == {"error": "unauthorized"}

    def test_advanced_search_without_credentials_is_401(self, client):
        assert client.post("/api/v1/search/advanced", json={"q": "x"}).status_code == 401

    def test_indexing_without_credentials_is_401(self, client):
        response = client.post(
            "/api/v1/search/index/document", json={"id": "doc-1", "title": "T"}
        )
        assert response.status_code == 401

    def test_reindex_without_credentials_is_401(self, client):
        assert client.post("/api/v1/search/reindex").status_code == 401

    def test_delete_without_credentials_is_401(self, client):
        assert client.delete("/api/v1/search/index/document/doc-1").status_code == 401

    def test_unknown_route_is_401_before_it_is_404(self, client):
        """Auth runs before the 404, so route existence is not disclosed."""
        assert client.get("/api/v1/search/does-not-exist").status_code == 401

    def test_unauthorized_response_body_has_no_detail(self, client):
        body = client.get(SEARCH_PATH).get_data(as_text=True)
        assert "token" not in body.lower()
        assert SERVICE_TOKEN not in body


class TestServiceToken:
    def test_valid_bearer_token_is_accepted(self, client):
        response = client.get(
            SEARCH_PATH, headers={"Authorization": f"Bearer {SERVICE_TOKEN}"}
        )
        assert response.status_code == 200

    def test_bearer_scheme_is_case_insensitive(self, client):
        response = client.get(
            SEARCH_PATH, headers={"Authorization": f"bEaReR {SERVICE_TOKEN}"}
        )
        assert response.status_code == 200

    def test_surrounding_whitespace_in_the_token_is_stripped(self, client):
        response = client.get(
            SEARCH_PATH, headers={"Authorization": f"Bearer   {SERVICE_TOKEN}  "}
        )
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "header",
        [
            "",
            "Bearer",
            "Bearer ",
            f"Basic {SERVICE_TOKEN}",
            f"Token {SERVICE_TOKEN}",
            SERVICE_TOKEN,
            f"Bearer {SERVICE_TOKEN}x",
            f"Bearer {SERVICE_TOKEN[:-1]}",
            f"Bearer {SERVICE_TOKEN.upper()}",
            "Bearer null",
        ],
        ids=[
            "empty",
            "scheme-only",
            "scheme-and-space",
            "basic-scheme",
            "token-scheme",
            "no-scheme",
            "token-plus-one-char",
            "token-minus-one-char",
            "wrong-case-token",
            "literal-null",
        ],
    )
    def test_malformed_or_wrong_tokens_are_rejected(self, client, header):
        response = client.get(SEARCH_PATH, headers={"Authorization": header})
        assert response.status_code == 401

    def test_an_expired_jwt_is_rejected(self, client):
        """The middleware never validates JWTs; an expired one is just a
        token that does not match the configured service token."""
        response = client.get(SEARCH_PATH, headers={"Authorization": f"Bearer {EXPIRED_JWT}"})
        assert response.status_code == 401

    def test_a_service_token_bypasses_the_user_header_entirely(self, client, meili):
        """No X-User-ID + service token -> allowed, and the query is unscoped."""
        response = client.get(
            SEARCH_PATH, headers={"Authorization": f"Bearer {SERVICE_TOKEN}"}
        )
        assert response.status_code == 200
        _, params = meili.index(DOCUMENTS_INDEX).search_calls[-1]
        assert "filter" not in params

    def test_no_token_configured_means_no_token_is_accepted(self, tokenless_client):
        response = tokenless_client.get(
            SEARCH_PATH, headers={"Authorization": "Bearer anything"}
        )
        assert response.status_code == 401


class TestGatewayUserHeader:
    def test_a_client_supplied_user_header_is_trusted_without_any_proof(
        self, client, meili
    ):
        """FINDING 6: ``X-User-ID`` is accepted from any caller.

        The design assumes the API gateway strips and re-injects this header
        after validating the JWT. If search-service is ever reachable directly
        (in-cluster, port-forward, or a gateway that forwards the client's own
        header) any caller can assert any identity and read that user's index.
        This test documents the current, by-design-but-fragile behaviour.
        """
        response = client.get(SEARCH_PATH, headers={"X-User-ID": "someone-elses-id"})
        assert response.status_code == 200
        _, params = meili.index(DOCUMENTS_INDEX).search_calls[-1]
        assert params["filter"] == 'owner_id = "someone-elses-id"'

    @pytest.mark.parametrize(
        "value", ["", " ", "   ", "\t"], ids=["empty", "one-space", "spaces", "tab"]
    )
    def test_blank_user_header_is_rejected(self, client, value):
        response = client.get(SEARCH_PATH, headers={"X-User-ID": value})
        assert response.status_code == 401

    def test_user_header_is_enough_when_no_service_token_is_configured(
        self, tokenless_client
    ):
        response = tokenless_client.get(SEARCH_PATH, headers={"X-User-ID": "user-1"})
        assert response.status_code == 200

    def test_user_header_wins_when_the_token_is_wrong(self, client):
        """A bad token is not fatal if a user identity is present."""
        response = client.get(
            SEARCH_PATH,
            headers={"Authorization": "Bearer wrong", "X-User-ID": "user-1"},
        )
        assert response.status_code == 200

    def test_user_header_grants_access_to_internal_index_endpoints(self, client):
        """Any end user can drive the indexing API, not just internal callers."""
        response = client.post(
            "/api/v1/search/index/document",
            json={"id": "doc-1", "title": "T", "owner_id": "user-1"},
            headers={"X-User-ID": "user-1"},
        )
        assert response.status_code == 201


class TestAuthDisabled:
    def test_auth_can_be_disabled_wholesale(self, meili):
        config = build_config(require_auth=False, service_token=SERVICE_TOKEN)
        client = build_app(meili, config).test_client()
        assert client.get(SEARCH_PATH).status_code == 200
        assert client.post("/api/v1/search/reindex").status_code == 200
