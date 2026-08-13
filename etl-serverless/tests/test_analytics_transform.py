from otterworks_etl.analytics.transform import (
    aggregate_events,
    peak_hour,
    resolve_event_type,
    resolve_hour,
    resolve_user_id,
)


def make_event(**kwargs):
    return {"timestamp": "2026-08-13T14:30:00Z", **kwargs}


class TestResolvers:
    def test_event_type_camel_case(self):
        assert resolve_event_type({"eventType": "file_uploaded"}) == "file_uploaded"

    def test_event_type_snake_case(self):
        assert resolve_event_type({"event_type": "file_uploaded"}) == "file_uploaded"

    def test_event_type_missing(self):
        assert resolve_event_type({}) == "unknown"

    def test_user_id_priority_order(self):
        assert resolve_user_id({"ownerId": "u1", "userId": "u2"}) == "u1"

    def test_user_id_falls_through_empty(self):
        assert resolve_user_id({"ownerId": "", "userId": "u2"}) == "u2"

    def test_user_id_missing(self):
        assert resolve_user_id({}) == "unknown"

    def test_hour_from_iso_timestamp(self):
        assert resolve_hour({"timestamp": "2026-08-13T14:30:00Z"}) == "14"

    def test_hour_invalid_timestamp(self):
        assert resolve_hour({"timestamp": "not-a-date"}) == "00"

    def test_hour_missing(self):
        assert resolve_hour({}) == "00"


class TestAggregateEvents:
    def test_empty_events(self):
        result = aggregate_events([])
        assert result["summary"]["total_events"] == 0
        assert result["summary"]["active_users"] == 0
        assert result["user_summaries"] == []

    def test_document_and_file_metrics(self):
        events = [
            make_event(eventType="document_created", userId="u1", documentId="d1"),
            make_event(eventType="document_edited", userId="u1", documentId="d1"),
            make_event(eventType="comment_added", userId="u2"),
            make_event(eventType="file_uploaded", userId="u2", fileId="f1", sizeBytes=1000),
            make_event(eventType="file_shared", userId="u1", fileId="f1"),
            make_event(eventType="file_deleted", userId="u3", fileId="f2"),
        ]
        summary = aggregate_events(events)["summary"]
        assert summary == {
            "active_users": 3,
            "active_documents": 1,
            "active_files": 2,
            "total_events": 6,
            "documents_created": 1,
            "documents_edited": 1,
            "comments_added": 1,
            "files_uploaded": 1,
            "files_shared": 1,
            "files_deleted": 1,
            "bytes_uploaded": 1000,
        }

    def test_unknown_user_excluded_from_active(self):
        events = [make_event(eventType="document_created")]
        result = aggregate_events(events)
        assert result["summary"]["active_users"] == 0
        assert result["summary"]["total_events"] == 1

    def test_top_users_sorted_and_limited(self):
        events = []
        for i in range(5):
            for _ in range(i + 1):
                events.append(make_event(eventType="comment_added", userId=f"u{i}"))
        result = aggregate_events(events, top_users=3)
        totals = [u["total"] for u in result["user_summaries"]]
        assert totals == [5, 4, 3]

    def test_hourly_breakdown(self):
        events = [
            {"timestamp": "2026-08-13T02:00:00Z", "eventType": "a"},
            {"timestamp": "2026-08-13T02:30:00Z", "eventType": "a"},
            {"timestamp": "2026-08-13T09:00:00Z", "eventType": "b"},
        ]
        breakdown = aggregate_events(events)["hourly_breakdown"]
        assert breakdown == {"02": {"a": 2}, "09": {"b": 1}}


class TestPeakHour:
    def test_none_when_empty(self):
        assert peak_hour({}) is None

    def test_picks_busiest_hour(self):
        breakdown = {"02": {"a": 2, "b": 1}, "09": {"b": 1}}
        assert peak_hour(breakdown) == {"hour": "02", "event_count": 3}
