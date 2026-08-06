"""WP-07: cross-user authorization for search, suggest and the index API.

Tenant scoping is a MeiliSearch ``owner_id`` filter built from the
gateway-injected ``X-User-ID``. The fake index evaluates that filter, so "user A
must not see user B's documents" is asserted on returned hits, not just on the
filter string.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.services import meilisearch_client
from tests.conftest_wp07 import (
    DOCUMENTS_INDEX,
    FILES_INDEX,
    build_app,
    build_config,
    last_search_params,
    seed_documents,
    seed_files,
)
from tests.fakes import FakeMeiliClient

USER_A = {"X-User-ID": "user-a"}
USER_B = {"X-User-ID": "user-b"}


@pytest.fixture()
def meili() -> FakeMeiliClient:
    client = FakeMeiliClient()
    seed_documents(
        client,
        {"id": "doc-a1", "title": "alpha merger memo", "owner_id": "user-a"},
        {"id": "doc-b1", "title": "alpha payroll memo", "owner_id": "user-b"},
    )
    seed_files(
        client,
        {"id": "file-a1", "name": "alpha-a.pdf", "owner_id": "user-a"},
        {"id": "file-b1", "name": "alpha-b.pdf", "owner_id": "user-b"},
    )
    return client


@pytest.fixture()
def client(meili: FakeMeiliClient):
    config = build_config(require_auth=True, service_token="svc-token")
    return build_app(meili, config).test_client()


@pytest.fixture()
def reset_analytics() -> Iterator[None]:
    """Snapshot/restore the module-level analytics store around a test."""
    with meilisearch_client._analytics_lock:
        saved = {
            "queries": list(meilisearch_client._search_analytics["queries"]),
            "total_searches": meilisearch_client._search_analytics["total_searches"],
            "total_results": meilisearch_client._search_analytics["total_results"],
        }
        meilisearch_client._search_analytics.update(
            {"queries": [], "total_searches": 0, "total_results": 0}
        )
    try:
        yield
    finally:
        with meilisearch_client._analytics_lock:
            meilisearch_client._search_analytics.update(saved)


class TestSearchScoping:
    def test_search_returns_only_the_callers_documents(self, client):
        body = client.get(
            "/api/v1/search/?q=alpha&type=document", headers=USER_A
        ).get_json()
        assert [hit["id"] for hit in body["results"]] == ["doc-a1"]
        assert body["total"] == 1

    def test_search_returns_only_the_callers_files(self, client):
        body = client.get("/api/v1/search/?q=alpha&type=file", headers=USER_B).get_json()
        assert [hit["id"] for hit in body["results"]] == ["file-b1"]

    def test_merged_search_never_crosses_the_owner_boundary(self, client):
        body = client.get("/api/v1/search/?q=alpha", headers=USER_A).get_json()
        owners = {hit["owner_id"] for hit in body["results"]}
        assert owners == {"user-a"}
        assert body["total"] == 2

    def test_owner_id_query_parameter_cannot_override_the_header(self, client, meili):
        """A caller cannot widen their scope with ?owner_id=."""
        client.get("/api/v1/search/?q=alpha&owner_id=user-b", headers=USER_A)
        assert last_search_params(meili, DOCUMENTS_INDEX)["filter"] == 'owner_id = "user-a"'

    def test_advanced_search_body_cannot_override_the_owner(self, client, meili):
        response = client.post(
            "/api/v1/search/advanced",
            json={"q": "alpha", "owner_id": "user-b", "type": "document"},
            headers=USER_A,
        )
        assert response.status_code == 200
        assert [hit["id"] for hit in response.get_json()["results"]] == ["doc-a1"]
        assert (
            last_search_params(meili, DOCUMENTS_INDEX)["filter"]
            == 'type = "document" AND owner_id = "user-a"'
        )

    def test_advanced_search_tag_filter_is_anded_with_the_owner_filter(
        self, client, meili
    ):
        client.post(
            "/api/v1/search/advanced",
            json={"q": "alpha", "type": "document", "tags": ["finance", "hr"]},
            headers=USER_A,
        )
        assert last_search_params(meili, DOCUMENTS_INDEX)["filter"] == (
            'type = "document" AND owner_id = "user-a" '
            'AND (tags = "finance" OR tags = "hr")'
        )

    def test_service_token_search_is_unscoped(self, client, meili):
        """FINDING 7: a service-token caller with no X-User-ID searches every
        tenant's data — no owner filter is applied at all. Internal callers are
        fully trusted, so anything holding SEARCH_SERVICE_TOKEN is a
        cross-tenant read primitive."""
        body = client.get(
            "/api/v1/search/?q=alpha&type=document",
            headers={"Authorization": "Bearer svc-token"},
        ).get_json()
        assert {hit["id"] for hit in body["results"]} == {"doc-a1", "doc-b1"}
        assert "owner_id" not in last_search_params(meili, DOCUMENTS_INDEX)["filter"]


class TestSuggestScoping:
    def test_suggest_leaks_other_users_titles(self, client):
        """FINDING 8: ``MeiliSearchService.suggest`` issues no owner filter.

        Autocomplete therefore returns document titles and file names owned by
        other tenants — a real cross-user disclosure, unlike ``search``.
        """
        body = client.get("/api/v1/search/suggest?q=alpha", headers=USER_A).get_json()
        assert "alpha payroll memo" in body["suggestions"]

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "FINDING 8: /api/v1/search/suggest is not scoped to X-User-ID, so "
            "another user's document titles are returned as suggestions."
        ),
    )
    def test_suggest_should_be_scoped_to_the_caller(self, client):
        body = client.get("/api/v1/search/suggest?q=alpha", headers=USER_A).get_json()
        assert body["suggestions"] == ["alpha merger memo", "alpha-a.pdf"]

    def test_suggest_honours_its_result_cap(self, client, meili):
        """Boundary: the suggest cap is 10 results across both indices."""
        seed_documents(
            meili,
            *[
                {"id": f"doc-x{i:02d}", "title": f"alpha {i:02d}", "owner_id": "user-a"}
                for i in range(15)
            ],
        )
        body = client.get("/api/v1/search/suggest?q=alpha", headers=USER_A).get_json()
        assert len(body["suggestions"]) == 10


class TestIndexApiAuthz:
    def test_a_user_can_delete_another_users_document_from_the_index(self, client, meili):
        """FINDING 9: DELETE /index/{type}/{id} has no ownership check.

        Any authenticated identity can evict any other tenant's document from
        the search index (a denial-of-search against that tenant's data).
        """
        response = client.delete("/api/v1/search/index/document/doc-a1", headers=USER_B)
        assert response.status_code == 200
        assert "doc-a1" not in meili.index(DOCUMENTS_INDEX).documents

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "FINDING 9: app/api/index.py's delete route never compares the "
            "document's owner_id with X-User-ID, so user-b can remove user-a's "
            "document from the index. Expected 403/404."
        ),
    )
    def test_deleting_another_users_document_should_be_forbidden(self, client):
        response = client.delete("/api/v1/search/index/document/doc-a1", headers=USER_B)
        assert response.status_code in (403, 404)

    def test_a_user_can_index_a_document_owned_by_someone_else(self, client, meili):
        """FINDING 10: the caller-supplied owner_id is written verbatim, so a
        user can inject documents into another tenant's search results."""
        response = client.post(
            "/api/v1/search/index/document",
            json={"id": "doc-forged", "title": "forged", "owner_id": "user-a"},
            headers=USER_B,
        )
        assert response.status_code == 201
        assert meili.index(DOCUMENTS_INDEX).documents["doc-forged"]["owner_id"] == "user-a"

        leaked = client.get(
            "/api/v1/search/?q=forged&type=document", headers=USER_A
        ).get_json()
        assert [hit["id"] for hit in leaked["results"]] == ["doc-forged"]

    def test_a_user_can_trigger_a_global_reindex(self, client):
        """Reindex is an admin operation but is reachable by any identity."""
        assert client.post("/api/v1/search/reindex", headers=USER_B).status_code == 200

    def test_deleting_a_document_owned_by_the_caller_succeeds(self, client, meili):
        response = client.delete("/api/v1/search/index/document/doc-a1", headers=USER_A)
        assert response.status_code == 200
        assert response.get_json()["status"] == "deleted"

    def test_deleting_a_file_only_touches_the_files_index(self, client, meili):
        client.delete("/api/v1/search/index/file/file-a1", headers=USER_A)
        assert "file-a1" not in meili.index(FILES_INDEX).documents
        assert "doc-a1" in meili.index(DOCUMENTS_INDEX).documents


class TestAnalyticsScoping:
    def test_analytics_exposes_other_users_query_strings(
        self, client, reset_analytics
    ):
        """FINDING 11: /analytics is a process-global store with no tenant
        scoping, so one user's raw query text is visible to every other user."""
        client.get("/api/v1/search/?q=confidential+merger+alpha", headers=USER_A)
        body = client.get("/api/v1/search/analytics", headers=USER_B).get_json()
        assert any(
            entry["query"] == "confidential merger alpha"
            for entry in body["popular_queries"]
        )

    def test_zero_result_queries_are_tracked(self, client, reset_analytics):
        client.get("/api/v1/search/?q=nothing-matches-this", headers=USER_A)
        body = client.get("/api/v1/search/analytics", headers=USER_A).get_json()
        assert body["zero_result_queries"][0]["query"] == "nothing-matches-this"
        assert body["total_searches"] == 1
        assert body["avg_results_per_query"] == 0.0
