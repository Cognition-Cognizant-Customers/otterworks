"""WP-07: query-string edge cases for GET /api/v1/search/.

Covers empty/whitespace queries, length boundaries, MeiliSearch filter-syntax
injection attempts, unicode/emoji, and stopword-only queries.
"""

from __future__ import annotations

import pytest

from tests.conftest_wp07 import (
    DOCUMENTS_INDEX,
    FILES_INDEX,
    build_app,
    last_search_params,
    seed_documents,
)
from tests.fakes import FakeMeiliClient

# The service applies no maximum query length of its own; this is the length
# at which we assert behaviour is still well-defined (see also
# ``test_over_long_query_is_rejected`` which documents the missing cap).
QUERY_LENGTH_PROBE = 1000


@pytest.fixture()
def meili() -> FakeMeiliClient:
    return FakeMeiliClient()


@pytest.fixture()
def client(meili: FakeMeiliClient):
    return build_app(meili).test_client()


class TestEmptyAndBlankQueries:
    def test_missing_q_is_rejected(self, client):
        """No ``q`` at all -> 400 with an error payload."""
        response = client.get("/api/v1/search/")
        assert response.status_code == 400
        assert response.get_json()["error"]

    def test_zero_length_q_is_rejected(self, client):
        """``q=`` (present but empty) -> 400."""
        response = client.get("/api/v1/search/?q=")
        assert response.status_code == 400
        assert response.get_json()["error"]

    def test_whitespace_only_query_is_accepted_and_forwarded_verbatim(
        self, client, meili
    ):
        """A whitespace-only query is truthy, so it reaches MeiliSearch as-is.

        Documented behaviour, not an assertion that it is desirable: the
        handler's guard is ``if not query``, which only catches the empty
        string.
        """
        response = client.get("/api/v1/search/?q=%20%20%20")
        assert response.status_code == 200
        assert response.get_json()["query"] == "   "
        assert meili.index(DOCUMENTS_INDEX).search_calls[-1][0] == "   "

    def test_tab_and_newline_only_query_is_accepted(self, client):
        """Other whitespace forms behave the same as spaces."""
        response = client.get("/api/v1/search/?q=%09%0A")
        assert response.status_code == 200
        assert response.get_json()["query"] == "\t\n"


class TestQueryLengthBoundaries:
    @pytest.mark.parametrize(
        "length",
        [QUERY_LENGTH_PROBE - 1, QUERY_LENGTH_PROBE, QUERY_LENGTH_PROBE + 1],
        ids=["max-1", "max", "max+1"],
    )
    def test_long_query_trio_is_forwarded_unchanged(self, client, meili, length):
        """max-1 / max / max+1: all three are accepted and passed through."""
        query = "a" * length
        response = client.get(f"/api/v1/search/?q={query}")
        assert response.status_code == 200
        assert response.get_json()["query"] == query
        assert len(meili.index(DOCUMENTS_INDEX).search_calls[-1][0]) == length

    def test_single_character_query_is_accepted(self, client):
        """Lower boundary: one character is the shortest non-empty query."""
        response = client.get("/api/v1/search/?q=a")
        assert response.status_code == 200
        assert response.get_json()["query"] == "a"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "FINDING (hardening gap): app/api/search.py enforces no maximum "
            "query length, so an arbitrarily large 'q' is forwarded to "
            "MeiliSearch. Expected a 400/413 above a sane cap."
        ),
    )
    def test_over_long_query_is_rejected(self, client):
        response = client.get("/api/v1/search/?q=" + "a" * 100_000)
        assert response.status_code in (400, 413)


class TestFilterSyntaxInjection:
    """MeiliSearch filter syntax in user input must not reach the filter."""

    @pytest.mark.parametrize(
        "query",
        [
            'owner_id = "user-2"',
            '" OR owner_id = "user-2',
            "type = 'document' AND owner_id != 'user-1'",
            "*",
            "a AND b OR c",
            "NOT secret",
            '\\"escaped\\"',
        ],
    )
    def test_injection_shaped_query_stays_in_the_q_argument(
        self, client, meili, query
    ):
        """Filter-ish text is searched for literally; it never edits ``filter``."""
        response = client.get(
            "/api/v1/search/", query_string={"q": query}, headers={"X-User-ID": "user-1"}
        )
        assert response.status_code == 200
        term, params = meili.index(DOCUMENTS_INDEX).search_calls[-1]
        assert term == query
        assert params["filter"] == 'owner_id = "user-1"'

    def test_quotes_in_user_id_header_are_escaped_in_the_filter(self, client, meili):
        """A crafted X-User-ID cannot break out of the owner_id clause."""
        response = client.get(
            "/api/v1/search/?q=test",
            headers={"X-User-ID": 'user-1" OR owner_id = "user-2'},
        )
        assert response.status_code == 200
        filter_expr = last_search_params(meili, DOCUMENTS_INDEX)["filter"]
        assert filter_expr == 'owner_id = "user-1\\" OR owner_id = \\"user-2"'

    def test_backslashes_in_type_filter_are_escaped(self, client, meili):
        """Backslashes are escaped before quotes, so no escape is broken."""
        response = client.get(
            "/api/v1/search/", query_string={"q": "x", "type": 'doc\\"ument'}
        )
        assert response.status_code == 200
        # An unknown type resolves to both indices; either records the filter.
        filter_expr = last_search_params(meili, DOCUMENTS_INDEX)["filter"]
        assert filter_expr == 'type = "doc\\\\\\"ument"'


class TestUnicodeAndStopwords:
    @pytest.mark.parametrize(
        "query",
        ["café", "日本語のドキュメント", "Ωμέγα", "naïve résumé"],
    )
    def test_unicode_queries_round_trip(self, client, meili, query):
        response = client.get("/api/v1/search/", query_string={"q": query})
        assert response.status_code == 200
        assert response.get_json()["query"] == query
        assert meili.index(FILES_INDEX).search_calls[-1][0] == query

    def test_emoji_query_round_trips(self, client, meili):
        response = client.get("/api/v1/search/", query_string={"q": "🦦 otter 🚀"})
        assert response.status_code == 200
        assert response.get_json()["query"] == "🦦 otter 🚀"

    def test_stopword_only_query_returns_an_empty_page_not_an_error(
        self, client, meili
    ):
        """A query of only stopwords is a normal query with no matches."""
        seed_documents(
            meili,
            {"id": "doc-1", "title": "Quarterly report", "owner_id": "user-1"},
        )
        response = client.get("/api/v1/search/", query_string={"q": "the and of a"})
        assert response.status_code == 200
        body = response.get_json()
        assert body["total"] == 0
        assert body["results"] == []

    def test_null_byte_in_query_is_handled(self, client):
        """A NUL byte must not crash the handler."""
        response = client.get("/api/v1/search/", query_string={"q": "abc\x00def"})
        assert response.status_code == 200
