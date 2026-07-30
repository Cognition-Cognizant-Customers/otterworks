import gzip
import json
from decimal import Decimal

import pytest

from conftest import FakeDynamoResource, FakeDynamoTable, FakeS3Client, load_script

audit_archive_weekly = load_script("audit_archive_weekly")


def test_decimal_encoder_handles_ints_and_floats():
    encoded = json.dumps(
        {"count": Decimal("3"), "score": Decimal("1.25")},
        cls=audit_archive_weekly.DecimalEncoder,
    )
    assert json.loads(encoded) == {"count": 3, "score": 1.25}


def test_archives_compresses_and_deletes(monkeypatch, fake_config):
    fake_config(audit_archive_weekly)

    events = [
        {"event_id": "e%03d" % i, "timestamp": "2025-01-01T00:00:%02dZ" % (i % 60),
         "action": "login", "attempts": Decimal(str(i))}
        for i in range(60)  # > 2 full batches of 25 + remainder of 10
    ]
    table = FakeDynamoTable([
        {"Items": events[:30], "LastEvaluatedKey": {"pk": "next"}},
        {"Items": events[30:]},
    ])
    s3 = FakeS3Client()

    monkeypatch.setattr(audit_archive_weekly.boto3, "resource",
                        lambda service, **kwargs: FakeDynamoResource(table))
    monkeypatch.setattr(audit_archive_weekly.boto3, "client", lambda service, **kwargs: s3)

    audit_archive_weekly.main()

    archive_puts = [c for c in s3.put_calls if c["Key"].endswith("audit_events.jsonl.gz")]
    assert len(archive_puts) == 1
    assert archive_puts[0]["StorageClass"] == "GLACIER"
    lines = gzip.decompress(archive_puts[0]["Body"]).decode().splitlines()
    assert len(lines) == 60
    first = json.loads(lines[0])
    assert first["event_id"] == "e000"
    assert first["attempts"] == 0  # Decimal encoded as int

    # every archived event is deleted from DynamoDB in batches
    assert len(table.deleted_keys) == 60
    assert table.deleted_keys[0] == {"event_id": "e000", "timestamp": events[0]["timestamp"]}

    report_puts = [c for c in s3.put_calls if c["Key"].endswith("report.json")]
    assert len(report_puts) == 1
    report = json.loads(report_puts[0]["Body"])
    assert report["results"]["events_archived"] == 60
    assert report["results"]["events_deleted_from_source"] == 60


def test_exits_cleanly_when_nothing_to_archive(monkeypatch, fake_config):
    fake_config(audit_archive_weekly)
    table = FakeDynamoTable([{"Items": []}])
    monkeypatch.setattr(audit_archive_weekly.boto3, "resource",
                        lambda service, **kwargs: FakeDynamoResource(table))
    monkeypatch.setattr(audit_archive_weekly.boto3, "client",
                        lambda service, **kwargs: FakeS3Client())

    with pytest.raises(SystemExit) as exc:
        audit_archive_weekly.main()
    assert exc.value.code == 0
