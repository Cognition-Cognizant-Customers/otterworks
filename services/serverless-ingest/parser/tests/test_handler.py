"""Handler-level tests with fake S3/DynamoDB doubles (fixture run mode).

Covers the SQS envelope path, idempotent reprocess, poison-message
propagation (fetch failure raises so SQS redrives to the DLQ), and
non-CUSTBILL key skipping.
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import handler  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
BUCKET = "ow-tp-demo-pipeline-000000000000"


class FakeBody:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise RuntimeError(f"NoSuchKey: s3://{Bucket}/{Key}")
        return {"Body": FakeBody(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = Body


class FakeTable:
    def __init__(self):
        self.items = {}
        self.put_calls = 0

    def put_item(self, Item):
        self.put_calls += 1
        self.items[(Item["pk"], Item["sk"])] = Item


def sqs_event(key, bucket=BUCKET):
    envelope = {
        "source": "aws.s3",
        "detail-type": "Object Created",
        "detail": {"bucket": {"name": bucket}, "object": {"key": key}},
    }
    return {"Records": [{"body": json.dumps(envelope)}]}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("NS", "demo")
    monkeypatch.setenv("BATCH_STATE_TABLE", "ow-tp-demo-batch-state")


def load(s3, name):
    s3.objects[(BUCKET, f"incoming/{name}")] = (FIXTURES / name).read_bytes()


def test_parses_object_and_writes_ledger(env):
    s3, table = FakeS3(), FakeTable()
    load(s3, "CUSTBILL_DEMO_ANOM.dat")

    handler.lambda_handler(sqs_event("incoming/CUSTBILL_DEMO_ANOM.dat"), None, s3, table)

    golden = (FIXTURES / "CUSTBILL_DEMO_ANOM.psv").read_bytes()
    assert s3.objects[(BUCKET, "parsed/CUSTBILL_DEMO_ANOM.psv")] == golden

    file_item = table.items[("file#demo", "CUSTBILL_DEMO_ANOM.psv")]
    assert file_item["record_count"] == 3
    assert file_item["trailer_count"] == 5

    anomaly_keys = sorted(k[1] for k in table.items if k[0] == "anomaly#demo")
    assert anomaly_keys == [
        "CUSTBILL_DEMO_ANOM#A-invalid-date",
        "CUSTBILL_DEMO_ANOM#A-nonutf8-byte",
        "CUSTBILL_DEMO_ANOM#A-short-record",
        "CUSTBILL_DEMO_ANOM#A-trailer-mismatch",
    ]


def test_clean_file_writes_no_anomaly_items(env):
    s3, table = FakeS3(), FakeTable()
    load(s3, "CUSTBILL_DEMO_001.dat")

    handler.lambda_handler(sqs_event("incoming/CUSTBILL_DEMO_001.dat"), None, s3, table)

    golden = (FIXTURES / "CUSTBILL_DEMO_001.psv").read_bytes()
    assert s3.objects[(BUCKET, "parsed/CUSTBILL_DEMO_001.psv")] == golden
    assert not any(k[0] == "anomaly#demo" for k in table.items)
    assert table.items[("file#demo", "CUSTBILL_DEMO_001.psv")]["record_count"] == 50


def test_idempotent_reprocess_rewrites_identical_state(env):
    s3, table = FakeS3(), FakeTable()
    load(s3, "CUSTBILL_DEMO_ANOM.dat")
    event = sqs_event("incoming/CUSTBILL_DEMO_ANOM.dat")

    handler.lambda_handler(event, None, s3, table)
    first_objects = dict(s3.objects)
    first_items = {k: dict(v) for k, v in table.items.items()}

    handler.lambda_handler(event, None, s3, table)  # SQS at-least-once redelivery

    assert s3.objects == first_objects
    assert {k: dict(v) for k, v in table.items.items()} == first_items
    assert len(table.items) == 5  # 1 file item + 4 anomaly items, no duplicates


def test_poison_message_raises_for_dlq_redrive(env):
    s3, table = FakeS3(), FakeTable()
    with pytest.raises(RuntimeError, match="NoSuchKey"):
        handler.lambda_handler(sqs_event("incoming/CUSTBILL_DEMO_404.dat"), None, s3, table)
    assert table.put_calls == 0


def test_non_custbill_keys_are_skipped(env):
    s3, table = FakeS3(), FakeTable()
    s3.objects[(BUCKET, "incoming/README.txt")] = b"not a custbill file"

    handler.lambda_handler(sqs_event("incoming/README.txt"), None, s3, table)

    assert table.put_calls == 0
    assert (BUCKET, "parsed/README.psv") not in s3.objects
