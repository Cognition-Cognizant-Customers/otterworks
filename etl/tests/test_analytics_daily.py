import gzip
import json
from decimal import Decimal

from conftest import (
    FakeConnection,
    FakeCursor,
    FakeDynamoResource,
    FakeDynamoTable,
    FakeS3Client,
    load_script,
)

analytics_daily = load_script("analytics_daily")


class FakeSqsClient:
    def __init__(self, message_batches):
        self.message_batches = list(message_batches)
        self.deleted_batches = []

    def receive_message(self, **kwargs):
        if self.message_batches:
            return {"Messages": self.message_batches.pop(0)}
        return {}

    def delete_message_batch(self, QueueUrl, Entries):
        self.deleted_batches.append(Entries)
        return {}


def sqs_msg(msg_id, body):
    return {"MessageId": msg_id, "ReceiptHandle": "rh-%s" % msg_id, "Body": json.dumps(body)}


def run_pipeline(monkeypatch, fake_config, sqs_batches, dynamo_pages):
    fake_config(analytics_daily)
    sqs = FakeSqsClient(sqs_batches)
    s3 = FakeS3Client()
    dynamo = FakeDynamoResource(FakeDynamoTable(dynamo_pages))
    cursor = FakeCursor()
    conn = FakeConnection(cursor)

    def fake_client(service, **kwargs):
        return {"sqs": sqs, "s3": s3}[service]

    monkeypatch.setattr(analytics_daily.boto3, "client", fake_client)
    monkeypatch.setattr(analytics_daily.boto3, "resource", lambda service, **kwargs: dynamo)
    monkeypatch.setattr(analytics_daily.psycopg2, "connect", lambda **kwargs: conn)

    analytics_daily.main()
    return sqs, s3, cursor, conn


def test_full_pipeline_aggregates_and_loads(monkeypatch, fake_config):
    sqs_batches = [
        [
            sqs_msg("m1", {"eventType": "document_created", "userId": "alice",
                           "documentId": "d1", "timestamp": "2026-07-30T10:15:00Z"}),
            sqs_msg("m2", {"eventType": "file_uploaded", "ownerId": "bob",
                           "fileId": "f1", "sizeBytes": 2048,
                           "timestamp": "2026-07-30T11:00:00Z"}),
            sqs_msg("m3", {"eventType": "comment_added", "authorId": "alice",
                           "documentId": "d1", "timestamp": "not-a-timestamp"}),
            {"MessageId": "bad", "ReceiptHandle": "rh-bad", "Body": "{malformed"},
        ]
    ]
    dynamo_pages = [
        {
            "Items": [
                {"eventType": "file_shared", "userId": "carol", "fileId": "f2",
                 "sizeBytes": Decimal("512"), "timestamp": "2026-07-30T10:45:00Z"},
            ],
            "LastEvaluatedKey": {"pk": "next"},
        },
        {
            "Items": [
                {"eventType": "document_edited", "editedBy": "bob",
                 "documentId": "d2", "score": Decimal("1.5"),
                 "timestamp": "2026-07-30T23:59:00Z"},
            ],
        },
    ]

    sqs, s3, cursor, conn = run_pipeline(monkeypatch, fake_config, sqs_batches, dynamo_pages)

    # malformed SQS message skipped, valid ones deleted
    assert len(sqs.deleted_batches) == 1
    assert [e["Id"] for e in sqs.deleted_batches[0]] == ["m1", "m2", "m3"]

    puts = {c["Key"].split("/")[-1]: c for c in s3.put_calls}
    assert set(puts) == {"summary.json.gz", "hourly_breakdown.json.gz",
                         "top_users.jsonl.gz", "report.json"}

    summary = json.loads(gzip.decompress(puts["summary.json.gz"]["Body"]))
    assert summary["total_events"] == 5
    assert summary["active_users"] == 3  # alice, bob, carol
    assert summary["documents_created"] == 1
    assert summary["documents_edited"] == 1
    assert summary["comments_added"] == 1
    assert summary["files_uploaded"] == 1
    assert summary["files_shared"] == 1
    assert summary["bytes_uploaded"] == 2048
    assert summary["active_documents"] == 2  # d1, d2
    assert summary["active_files"] == 2  # f1 uploaded, f2 shared

    hourly = json.loads(gzip.decompress(puts["hourly_breakdown.json.gz"]["Body"]))
    assert hourly["10"] == {"document_created": 1, "file_shared": 1}
    assert hourly["11"] == {"file_uploaded": 1}
    assert hourly["00"] == {"comment_added": 1}  # unparseable timestamp bucketed at 00
    assert hourly["23"] == {"document_edited": 1}

    top_users = [json.loads(line) for line in
                 gzip.decompress(puts["top_users.jsonl.gz"]["Body"]).decode().splitlines()]
    by_uid = {u["user_id"]: u for u in top_users}
    assert by_uid["alice"]["total"] == 2
    assert by_uid["alice"]["actions"] == {"document_created": 1, "comment_added": 1}
    assert by_uid["bob"]["total"] == 2
    assert by_uid["carol"]["total"] == 1

    report = json.loads(puts["report.json"]["Body"])
    assert report["report_type"] == "daily_analytics"
    assert report["summary"] == summary

    # PostgreSQL upsert executed and committed with matching aggregates
    assert conn.committed
    assert len(cursor.executed) == 1
    _, params = cursor.executed[0]
    assert params[1:] == (
        summary["active_users"], summary["active_documents"], summary["active_files"],
        summary["total_events"], summary["documents_created"], summary["documents_edited"],
        summary["comments_added"], summary["files_uploaded"], summary["files_shared"],
        summary["files_deleted"], summary["bytes_uploaded"],
    )
    assert cursor.closed and conn.closed


def test_exits_cleanly_when_no_events(monkeypatch, fake_config):
    import pytest

    fake_config(analytics_daily)
    sqs = FakeSqsClient([])
    dynamo = FakeDynamoResource(FakeDynamoTable([{"Items": []}]))
    monkeypatch.setattr(analytics_daily.boto3, "client",
                        lambda service, **kwargs: {"sqs": sqs, "s3": FakeS3Client()}[service])
    monkeypatch.setattr(analytics_daily.boto3, "resource", lambda service, **kwargs: dynamo)

    with pytest.raises(SystemExit) as exc:
        analytics_daily.main()
    assert exc.value.code == 0


def test_postgres_failure_does_not_abort_report(monkeypatch, fake_config):
    fake_config(analytics_daily)
    sqs = FakeSqsClient([[sqs_msg("m1", {"eventType": "document_created", "userId": "alice",
                                         "timestamp": "2026-07-30T09:00:00Z"})]])
    s3 = FakeS3Client()
    dynamo = FakeDynamoResource(FakeDynamoTable([{"Items": []}]))

    def failing_connect(**kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(analytics_daily.boto3, "client",
                        lambda service, **kwargs: {"sqs": sqs, "s3": s3}[service])
    monkeypatch.setattr(analytics_daily.boto3, "resource", lambda service, **kwargs: dynamo)
    monkeypatch.setattr(analytics_daily.psycopg2, "connect", failing_connect)

    analytics_daily.main()

    keys = [c["Key"] for c in s3.put_calls]
    assert any(k.endswith("report.json") for k in keys)
