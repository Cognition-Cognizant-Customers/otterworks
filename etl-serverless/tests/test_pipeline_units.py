from datetime import UTC, datetime, timedelta

import pytest
from otterworks_etl.audit_archive.handler import cutoff_for
from otterworks_etl.common.dispatch import make_handler
from otterworks_etl.storage_cleanup.handler import find_orphans
from otterworks_etl.user_activity.transform import build_trends, merge_user_day


class TestCutoffFor:
    def test_default_retention(self):
        assert cutoff_for("2026-08-13") == "2026-05-15T00:00:00Z"

    def test_custom_retention(self):
        assert cutoff_for("2026-08-13", retention_days=1) == "2026-08-12T00:00:00Z"


class TestFindOrphans:
    def test_no_orphans(self):
        objects = [{"key": "files/a", "size": 10}]
        orphaned, size = find_orphans(objects, {"files/a"})
        assert orphaned == []
        assert size == 0

    def test_orphans_and_size(self):
        objects = [
            {"key": "files/a", "size": 10},
            {"key": "files/b", "size": 25},
            {"key": "files/c", "size": 7},
        ]
        orphaned, size = find_orphans(objects, {"files/b"})
        assert [o["key"] for o in orphaned] == ["files/a", "files/c"]
        assert size == 17

    def test_empty_objects(self):
        assert find_orphans([], set()) == ([], 0)

    def test_recent_objects_skipped(self):
        now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
        objects = [
            {"key": "files/new", "size": 10,
             "last_modified": (now - timedelta(hours=1)).isoformat()},
            {"key": "files/old", "size": 25,
             "last_modified": (now - timedelta(days=2)).isoformat()},
        ]
        orphaned, size = find_orphans(objects, set(), now=now)
        assert [o["key"] for o in orphaned] == ["files/old"]
        assert size == 25


class TestMergeUserDay:
    def test_new_user(self):
        totals: dict[str, dict] = {}
        merge_user_day(totals, {"user_id": "u1", "total": 3, "actions": {"edit": 3}})
        assert totals["u1"] == {
            "user_id": "u1",
            "total_actions": 3,
            "active_days": 1,
            "actions_by_type": {"edit": 3},
        }

    def test_accumulates_across_days(self):
        totals: dict[str, dict] = {}
        merge_user_day(totals, {"user_id": "u1", "total": 3, "actions": {"edit": 3}})
        merge_user_day(totals, {"user_id": "u1", "total": 2, "actions": {"edit": 1, "view": 1}})
        assert totals["u1"]["total_actions"] == 5
        assert totals["u1"]["active_days"] == 2
        assert totals["u1"]["actions_by_type"] == {"edit": 4, "view": 1}

    def test_missing_user_id(self):
        totals: dict[str, dict] = {}
        merge_user_day(totals, {"total": 1})
        assert "unknown" in totals


class TestBuildTrends:
    def test_empty(self):
        assert build_trends([]) == {
            "total_events": 0,
            "peak_active_users": 0,
            "avg_daily_events": 0,
            "reporting_days": 0,
        }

    def test_aggregates(self):
        summaries = [
            {"total_events": 10, "active_users": 3},
            {"total_events": 20, "active_users": 5},
        ]
        assert build_trends(summaries) == {
            "total_events": 30,
            "peak_active_users": 5,
            "avg_daily_events": 15.0,
            "reporting_days": 2,
        }


class TestDispatch:
    def test_routes_to_task(self):
        handler = make_handler("test", {"do_thing": lambda event: {"ok": True}})
        assert handler({"task": "do_thing"}, None) == {"ok": True}

    def test_unknown_task_raises(self):
        handler = make_handler("test", {"do_thing": lambda event: {}})
        with pytest.raises(ValueError, match="Unknown task 'nope'"):
            handler({"task": "nope"}, None)

    def test_missing_task_raises(self):
        handler = make_handler("test", {"do_thing": lambda event: {}})
        with pytest.raises(ValueError):
            handler({}, None)
