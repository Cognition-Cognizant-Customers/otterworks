import gzip
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import FakeConnection, FakeCursor, FakeS3Client, load_script

user_activity_daily = load_script("user_activity_daily")


def summary_row(report_date, total_events, active_users):
    return (report_date, active_users, 5, 7, total_events, 1, 2, 3, 4, 5, 6, 7000)


def jsonl_gz(records):
    return gzip.compress(("\n".join(json.dumps(r) for r in records) + "\n").encode())


def test_report_aggregates_postgres_and_s3_data(monkeypatch, fake_config):
    fake_config(user_activity_daily)

    rows = [
        summary_row(date(2026, 7, 28), 100, 10),
        summary_row(date(2026, 7, 29), 300, 30),
    ]
    cursor = FakeCursor(rows=rows)
    conn = FakeConnection(cursor)

    ds = datetime.now(tz=timezone.utc)
    day0 = ds.strftime("analytics/daily/year=%Y/month=%m/day=%d/top_users.jsonl.gz")
    day1 = (ds - timedelta(days=1)).strftime(
        "analytics/daily/year=%Y/month=%m/day=%d/top_users.jsonl.gz"
    )

    s3 = FakeS3Client(objects={
        ("test-data-lake", day0): jsonl_gz([
            {"user_id": "alice", "total": 5, "actions": {"document_created": 2, "comment_added": 3}},
            {"user_id": "bob", "total": 1, "actions": {"file_uploaded": 1}},
        ]),
        ("test-data-lake", day1): jsonl_gz([
            {"user_id": "alice", "total": 4, "actions": {"document_created": 4}},
        ]),
    })

    monkeypatch.setattr(user_activity_daily.psycopg2, "connect", lambda **kwargs: conn)
    monkeypatch.setattr(user_activity_daily.boto3, "client", lambda service, **kwargs: s3)

    user_activity_daily.main()

    puts = {c["Key"]: c for c in s3.put_calls}
    ds_str = ds.strftime("%Y-%m-%d")
    assert "reports/user-activity/%s/activity_report.json" % ds_str in puts
    assert "reports/user-activity/latest/activity_report.json" in puts
    assert "reports/user-activity/%s/user_summaries.jsonl" % ds_str in puts

    report = json.loads(puts["reports/user-activity/latest/activity_report.json"]["Body"])
    assert report["trends"]["total_events"] == 400
    assert report["trends"]["peak_active_users"] == 30
    assert report["trends"]["avg_daily_events"] == 200.0
    assert report["trends"]["reporting_days"] == 2

    # date objects serialized via isoformat
    assert report["daily_summaries"][0]["report_date"] == "2026-07-28"

    top = report["top_users"]
    assert top[0]["user_id"] == "alice"
    assert top[0]["total_actions"] == 9
    assert top[0]["active_days"] == 2
    assert top[0]["actions_by_type"] == {"document_created": 6, "comment_added": 3}
    assert top[1]["user_id"] == "bob"

    lines = puts["reports/user-activity/%s/user_summaries.jsonl" % ds_str]["Body"]
    assert len(lines.decode().strip().splitlines()) == 2

    assert cursor.closed and conn.closed


def test_exits_nonzero_when_postgres_unavailable(monkeypatch, fake_config):
    fake_config(user_activity_daily)

    def failing_connect(**kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(user_activity_daily.psycopg2, "connect", failing_connect)
    monkeypatch.setattr(user_activity_daily.boto3, "client",
                        lambda service, **kwargs: FakeS3Client())

    with pytest.raises(SystemExit) as exc:
        user_activity_daily.main()
    assert exc.value.code == 1


def test_missing_s3_days_are_skipped(monkeypatch, fake_config):
    fake_config(user_activity_daily)
    cursor = FakeCursor(rows=[])
    conn = FakeConnection(cursor)
    s3 = FakeS3Client(objects={})  # no per-user data at all

    monkeypatch.setattr(user_activity_daily.psycopg2, "connect", lambda **kwargs: conn)
    monkeypatch.setattr(user_activity_daily.boto3, "client", lambda service, **kwargs: s3)

    user_activity_daily.main()

    report = json.loads(
        [c for c in s3.put_calls if c["Key"].endswith("latest/activity_report.json")][0]["Body"]
    )
    assert report["trends"] == {
        "total_events": 0, "peak_active_users": 0,
        "avg_daily_events": 0, "reporting_days": 0,
    }
    assert report["user_summaries"] == []
