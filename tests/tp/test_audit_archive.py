"""Unit tests for the cron-archive expiry-driven archiver.

These exercise the pure logic of the packaged Lambda handler (boundary semantics,
archive key determinism, encoding, malformed records, the TTL stream envelope) with
stubbed AWS clients, so they need no credentials and no LocalStack. The end-to-end
proof against the fixture estate is the recon script in fixture mode.
"""

from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infrastructure/terraform/tp-cronbox/lambda/audit_archive"))

import handler

RUN_DATE = datetime(2026, 1, 15, tzinfo=timezone.utc)
CUTOFF = RUN_DATE - timedelta(days=90)
REFERENCE = "2026-01-15T00:00:00Z"


def item(event_id, stamp, expires_at=None, **extra):
    record = {
        "event_id": {"S": event_id},
        "timestamp": {"S": stamp.strftime("%Y-%m-%dT%H:%M:%SZ")},
        "actor": {"S": "user-000"},
    }
    if expires_at is not None:
        record["expires_at"] = {"N": str(expires_at)}
    record.update(extra)
    return record


def ttl_epoch(stamp):
    return int((stamp + timedelta(days=90)).timestamp())


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = 0

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise handler.ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = Body
        self.puts += 1


class FakeDynamo:
    def __init__(self, items):
        self.items = items

    def scan(self, **kwargs):
        return {"Items": self.items}


@pytest.fixture(autouse=True)
def stub_clients(monkeypatch):
    s3, dynamo = FakeS3(), FakeDynamo([])
    clients = {"s3": s3, "dynamodb": dynamo}

    def fake_client(service):
        if service == "cloudwatch":
            return type("NoopMetrics", (), {"put_metric_data": lambda self, **kw: None})()
        return clients[service]

    monkeypatch.setattr(handler, "_client", fake_client)
    return clients


def sweep(stub_clients, items):
    stub_clients["dynamodb"].items = items
    return handler.handle_sweep({"mode": "sweep", "reference_time": REFERENCE})


def test_cutoff_is_exclusive(stub_clients):
    items = [
        item("demo-boundary-0", CUTOFF - timedelta(seconds=1), ttl_epoch(CUTOFF - timedelta(seconds=1))),
        item("demo-boundary-1", CUTOFF, ttl_epoch(CUTOFF)),
        item("demo-boundary-2", CUTOFF + timedelta(seconds=1), ttl_epoch(CUTOFF + timedelta(seconds=1))),
    ]
    result = sweep(stub_clients, items)
    assert result["archived"] == ["demo-boundary-0"]
    assert result["retained"] == ["demo-boundary-1", "demo-boundary-2"]


def test_missing_ttl_attribute_is_retained_and_attributed(stub_clients):
    result = sweep(stub_clients, [item("demo-unexpirable", CUTOFF - timedelta(days=400))])
    assert result["unexpirable"] == ["demo-unexpirable"]
    assert result["archived"] == []
    assert stub_clients["s3"].objects == {}


def test_unparseable_ttl_attribute_is_retained_and_attributed(stub_clients):
    broken = item("demo-broken", CUTOFF - timedelta(days=400))
    broken["expires_at"] = {"S": "not-a-number"}
    result = sweep(stub_clients, [broken])
    assert result["unexpirable"] == ["demo-broken"]
    assert result["archived"] == []


def test_rerun_is_convergent(stub_clients):
    stamp = CUTOFF - timedelta(days=3)
    items = [item("demo-audit-0000", stamp, ttl_epoch(stamp))]
    first = sweep(stub_clients, items)
    second = sweep(stub_clients, items)
    assert first["archived"] == ["demo-audit-0000"]
    assert second["archived"] == []
    assert second["already_archived"] == ["demo-audit-0000"]
    assert len(stub_clients["s3"].objects) == 1
    assert stub_clients["s3"].puts == 1


def test_archive_key_is_a_function_of_the_composite_key():
    record = {"event_id": "demo-audit-0078", "timestamp": "2025-07-29T23:58:42Z", "expires_at": 1761609522}
    assert handler.archive_key(record).endswith("demo-audit-0078__20250729T235842Z.jsonl.gz")
    assert handler.archive_key(record) == handler.archive_key(dict(record))


def test_payload_is_archived_verbatim_and_deterministically(stub_clients):
    stamp = CUTOFF - timedelta(days=3)
    payload = json.dumps({"i": 78}, sort_keys=True)
    record = item(
        "demo-audit-0078",
        stamp,
        ttl_epoch(stamp),
        raw_payload={"S": payload},
        note={"S": "café ünïcode"},
        attempts={"N": "3"},
    )
    sweep(stub_clients, [record])
    body = next(iter(stub_clients["s3"].objects.values()))
    decoded = json.loads(gzip.decompress(body).decode("utf-8").strip())
    assert decoded["raw_payload"] == payload
    assert decoded["note"] == "café ünïcode"
    assert decoded["attempts"] == 3
    assert b"caf\xc3\xa9" in gzip.decompress(body)  # not escaped to \uXXXX
    assert gzip.decompress(body).endswith(b"\n")
    # gzip mtime=0 keeps the archive bytes stable across runs
    assert handler.encode(handler.deserialize(record)) == body


def test_binary_attribute_survives_archival(stub_clients):
    stamp = CUTOFF - timedelta(days=3)
    record = item("demo-audit-bin", stamp, ttl_epoch(stamp), blob={"B": b"\xff\xfe\x00"})
    sweep(stub_clients, [record])
    body = next(iter(stub_clients["s3"].objects.values()))
    decoded = json.loads(gzip.decompress(body).decode("utf-8").strip())
    assert decoded["blob"] == {"$binary_base64": "//4A"}


def test_empty_input_writes_nothing(stub_clients):
    result = sweep(stub_clients, [])
    assert result["archived"] == []
    assert stub_clients["s3"].objects == {}


def test_ttl_stream_removal_is_archived_before_the_item_is_lost(stub_clients):
    stamp = CUTOFF - timedelta(days=3)
    event = {
        "Records": [
            {
                "eventID": "1",
                "eventName": "REMOVE",
                "userIdentity": {"type": "Service", "principalId": "dynamodb.amazonaws.com"},
                "dynamodb": {"OldImage": item("demo-audit-0001", stamp, ttl_epoch(stamp))},
            }
        ]
    }
    result = handler.handle_stream(event)
    assert result["archived"] == ["demo-audit-0001"]
    assert list(stub_clients["s3"].objects) == [
        handler.archive_key({"event_id": "demo-audit-0001", "timestamp": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"), "expires_at": ttl_epoch(stamp)})
    ]


def test_non_ttl_removal_is_not_archived(stub_clients):
    stamp = CUTOFF - timedelta(days=3)
    event = {
        "Records": [
            {
                "eventID": "2",
                "eventName": "REMOVE",
                "userIdentity": {"type": "IAMUser", "principalId": "AIDAEXAMPLE"},
                "dynamodb": {"OldImage": item("demo-audit-0002", stamp, ttl_epoch(stamp))},
            }
        ]
    }
    result = handler.handle_stream(event)
    assert result["archived"] == []
    assert result["skipped_non_ttl"] == ["2"]
    assert stub_clients["s3"].objects == {}


def test_stream_reports_failures_per_record(stub_clients, monkeypatch):
    """A TTL removal is the item's last copy: one failure must not drop the batch."""
    stamp = CUTOFF - timedelta(days=3)
    original = handler.put_archive

    def flaky(s3, record):
        if record["event_id"] == "demo-audit-0005":
            raise handler.ClientError({"Error": {"Code": "InternalError"}}, "PutObject")
        return original(s3, record)

    monkeypatch.setattr(handler, "put_archive", flaky)
    event = {
        "Records": [
            {
                "eventID": str(index),
                "eventName": "REMOVE",
                "userIdentity": {"principalId": "dynamodb.amazonaws.com"},
                "dynamodb": {
                    "SequenceNumber": f"seq-{index}",
                    "OldImage": item(f"demo-audit-000{index}", stamp, ttl_epoch(stamp)),
                },
            }
            for index in (4, 5, 6)
        ]
    }
    result = handler.handle_stream(event)
    assert result["archived"] == ["demo-audit-0004", "demo-audit-0006"]
    assert result["batchItemFailures"] == [{"itemIdentifier": "seq-5"}]


def test_stream_and_sweep_agree_on_the_archive_key(stub_clients):
    """The two paths must never produce two objects for the same item."""
    stamp = CUTOFF - timedelta(days=3)
    record = item("demo-audit-0003", stamp, ttl_epoch(stamp))
    sweep(stub_clients, [record])
    handler.handle_stream(
        {
            "Records": [
                {
                    "eventID": "3",
                    "eventName": "REMOVE",
                    "userIdentity": {"principalId": "dynamodb.amazonaws.com"},
                    "dynamodb": {"OldImage": record},
                }
            ]
        }
    )
    assert len(stub_clients["s3"].objects) == 1
    assert stub_clients["s3"].puts == 1


def test_sweep_matches_the_golden_archive_set(stub_clients):
    """81 of the 103 seeded records archive; the golden artifact is the expectation."""
    sys.path.insert(0, str(ROOT / "scripts/tp_aws"))
    import audit_archive_recon as recon

    corpus = recon.seed_corpus("demo", "2026-01-15")
    images = []
    for record in corpus:
        image = {"event_id": {"S": record["event_id"]}, "timestamp": {"S": record["timestamp"]}}
        for name, value in record.items():
            if name in image:
                continue
            image[name] = {"N": str(value)} if isinstance(value, int) else {"S": str(value)}
        images.append(image)
    result = sweep(stub_clients, images)
    expected = sorted(recon.golden_archive_records("demo"))
    assert result["archived"] == expected
    assert len(result["archived"]) == 81
    assert sorted(result["retained"]) == recon.golden_retained_ids("demo")
    assert result["unexpirable"] == [recon.UNEXPIRABLE_PROBE]
