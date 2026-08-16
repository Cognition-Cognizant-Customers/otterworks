"""Fixture-mode contract tests for the ingest Lambda (moto-emulated S3/DynamoDB).

Covers the aws-ingest acceptance checks that are provable without a live
apply: byte-transparent-copy, archive-copy, landing-deleted, name-validation,
idempotent-redelivery, errors-surfaced. Live-only paths (real EventBridge
envelope delivery, lambda permission SourceArn resolution, DLQ drain, IAM
policy evaluation) are the parent's to verify.
"""

import hashlib
import importlib
import os
import sys

import boto3
import pytest
from moto import mock_aws

BUCKET = "ow-tp-demo-pipeline-599083837640"
TABLE = "ow-tp-demo-batch-state"

# Golden baseline inputs (immutable; see the aws-ingest contract).
GOLDEN_MD5 = {
    "CUSTBILL_DEMO_001.dat": "f304679dff6190de3206ccc15466428c",
    "CUSTBILL_DEMO_002.dat": "5153ba871c74d8d2d518021f63b36d07",
    "CUSTBILL_DEMO_ANOM.dat": "11eb3d1a3cf99ad46d66d3c65c0add01",
}


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("PIPELINE_BUCKET", BUCKET)
    monkeypatch.setenv("BATCH_STATE_TABLE", TABLE)
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import handler as mod
        importlib.reload(mod)
        yield mod, s3, ddb


def land(s3, basename, body):
    s3.put_object(Bucket=BUCKET, Key=f"landing/{basename}", Body=body)
    head = s3.head_object(Bucket=BUCKET, Key=f"landing/{basename}")
    return event_for(basename, head["ETag"].strip('"'))


def event_for(basename, etag=""):
    return {
        "source": "aws.s3",
        "detail-type": "Object Created",
        "time": "2026-01-15T00:00:00Z",
        "detail": {
            "bucket": {"name": BUCKET},
            "object": {"key": f"landing/{basename}", "etag": etag},
        },
    }


def get_body(s3, key):
    return s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()


def ledger_items(ddb):
    return ddb.scan(TableName=TABLE)["Items"]


def golden_dir():
    root = os.environ.get("TP_GOLDEN_INPUTS", "/tmp/otterworks-legacy/sftp-drop/upload")
    return root


def test_byte_transparent_copy_including_non_utf8(env):
    mod, s3, ddb = env
    body = b"HDR CUSTBILL\nSTERLING \xa3 LTD record with raw byte\n"
    ev = land(s3, "CUSTBILL_DEMO_ANOM.dat", body)
    result = mod.handler(ev, None)
    assert result["status"] == "staged"
    assert get_body(s3, "incoming/CUSTBILL_DEMO_ANOM.dat") == body
    assert b"\xa3" in get_body(s3, "incoming/CUSTBILL_DEMO_ANOM.dat")


def test_golden_inputs_stage_bit_for_bit(env):
    mod, s3, ddb = env
    src = golden_dir()
    present = [b for b in GOLDEN_MD5 if os.path.exists(os.path.join(src, b))]
    if not present:
        pytest.skip("golden inputs not generated; run make legacy-etl-gen-data NS=demo")
    for basename in present:
        with open(os.path.join(src, basename), "rb") as fh:
            body = fh.read()
        assert hashlib.md5(body).hexdigest() == GOLDEN_MD5[basename]
        ev = land(s3, basename, body)
        mod.handler(ev, None)
        staged = get_body(s3, f"incoming/{basename}")
        assert hashlib.md5(staged).hexdigest() == GOLDEN_MD5[basename]
        archived = get_body(s3, f"archive/{basename}")
        assert hashlib.md5(archived).hexdigest() == GOLDEN_MD5[basename]


def test_archive_copy_deterministic_key(env):
    mod, s3, ddb = env
    ev = land(s3, "CUSTBILL_DEMO_001.dat", b"payload")
    result = mod.handler(ev, None)
    assert result["archive_key"] == "archive/CUSTBILL_DEMO_001.dat"
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET, Prefix="archive/")["Contents"]]
    assert keys == ["archive/CUSTBILL_DEMO_001.dat"]


def test_landing_deleted_after_staging(env):
    mod, s3, ddb = env
    ev = land(s3, "CUSTBILL_DEMO_001.dat", b"payload")
    mod.handler(ev, None)
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET, Prefix="landing/")


def test_name_validation_quarantines_and_records(env):
    mod, s3, ddb = env
    ev = land(s3, "notes.txt", b"not a feed file")
    result = mod.handler(ev, None)
    assert result["status"] == "quarantined"
    assert get_body(s3, "quarantine/notes.txt") == b"not a feed file"
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET, Prefix="landing/")
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET, Prefix="incoming/")
    items = ledger_items(ddb)
    assert len(items) == 1
    assert items[0]["status"]["S"] == "quarantined"


def test_idempotent_redelivery(env):
    mod, s3, ddb = env
    ev = land(s3, "CUSTBILL_DEMO_001.dat", b"payload")
    first = mod.handler(ev, None)
    assert first["redelivery"] is False
    # Redelivery after completion: landing object already deleted.
    second = mod.handler(ev, None)
    assert second["redelivery"] is True
    assert get_body(s3, "incoming/CUSTBILL_DEMO_001.dat") == b"payload"
    assert get_body(s3, "archive/CUSTBILL_DEMO_001.dat") == b"payload"
    assert len(ledger_items(ddb)) == 1

    # Redelivery mid-flight (landing object still present): copies rerun,
    # state converges, still no duplicate ledger rows.
    ev2 = land(s3, "CUSTBILL_DEMO_002.dat", b"other")
    mod.handler(ev2, None)
    s3.put_object(Bucket=BUCKET, Key="landing/CUSTBILL_DEMO_002.dat", Body=b"other")
    third = mod.handler(ev2, None)
    assert third["redelivery"] is True
    assert get_body(s3, "incoming/CUSTBILL_DEMO_002.dat") == b"other"
    assert len(ledger_items(ddb)) == 2


def test_idempotent_redelivery_without_etag(env):
    mod, s3, ddb = env
    ev = land(s3, "CUSTBILL_DEMO_001.dat", b"payload")
    mod.handler(ev, None)
    # Redelivery whose event carries no etag, after the landed object is gone:
    # the ledger (not S3 404-vs-403 semantics) identifies the completed run.
    second = mod.handler(event_for("CUSTBILL_DEMO_001.dat"), None)
    assert second["redelivery"] is True
    assert get_body(s3, "incoming/CUSTBILL_DEMO_001.dat") == b"payload"
    assert len(ledger_items(ddb)) == 1


def test_errors_surface_on_missing_object_without_etag(env):
    mod, s3, ddb = env
    # No etag in the event, no landed object, no ledger row: must raise.
    with pytest.raises(Exception):
        mod.handler(event_for("CUSTBILL_DEMO_404.dat"), None)


def test_errors_surface_on_missing_object(env):
    mod, s3, ddb = env
    with pytest.raises(Exception):
        mod.handler(event_for("CUSTBILL_DEMO_404.dat", "d41d8cd98f00b204e9800998ecf8427e"), None)


def test_errors_surface_on_malformed_event(env):
    mod, s3, ddb = env
    with pytest.raises(ValueError):
        mod.handler({"detail": {}}, None)
    with pytest.raises(ValueError):
        mod.handler(event_for("nested/CUSTBILL.dat"), None)
    with pytest.raises(ValueError):
        ev = event_for("CUSTBILL_DEMO_001.dat")
        ev["detail"]["object"]["key"] = "incoming/CUSTBILL_DEMO_001.dat"
        mod.handler(ev, None)
