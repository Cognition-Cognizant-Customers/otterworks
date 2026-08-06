"""Search analytics accounting, including the retention boundary.

The analytics store is a module-level dict; the fixture below swaps in an
empty one per test and restores the original afterwards, so these tests
neither depend on nor leak state.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

import pytest

from app.services import meilisearch_client
from app.services.meilisearch_client import (
    MAX_ANALYTICS_ENTRIES,
    get_search_analytics,
    record_search_analytics,
)

TOP_N = 20


@pytest.fixture(autouse=True)
def isolated_analytics_store():
    """Give each test its own analytics store and restore the original."""
    original = copy.deepcopy(meilisearch_client._search_analytics)
    meilisearch_client._search_analytics.clear()
    meilisearch_client._search_analytics.update(
        {"queries": [], "total_searches": 0, "total_results": 0}
    )
    yield
    meilisearch_client._search_analytics.clear()
    meilisearch_client._search_analytics.update(original)


class TestRecording:
    """What ``record_search_analytics`` accumulates."""

    def test_empty_store_reports_zeroes(self):
        analytics = get_search_analytics()
        assert analytics.total_searches == 0
        assert analytics.avg_results_per_query == 0.0
        assert analytics.popular_queries == []
        assert analytics.zero_result_queries == []

    def test_totals_and_average_are_accumulated(self):
        record_search_analytics("otter", 3)
        record_search_analytics("otter", 0)
        analytics = get_search_analytics()
        assert analytics.total_searches == 2
        assert analytics.avg_results_per_query == 1.5

    def test_average_is_rounded_to_two_decimals(self):
        for _ in range(3):
            record_search_analytics("otter", 1)
        record_search_analytics("otter", 0)
        assert get_search_analytics().avg_results_per_query == 0.75

    def test_zero_result_queries_are_tracked_separately(self):
        record_search_analytics("otter", 5)
        record_search_analytics("no-such-thing", 0)
        record_search_analytics("no-such-thing", 0)
        analytics = get_search_analytics()
        assert analytics.zero_result_queries == [{"query": "no-such-thing", "count": 2}]
        assert {entry["query"] for entry in analytics.popular_queries} == {
            "otter",
            "no-such-thing",
        }

    def test_popular_queries_are_ordered_by_frequency(self):
        record_search_analytics("rare", 1)
        for _ in range(3):
            record_search_analytics("common", 1)
        assert get_search_analytics().popular_queries[0] == {"query": "common", "count": 3}

    @pytest.mark.parametrize("distinct", [TOP_N - 1, TOP_N, TOP_N + 1])
    def test_top_lists_are_capped_at_twenty(self, distinct: int):
        for i in range(distinct):
            record_search_analytics(f"query-{i}", 0)
        analytics = get_search_analytics()
        assert len(analytics.popular_queries) == min(distinct, TOP_N)
        assert len(analytics.zero_result_queries) == min(distinct, TOP_N)

    def test_empty_and_wildcard_queries_are_recorded_verbatim(self):
        record_search_analytics("", 0)
        record_search_analytics("*", 7)
        recorded = {entry["query"] for entry in get_search_analytics().popular_queries}
        assert recorded == {"", "*"}


class TestRetentionBoundary:
    """The store keeps at most ``MAX_ANALYTICS_ENTRIES`` raw entries."""

    @pytest.mark.parametrize(
        "recorded",
        [MAX_ANALYTICS_ENTRIES - 1, MAX_ANALYTICS_ENTRIES, MAX_ANALYTICS_ENTRIES + 1],
    )
    def test_retention_trio(self, recorded: int):
        for i in range(recorded):
            record_search_analytics(f"query-{i}", 0)
        queries = meilisearch_client._search_analytics["queries"]
        assert len(queries) == min(recorded, MAX_ANALYTICS_ENTRIES)
        # The oldest entry is evicted only once the cap is exceeded.
        oldest_kept = 1 if recorded > MAX_ANALYTICS_ENTRIES else 0
        assert queries[0]["query"] == f"query-{oldest_kept}"

    def test_running_totals_survive_eviction(self):
        for _ in range(MAX_ANALYTICS_ENTRIES + 5):
            record_search_analytics("otter", 1)
        analytics = get_search_analytics()
        assert analytics.total_searches == MAX_ANALYTICS_ENTRIES + 5
        assert analytics.avg_results_per_query == 1.0


class TestEndpointIntegration:
    """Which endpoints feed the analytics store."""

    @pytest.fixture()
    def searching_client(self, client, mock_meilisearch_client: MagicMock):
        mock_meilisearch_client.index.return_value.search.return_value = {
            "estimatedTotalHits": 0,
            "hits": [],
        }
        return client

    def test_search_records_the_query(self, searching_client):
        searching_client.get("/api/v1/search/?q=otterworks")
        assert get_search_analytics().popular_queries == [
            {"query": "otterworks", "count": 1}
        ]

    def test_advanced_search_without_a_query_records_a_wildcard(self, searching_client):
        searching_client.post("/api/v1/search/advanced", json={"type": "document"})
        assert get_search_analytics().popular_queries == [{"query": "*", "count": 1}]

    def test_suggest_does_not_pollute_analytics(self, searching_client):
        searching_client.get("/api/v1/search/suggest?q=ot")
        assert get_search_analytics().total_searches == 0

    def test_failed_search_is_not_recorded(self, searching_client, mock_meilisearch_client):
        mock_meilisearch_client.index.return_value.search.side_effect = RuntimeError("down")
        assert searching_client.get("/api/v1/search/?q=otter").status_code == 500
        assert get_search_analytics().total_searches == 0

    def test_analytics_endpoint_reflects_recorded_searches(self, searching_client):
        searching_client.get("/api/v1/search/?q=otter")
        searching_client.get("/api/v1/search/?q=otter")
        body = searching_client.get("/api/v1/search/analytics").get_json()
        assert body["total_searches"] == 2
        assert body["popular_queries"] == [{"query": "otter", "count": 2}]
        assert body["zero_result_queries"] == [{"query": "otter", "count": 2}]
