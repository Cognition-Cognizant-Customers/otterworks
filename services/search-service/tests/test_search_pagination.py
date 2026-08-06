"""Tests for pagination clamping and query edge cases on the search endpoints.

Both the GET (``/api/v1/search/``) and POST (``/api/v1/search/advanced``)
entry points clamp ``page`` to a minimum of 1 and ``size`` to the range
1..100. These tests pin that contract for both paths so the two
implementations cannot drift apart.
"""

from __future__ import annotations

import pytest

EMPTY_RESULT = {"estimatedTotalHits": 0, "hits": []}

SIZE_CLAMP_CASES = [
    (0, 1),
    (1, 1),
    (99, 99),
    (100, 100),
    (101, 100),
    (-5, 1),
]


@pytest.fixture()
def empty_search(mock_meilisearch_client):
    """Make every MeiliSearch query return zero hits."""
    mock_meilisearch_client.index.return_value.search.return_value = dict(EMPTY_RESULT)
    return mock_meilisearch_client


class TestSearchGetPagination:
    """Tests for pagination params on GET /api/v1/search/."""

    @pytest.mark.parametrize(("requested_size", "expected_size"), SIZE_CLAMP_CASES)
    def test_search_get_size_out_of_range_clamped_to_1_100(
        self, client, empty_search, requested_size: int, expected_size: int
    ):
        """size is clamped into the inclusive range 1..100."""
        response = client.get(f"/api/v1/search/?q=test&size={requested_size}")
        assert response.status_code == 200
        assert response.get_json()["page_size"] == expected_size

    @pytest.mark.parametrize(("requested_page", "expected_page"), [(0, 1), (-3, 1), (1, 1), (7, 7)])
    def test_search_get_page_below_one_clamped_to_one(
        self, client, empty_search, requested_page: int, expected_page: int
    ):
        """page is clamped to a minimum of 1."""
        response = client.get(f"/api/v1/search/?q=test&page={requested_page}")
        assert response.status_code == 200
        assert response.get_json()["page"] == expected_page

    def test_search_get_page_beyond_last_returns_200_with_empty_results(self, client, mock_meilisearch_client):
        """A page past the end of the result set is a 200 with no results."""
        mock_meilisearch_client.index.return_value.search.return_value = {
            "estimatedTotalHits": 3,
            "hits": [],
        }

        response = client.get("/api/v1/search/?q=test&page=99&size=10")
        assert response.status_code == 200
        data = response.get_json()
        assert data["results"] == []
        assert data["page"] == 99

    @pytest.mark.parametrize("page", ["abc", "1.5", "", "1e3"])
    def test_search_get_non_numeric_page_returns_400(self, client, empty_search, page: str):
        """A page value that is not an integer is rejected with 400."""
        response = client.get(f"/api/v1/search/?q=test&page={page}")
        assert response.status_code == 400
        assert "error" in response.get_json()

    @pytest.mark.parametrize("size", ["abc", "2.5", "", "ten"])
    def test_search_get_non_numeric_size_returns_400(self, client, empty_search, size: str):
        """A size value that is not an integer is rejected with 400."""
        response = client.get(f"/api/v1/search/?q=test&size={size}")
        assert response.status_code == 400
        assert "error" in response.get_json()


class TestAdvancedSearchPagination:
    """Tests for pagination params on POST /api/v1/search/advanced."""

    @pytest.mark.parametrize(("requested_size", "expected_size"), SIZE_CLAMP_CASES)
    def test_advanced_search_size_out_of_range_clamped_to_1_100(
        self, client, empty_search, requested_size: int, expected_size: int
    ):
        """size is clamped into the inclusive range 1..100, as on the GET path."""
        response = client.post("/api/v1/search/advanced", json={"q": "test", "size": requested_size})
        assert response.status_code == 200
        assert response.get_json()["page_size"] == expected_size

    @pytest.mark.parametrize(("requested_page", "expected_page"), [(0, 1), (-3, 1), (1, 1), (7, 7)])
    def test_advanced_search_page_below_one_clamped_to_one(
        self, client, empty_search, requested_page: int, expected_page: int
    ):
        """page is clamped to a minimum of 1, as on the GET path."""
        response = client.post("/api/v1/search/advanced", json={"q": "test", "page": requested_page})
        assert response.status_code == 200
        assert response.get_json()["page"] == expected_page

    def test_advanced_search_page_beyond_last_returns_200_with_empty_results(
        self, client, mock_meilisearch_client
    ):
        """A page past the end of the result set is a 200 with no results."""
        mock_meilisearch_client.index.return_value.search.return_value = {
            "estimatedTotalHits": 3,
            "hits": [],
        }

        response = client.post("/api/v1/search/advanced", json={"q": "test", "page": 99, "size": 10})
        assert response.status_code == 200
        data = response.get_json()
        assert data["results"] == []
        assert data["page"] == 99

    @pytest.mark.parametrize("page", ["abc", None, [1], "1.5"])
    def test_advanced_search_non_numeric_page_returns_400(self, client, empty_search, page):
        """A page value that is not an integer is rejected with 400."""
        response = client.post("/api/v1/search/advanced", json={"q": "test", "page": page})
        assert response.status_code == 400
        assert "error" in response.get_json()

    @pytest.mark.parametrize("size", ["abc", None, [1], "2.5"])
    def test_advanced_search_non_numeric_size_returns_400(self, client, empty_search, size):
        """A size value that is not an integer is rejected with 400."""
        response = client.post("/api/v1/search/advanced", json={"q": "test", "size": size})
        assert response.status_code == 400
        assert "error" in response.get_json()


class TestSearchQueryEdgeCases:
    """Tests for unusual values of the q parameter."""

    def test_search_get_empty_query_returns_400(self, client, empty_search):
        """An explicitly empty q is rejected with 400."""
        response = client.get("/api/v1/search/?q=")
        assert response.status_code == 400
        assert "error" in response.get_json()

    def test_search_get_missing_query_returns_400(self, client, empty_search):
        """A request without q at all is rejected with 400."""
        response = client.get("/api/v1/search/")
        assert response.status_code == 400
        assert "error" in response.get_json()

    def test_search_get_whitespace_only_query_returns_200(self, client, empty_search):
        """Whitespace-only q is treated as a real query, not as a missing one."""
        response = client.get("/api/v1/search/?q=%20%20%20")
        assert response.status_code == 200
        data = response.get_json()
        assert data["query"] == "   "
        assert data["results"] == []

    def test_search_get_very_long_query_returns_200(self, client, empty_search):
        """A very long q is accepted and echoed back unchanged."""
        long_query = "otter" * 400
        response = client.get(f"/api/v1/search/?q={long_query}")
        assert response.status_code == 200
        assert response.get_json()["query"] == long_query

    @pytest.mark.parametrize(
        "query",
        [
            "🦦 otter",
            "報告書",
            "otter 🦦 報告書",
        ],
    )
    def test_search_get_unicode_query_returns_200(self, client, empty_search, query: str):
        """Emoji and CJK queries round-trip without corruption."""
        response = client.get("/api/v1/search/", query_string={"q": query})
        assert response.status_code == 200
        assert response.get_json()["query"] == query

    @pytest.mark.parametrize(
        "query",
        [
            'title = "x"',
            "owner_id != 'user-1'",
            "a AND b OR c",
            "*",
            'quote " backslash \\ end',
        ],
    )
    def test_search_get_backend_syntax_in_query_returns_200(self, client, empty_search, query: str):
        """Characters meaningful to the search backend are treated as plain text."""
        response = client.get("/api/v1/search/", query_string={"q": query})
        assert response.status_code == 200
        assert response.get_json()["query"] == query

    def test_search_get_zero_results_returns_200_with_empty_list(self, client, empty_search):
        """No matches is a 200 with an empty results list, never a 404 or null."""
        response = client.get("/api/v1/search/?q=nothing-matches-this")
        assert response.status_code == 200
        data = response.get_json()
        assert data["results"] == []
        assert data["total"] == 0

    def test_advanced_search_zero_results_returns_200_with_empty_list(self, client, empty_search):
        """No matches on the advanced path is also a 200 with an empty results list."""
        response = client.post("/api/v1/search/advanced", json={"q": "nothing-matches-this"})
        assert response.status_code == 200
        data = response.get_json()
        assert data["results"] == []
        assert data["total"] == 0
