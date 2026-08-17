"""Tests for the properties that make CSRF tokens unnecessary here."""

from __future__ import annotations


class TestCsrfSafety:
    """The API must stay header-authenticated and cookie-free."""

    def test_form_encoded_post_is_rejected(self, client):
        """A cross-site-submittable content type cannot reach an endpoint."""
        response = client.post(
            "/api/v1/search/index/document",
            data={"id": "doc-1", "title": "Doc"},
            content_type="application/x-www-form-urlencoded",
        )

        assert response.status_code == 415

    def test_json_post_is_accepted(self, client, mock_meilisearch_client):
        """JSON callers are unaffected by the content-type guard."""
        response = client.post(
            "/api/v1/search/index/document",
            json={"id": "doc-1", "title": "Doc", "ownerId": "user-1"},
        )

        assert response.status_code == 201

    def test_no_session_cookie_is_issued(self, client):
        """Sessions are disabled, so no ambient cookie authority exists."""
        response = client.get("/health")

        assert response.status_code == 200
        assert "Set-Cookie" not in response.headers

    def test_cors_does_not_allow_credentials(self, client):
        """Browsers must not be able to send credentials cross-origin."""
        response = client.get(
            "/api/v1/search/?q=test", headers={"Origin": "http://localhost:3000"}
        )

        assert response.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"
        assert "Access-Control-Allow-Credentials" not in response.headers
