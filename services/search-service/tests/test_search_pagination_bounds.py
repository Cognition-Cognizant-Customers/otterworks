"""WP-07: pagination boundaries for the search endpoints.

The public API is page/size; MeiliSearch is driven with offset/limit derived
from them. Both layers are asserted so a clamp that is applied to the response
but not to the upstream call cannot pass.
"""

from __future__ import annotations

import pytest

from tests.conftest_wp07 import (
    DOCUMENTS_INDEX,
    build_app,
    last_search_params,
    seed_documents,
    seed_files,
)
from tests.fakes import FakeMeiliClient

MAX_PAGE_SIZE = 100
MIN_PAGE_SIZE = 1


@pytest.fixture()
def meili() -> FakeMeiliClient:
    return FakeMeiliClient()


@pytest.fixture()
def client(meili: FakeMeiliClient):
    return build_app(meili).test_client()


class TestPageBounds:
    @pytest.mark.parametrize(
        ("page", "expected_page"),
        [("-1", 1), ("0", 1), ("1", 1), ("2", 2)],
        ids=["negative", "zero", "min", "min+1"],
    )
    def test_page_is_clamped_to_one(self, client, meili, page, expected_page):
        response = client.get(
            "/api/v1/search/", query_string={"q": "x", "type": "document", "page": page}
        )
        assert response.status_code == 200
        assert response.get_json()["page"] == expected_page

    def test_offset_is_never_negative(self, client, meili):
        """page=-1 must not produce a negative MeiliSearch offset."""
        client.get(
            "/api/v1/search/", query_string={"q": "x", "type": "document", "page": "-1"}
        )
        assert last_search_params(meili, DOCUMENTS_INDEX)["offset"] == 0

    def test_offset_follows_page_for_single_index_search(self, client, meili):
        client.get(
            "/api/v1/search/",
            query_string={"q": "x", "type": "document", "page": "3", "size": "10"},
        )
        params = last_search_params(meili, DOCUMENTS_INDEX)
        assert (params["offset"], params["limit"]) == (20, 10)

    def test_multi_index_search_fetches_from_offset_zero(self, client, meili):
        """Without a type filter both indices are merged, so offset must be 0."""
        client.get("/api/v1/search/", query_string={"q": "x", "page": "3", "size": "10"})
        params = last_search_params(meili, DOCUMENTS_INDEX)
        assert (params["offset"], params["limit"]) == (0, 30)

    def test_page_beyond_the_end_returns_an_empty_window(self, client, meili):
        seed_documents(
            meili, {"id": "doc-1", "title": "alpha report", "owner_id": "user-1"}
        )
        response = client.get(
            "/api/v1/search/",
            query_string={"q": "alpha", "type": "document", "page": "99", "size": "10"},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["results"] == []
        assert body["total"] == 1

    def test_non_numeric_page_is_rejected(self, client):
        response = client.get("/api/v1/search/", query_string={"q": "x", "page": "1.5"})
        assert response.status_code == 400


class TestPageSizeBounds:
    @pytest.mark.parametrize(
        ("size", "expected"),
        [
            ("-1", MIN_PAGE_SIZE),
            ("0", MIN_PAGE_SIZE),
            ("1", MIN_PAGE_SIZE),
            (str(MAX_PAGE_SIZE - 1), MAX_PAGE_SIZE - 1),
            (str(MAX_PAGE_SIZE), MAX_PAGE_SIZE),
            (str(MAX_PAGE_SIZE + 1), MAX_PAGE_SIZE),
            ("100000", MAX_PAGE_SIZE),
        ],
        ids=["negative", "min-1", "min", "max-1", "max", "max+1", "huge"],
    )
    def test_size_is_clamped_to_the_allowed_window(self, client, meili, size, expected):
        response = client.get(
            "/api/v1/search/", query_string={"q": "x", "type": "document", "size": size}
        )
        assert response.status_code == 200
        assert response.get_json()["page_size"] == expected
        assert last_search_params(meili, DOCUMENTS_INDEX)["limit"] == expected

    def test_non_numeric_size_is_rejected(self, client):
        response = client.get(
            "/api/v1/search/", query_string={"q": "x", "size": "twenty"}
        )
        assert response.status_code == 400
        assert response.get_json()["error"]

    def test_empty_size_is_rejected(self, client):
        response = client.get("/api/v1/search/", query_string={"q": "x", "size": ""})
        assert response.status_code == 400


class TestMergedPaginationWindow:
    """Merged document+file paging must not repeat or drop hits."""

    def _seed(self, meili: FakeMeiliClient) -> None:
        seed_documents(
            meili,
            *[
                {"id": f"doc-{i:02d}", "title": "alpha doc", "owner_id": "user-1"}
                for i in range(6)
            ],
        )
        seed_files(
            meili,
            *[
                {"id": f"file-{i:02d}", "name": "alpha file", "owner_id": "user-1"}
                for i in range(6)
            ],
        )

    def test_first_and_second_page_do_not_overlap(self, client, meili):
        self._seed(meili)
        first = client.get(
            "/api/v1/search/",
            query_string={"q": "alpha", "page": "1", "size": "5"},
            headers={"X-User-ID": "user-1"},
        ).get_json()
        second = client.get(
            "/api/v1/search/",
            query_string={"q": "alpha", "page": "2", "size": "5"},
            headers={"X-User-ID": "user-1"},
        ).get_json()

        first_ids = [hit["id"] for hit in first["results"]]
        second_ids = [hit["id"] for hit in second["results"]]
        assert len(first_ids) == 5
        assert not set(first_ids) & set(second_ids)
        assert first["total"] == second["total"] == 12


class TestAdvancedSearchBounds:
    @pytest.mark.parametrize(
        ("size", "expected"),
        [(0, MIN_PAGE_SIZE), (1, 1), (100, 100), (101, MAX_PAGE_SIZE)],
        ids=["min-1", "min", "max", "max+1"],
    )
    def test_advanced_search_clamps_size(self, client, size, expected):
        response = client.post(
            "/api/v1/search/advanced", json={"q": "x", "size": size, "type": "document"}
        )
        assert response.status_code == 200
        assert response.get_json()["page_size"] == expected

    @pytest.mark.parametrize("page", [-3, 0, 1])
    def test_advanced_search_clamps_page_to_one(self, client, page):
        response = client.post("/api/v1/search/advanced", json={"q": "x", "page": page})
        assert response.status_code == 200
        assert response.get_json()["page"] == 1

    def test_advanced_search_rejects_non_numeric_page(self, client):
        response = client.post(
            "/api/v1/search/advanced", json={"q": "x", "page": "later"}
        )
        assert response.status_code == 400
