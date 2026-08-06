"""Query-string and pagination edge cases for the search API.

Covers empty/oversized queries, filter-injection-ish syntax, the
``size`` cap and ``page`` floor boundary trios, and the offset/limit
arithmetic behind multi-index pagination.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.meilisearch_client import MeiliSearchService
from tests.fakes import api_error

MAX_PAGE_SIZE = 100
MIN_SUGGEST_PREFIX = 2
# The service enforces no upper bound on `q`; 1024 is the length the trio below
# probes to prove that (nothing in the code changes behaviour at that point).
PROBE_QUERY_LENGTH = 1024


def empty_result() -> dict[str, object]:
    return {"estimatedTotalHits": 0, "hits": []}


def last_search_params(mock_client: MagicMock) -> dict[str, object]:
    """Return the params dict of the most recent ``index.search`` call."""
    return mock_client.index.return_value.search.call_args.args[1]


def last_search_query(mock_client: MagicMock) -> str:
    return mock_client.index.return_value.search.call_args.args[0]


@pytest.fixture()
def searching_client(client, mock_meilisearch_client: MagicMock):
    """Test client whose MeiliSearch mock returns an empty result set."""
    mock_meilisearch_client.index.return_value.search.return_value = empty_result()
    return client


class TestQueryLengthBoundaries:
    """Length 0, and the trio around the probe length."""

    def test_missing_query_is_rejected(self, searching_client):
        response = searching_client.get("/api/v1/search/")
        assert response.status_code == 400
        assert response.get_json()["error"] == "Query parameter 'q' is required"

    def test_empty_query_is_rejected(self, searching_client):
        response = searching_client.get("/api/v1/search/?q=")
        assert response.status_code == 400

    def test_whitespace_only_query_is_accepted(self, searching_client, mock_meilisearch_client):
        """A blank-but-non-empty query is not trimmed; it reaches MeiliSearch."""
        response = searching_client.get("/api/v1/search/?q=%20")
        assert response.status_code == 200
        assert last_search_query(mock_meilisearch_client) == " "

    def test_single_character_query_is_accepted(self, searching_client):
        assert searching_client.get("/api/v1/search/?q=o").status_code == 200

    @pytest.mark.parametrize(
        "length", [PROBE_QUERY_LENGTH - 1, PROBE_QUERY_LENGTH, PROBE_QUERY_LENGTH + 1]
    )
    def test_long_queries_are_not_capped(
        self, searching_client, mock_meilisearch_client, length: int
    ):
        """No maximum query length is enforced — the trio is pinned as-is.

        All three lengths behave identically and the query is forwarded
        verbatim, so an unbounded string reaches MeiliSearch.
        """
        query = "o" * length
        response = searching_client.get(f"/api/v1/search/?q={query}")
        assert response.status_code == 200
        assert last_search_query(mock_meilisearch_client) == query
        assert response.get_json()["query"] == query

    def test_very_long_query_is_echoed_in_the_response(self, searching_client):
        query = "otter " * 5000
        response = searching_client.post(
            "/api/v1/search/advanced", json={"q": query}
        )
        assert response.status_code == 200
        assert response.get_json()["query"] == query


class TestQueryInjection:
    """Filter syntax in user input must stay inside the query term."""

    @pytest.mark.parametrize(
        "query",
        [
            'owner_id = "someone-else"',
            'type = "file" OR type = "document"',
            "' OR 1=1 --",
            '" OR owner_id EXISTS',
            "*",
            "%00",
            "../../etc/passwd",
            "<script>alert(1)</script>",
            "{}[]()&|!^~:/\\",
            "otter\u0000null",
            "🦦 emoji query",
        ],
    )
    def test_injection_shaped_queries_stay_in_the_query_term(
        self, searching_client, mock_meilisearch_client, query: str
    ):
        response = searching_client.get(
            "/api/v1/search/",
            query_string={"q": query, "type": "document"},
            headers={"X-User-ID": "user-1"},
        )
        assert response.status_code == 200
        assert last_search_query(mock_meilisearch_client) == query
        assert last_search_params(mock_meilisearch_client)["filter"] == (
            'type = "document" AND owner_id = "user-1"'
        )

    def test_quotes_in_the_user_id_are_escaped_in_the_filter(
        self, searching_client, mock_meilisearch_client
    ):
        """A crafted X-User-ID must not break out of the owner_id filter."""
        response = searching_client.get(
            "/api/v1/search/?q=otter&type=document",
            headers={"X-User-ID": 'user-1" OR owner_id = "user-2'},
        )
        assert response.status_code == 200
        assert last_search_params(mock_meilisearch_client)["filter"] == (
            'type = "document" AND owner_id = "user-1\\" OR owner_id = \\"user-2"'
        )

    def test_unknown_type_filter_searches_both_indices(
        self, searching_client, mock_meilisearch_client
    ):
        """An unrecognised ``type`` is still passed to the filter verbatim."""
        response = searching_client.get('/api/v1/search/?q=otter&type=folder')
        assert response.status_code == 200
        assert mock_meilisearch_client.index.return_value.search.call_count == 2
        assert last_search_params(mock_meilisearch_client)["filter"] == 'type = "folder"'

    @pytest.mark.parametrize(
        ("raw", "escaped"),
        [
            ('a"b', 'a\\"b'),
            ("a\\b", "a\\\\b"),
            ('\\"', '\\\\\\"'),
            ("plain", "plain"),
            ("", ""),
        ],
    )
    def test_escape_handles_quotes_and_backslashes(self, raw: str, escaped: str):
        assert MeiliSearchService._escape(raw) == escaped

    def test_invalid_filter_from_meilisearch_becomes_a_400(
        self, searching_client, mock_meilisearch_client
    ):
        mock_meilisearch_client.index.return_value.search.side_effect = api_error(
            "Attribute `type` is not filterable."
        )
        response = searching_client.get("/api/v1/search/?q=otter")
        assert response.status_code == 400
        assert "Invalid search filter" in response.get_json()["error"]


class TestPaginationBounds:
    """``page`` floor and ``size`` cap, as boundary trios."""

    @pytest.mark.parametrize(
        ("requested", "effective"),
        [("-1", 1), ("0", 1), ("1", 1), ("2", 2), ("1000000", 1000000)],
    )
    def test_page_is_floored_at_one(self, searching_client, requested: str, effective: int):
        response = searching_client.get(f"/api/v1/search/?q=otter&page={requested}")
        assert response.status_code == 200
        assert response.get_json()["page"] == effective

    @pytest.mark.parametrize(
        ("requested", "effective"),
        [
            ("0", 1),
            ("1", 1),
            ("2", 2),
            (str(MAX_PAGE_SIZE - 1), MAX_PAGE_SIZE - 1),
            (str(MAX_PAGE_SIZE), MAX_PAGE_SIZE),
            (str(MAX_PAGE_SIZE + 1), MAX_PAGE_SIZE),
            ("100000", MAX_PAGE_SIZE),
        ],
    )
    def test_size_is_clamped_to_the_cap(self, searching_client, requested: str, effective: int):
        response = searching_client.get(f"/api/v1/search/?q=otter&size={requested}")
        assert response.status_code == 200
        assert response.get_json()["page_size"] == effective

    @pytest.mark.parametrize("value", ["abc", "1.5", "", "1e3", "null", "2,3", " "])
    def test_non_integer_pagination_parameters_are_rejected(self, searching_client, value: str):
        assert searching_client.get(f"/api/v1/search/?q=otter&page={value}").status_code == 400
        assert searching_client.get(f"/api/v1/search/?q=otter&size={value}").status_code == 400

    def test_non_ascii_digits_are_accepted_as_numbers(self, searching_client):
        """``int()`` parses Unicode decimal digits, so ``page=٣`` means page 3."""
        response = searching_client.get("/api/v1/search/?q=otter&page=\u0663")
        assert response.status_code == 200
        assert response.get_json()["page"] == 3

    def test_single_index_pagination_uses_offset_and_limit(
        self, searching_client, mock_meilisearch_client
    ):
        searching_client.get("/api/v1/search/?q=otter&type=document&page=3&size=10")
        params = last_search_params(mock_meilisearch_client)
        assert params["offset"] == 20
        assert params["limit"] == 10

    def test_multi_index_pagination_over_fetches_from_offset_zero(
        self, searching_client, mock_meilisearch_client
    ):
        """Both indices are searched, so the service fetches page*size from each."""
        searching_client.get("/api/v1/search/?q=otter&page=3&size=10")
        params = last_search_params(mock_meilisearch_client)
        assert params["offset"] == 0
        assert params["limit"] == 30

    def test_page_beyond_the_last_page_returns_no_results(
        self, searching_client, mock_meilisearch_client
    ):
        mock_meilisearch_client.index.return_value.search.return_value = {
            "estimatedTotalHits": 1,
            "hits": [{"id": "doc-1", "title": "Otter", "type": "document"}],
        }
        response = searching_client.get("/api/v1/search/?q=otter&page=9&size=20")
        body = response.get_json()
        assert response.status_code == 200
        assert body["results"] == []
        assert body["page"] == 9
        assert body["total"] == 2

    def test_multi_index_paging_can_starve_the_second_index(
        self, searching_client, mock_meilisearch_client
    ):
        """Hits are concatenated per index, not merged by relevance.

        With both indices full, page 2 is served entirely from the documents
        index and the files index's hits are unreachable. Pinned as current
        behaviour, not asserted to be correct.
        """
        documents = [
            {"id": f"doc-{i}", "title": f"Otter {i}", "type": "document"} for i in range(20)
        ]
        mock_meilisearch_client.index.return_value.search.return_value = {
            "estimatedTotalHits": 20,
            "hits": documents,
        }
        response = searching_client.get("/api/v1/search/?q=otter&page=2&size=10")
        ids = [hit["id"] for hit in response.get_json()["results"]]
        assert ids == [f"doc-{i}" for i in range(10, 20)]

    @pytest.mark.parametrize(
        ("payload", "expected_page", "expected_size"),
        [
            ({"page": 0}, 1, 20),
            ({"page": -5}, 1, 20),
            ({"size": 0}, 1, 1),
            ({"size": MAX_PAGE_SIZE - 1}, 1, MAX_PAGE_SIZE - 1),
            ({"size": MAX_PAGE_SIZE}, 1, MAX_PAGE_SIZE),
            ({"size": MAX_PAGE_SIZE + 1}, 1, MAX_PAGE_SIZE),
            ({"page": "4", "size": "15"}, 4, 15),
        ],
    )
    def test_advanced_search_applies_the_same_bounds(
        self, searching_client, payload: dict, expected_page: int, expected_size: int
    ):
        response = searching_client.post("/api/v1/search/advanced", json=payload)
        assert response.status_code == 200
        body = response.get_json()
        assert body["page"] == expected_page
        assert body["page_size"] == expected_size

    @pytest.mark.parametrize("payload", [{"page": "x"}, {"size": "x"}, {"page": None}])
    def test_advanced_search_rejects_non_integer_bounds(self, searching_client, payload: dict):
        response = searching_client.post("/api/v1/search/advanced", json=payload)
        assert response.status_code == 400

    def test_advanced_search_without_a_query_matches_everything(
        self, searching_client, mock_meilisearch_client
    ):
        response = searching_client.post("/api/v1/search/advanced", json={"tags": ["a", "b"]})
        assert response.status_code == 200
        assert response.get_json()["query"] == "*"
        assert last_search_params(mock_meilisearch_client)["filter"] == (
            '(tags = "a" OR tags = "b")'
        )


class TestAdvancedSearchFilters:
    """Filter assembly and hit parsing for POST /advanced."""

    def test_owner_scope_comes_from_the_gateway_header_not_the_body(
        self, searching_client, mock_meilisearch_client
    ):
        """An ``owner_id`` in the body is ignored, so a caller cannot widen scope."""
        response = searching_client.post(
            "/api/v1/search/advanced",
            json={"q": "otter", "type": "document", "owner_id": "user-2"},
            headers={"X-User-ID": "user-1"},
        )
        assert response.status_code == 200
        assert last_search_params(mock_meilisearch_client)["filter"] == (
            'type = "document" AND owner_id = "user-1"'
        )

    def test_date_range_filters_are_escaped_and_combined(
        self, searching_client, mock_meilisearch_client
    ):
        searching_client.post(
            "/api/v1/search/advanced",
            json={
                "q": "otter",
                "type": "document",
                "date_from": '2026-01-01" OR true',
                "date_to": "2026-12-31",
            },
        )
        assert last_search_params(mock_meilisearch_client)["filter"] == (
            'type = "document" AND created_at >= "2026-01-01\\" OR true"'
            ' AND created_at <= "2026-12-31"'
        )

    def test_no_filter_is_sent_when_nothing_narrows_the_search(
        self, searching_client, mock_meilisearch_client
    ):
        searching_client.post("/api/v1/search/advanced", json={"q": "otter"})
        assert "filter" not in last_search_params(mock_meilisearch_client)

    def test_file_hits_are_parsed_with_file_specific_fields(
        self, searching_client, mock_meilisearch_client
    ):
        mock_meilisearch_client.index.return_value.search.return_value = {
            "estimatedTotalHits": 1,
            "hits": [
                {
                    "id": "file-1",
                    "name": "otter.png",
                    "type": "file",
                    "owner_id": "user-1",
                    "mime_type": "image/png",
                    "folder_id": "folder-1",
                    "size": 2048,
                    "_formatted": {"name": "<em>otter</em>.png"},
                }
            ],
        }
        response = searching_client.post(
            "/api/v1/search/advanced", json={"q": "otter", "type": "file"}
        )
        hit = response.get_json()["results"][0]
        assert hit["title"] == "otter.png"
        assert hit["content_snippet"] == ""
        assert hit["mime_type"] == "image/png"
        assert hit["size"] == 2048
        assert hit["highlights"] == {"name": ["<em>otter</em>.png"]}

    def test_unhighlighted_fields_are_not_reported_as_highlights(
        self, searching_client, mock_meilisearch_client
    ):
        mock_meilisearch_client.index.return_value.search.return_value = {
            "estimatedTotalHits": 1,
            "hits": [
                {
                    "id": "doc-1",
                    "title": "Otter report",
                    "type": "document",
                    "_formatted": {"title": "Otter report", "content": "plain text"},
                }
            ],
        }
        response = searching_client.post(
            "/api/v1/search/advanced", json={"q": "otter", "type": "document"}
        )
        assert response.get_json()["results"][0]["highlights"] == {}


class TestSuggestPrefixBoundaries:
    """``suggest`` requires a prefix of at least two characters."""

    @pytest.mark.parametrize(
        ("prefix", "expects_lookup"),
        [
            ("", False),
            ("o", False),
            ("ot", True),
            ("ott", True),
        ],
    )
    def test_minimum_prefix_length_trio(
        self, searching_client, mock_meilisearch_client, prefix: str, expects_lookup: bool
    ):
        assert MIN_SUGGEST_PREFIX == 2
        response = searching_client.get(f"/api/v1/search/suggest?q={prefix}")
        assert response.status_code == 200
        assert response.get_json()["query"] == prefix
        assert mock_meilisearch_client.index.return_value.search.called is expects_lookup

    def test_suggestions_are_deduplicated_across_indices(
        self, searching_client, mock_meilisearch_client
    ):
        mock_meilisearch_client.index.return_value.search.return_value = {
            "estimatedTotalHits": 3,
            "hits": [{"title": "Otter notes"}, {"title": "Otter notes"}, {"name": "otter.png"}],
        }
        response = searching_client.get("/api/v1/search/suggest?q=ot")
        assert response.get_json()["suggestions"] == ["Otter notes", "otter.png"]

    def test_suggestion_count_is_capped_at_the_default_size(
        self, searching_client, mock_meilisearch_client
    ):
        mock_meilisearch_client.index.return_value.search.return_value = {
            "estimatedTotalHits": 25,
            "hits": [{"title": f"Otter {i}"} for i in range(25)],
        }
        response = searching_client.get("/api/v1/search/suggest?q=ot")
        assert len(response.get_json()["suggestions"]) == 10

    def test_hits_without_a_title_or_name_are_skipped(
        self, searching_client, mock_meilisearch_client
    ):
        mock_meilisearch_client.index.return_value.search.return_value = {
            "estimatedTotalHits": 2,
            "hits": [{"id": "x"}, {"title": ""}, {"name": "otter.png"}],
        }
        response = searching_client.get("/api/v1/search/suggest?q=ot")
        assert response.get_json()["suggestions"] == ["otter.png"]
