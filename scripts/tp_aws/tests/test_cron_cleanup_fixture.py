"""LocalStack proof for the cron-cleanup contract."""

from __future__ import annotations

import importlib.util
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import urlopen

import boto3
import pytest
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError

ROOT = Path(__file__).resolve().parents[3]
HANDLER_PATH = ROOT / "infrastructure/lambda/ow-tp-orphan-quarantine/handler.py"
ENDPOINT = os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566")
REGION = "us-east-1"
STORAGE_BUCKET = "ow-tp-fixture-cron-cleanup-storage"
QUARANTINE_BUCKET = "ow-tp-fixture-cron-cleanup-quarantine"
METADATA_TABLE = "ow-tp-fixture-cron-cleanup-metadata"
AUDIT_TABLE = "ow-tp-fixture-cron-cleanup-audit"
RUN_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _clients():
    kwargs = {
        "endpoint_url": ENDPOINT,
        "region_name": REGION,
        "aws_access_key_id": "123456789012",
        "aws_secret_access_key": "cronbox-local-secret",
    }
    return boto3.client("s3", **kwargs), boto3.resource("dynamodb", **kwargs)


def _event(key: str, event_time: datetime) -> dict:
    return {
        "version": "0",
        "id": f"fixture-{key}",
        "source": "aws.s3",
        "detail-type": "Object Created",
        "time": event_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "detail": {
            "bucket": {"name": STORAGE_BUCKET},
            "object": {"key": key},
        },
    }


def _load_handler():
    os.environ.update(
        {
            "AWS_ENDPOINT_URL": ENDPOINT,
            "AWS_ACCESS_KEY_ID": "123456789012",
            "AWS_SECRET_ACCESS_KEY": "cronbox-local-secret",
            "AWS_REGION": REGION,
            "STORAGE_BUCKET": STORAGE_BUCKET,
            "QUARANTINE_BUCKET": QUARANTINE_BUCKET,
            "METADATA_TABLE": METADATA_TABLE,
            "AUDIT_TABLE": AUDIT_TABLE,
            "FILES_PREFIX": "files/",
            "QUARANTINE_PREFIX": "quarantined",
            "RECHECK_DELAY_SECONDS": "0",
        }
    )
    spec = importlib.util.spec_from_file_location("cron_cleanup_handler", HANDLER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wait_for_localstack(s3, dynamodb):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{ENDPOINT}/_localstack/init/ready", timeout=2) as response:
                ready = json.load(response)
            with urlopen(f"{ENDPOINT}/_localstack/health", timeout=2) as response:
                health = json.load(response)
            statuses = health.get("services", {})
            if (
                ready.get("completed") is True
                and statuses.get("s3") in {"running", "available"}
                and statuses.get("dynamodb") in {"running", "available"}
            ):
                return
        except (BotoCoreError, EndpointConnectionError, OSError, ValueError):
            pass
        time.sleep(1)
    pytest.fail(f"LocalStack did not finish initialization at {ENDPOINT}")


@pytest.fixture(scope="module")
def estate():
    try:
        s3, dynamodb = _clients()
        s3.list_buckets()
    except (BotoCoreError, ClientError, EndpointConnectionError) as error:
        pytest.fail(
            f"LocalStack is required for the fixture proof at {ENDPOINT}: {error}"
        )
    _wait_for_localstack(s3, dynamodb)

    for bucket in (STORAGE_BUCKET, QUARANTINE_BUCKET):
        try:
            s3.create_bucket(Bucket=bucket)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in (
                "BucketAlreadyExists",
                "BucketAlreadyOwnedByYou",
            ):
                raise

    try:
        dynamodb.create_table(
            TableName=METADATA_TABLE,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        dynamodb.Table(METADATA_TABLE).wait_until_exists()
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "ResourceInUseException":
            raise
    try:
        dynamodb.create_table(
            TableName=AUDIT_TABLE,
            KeySchema=[{"AttributeName": "object_key", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "object_key", "AttributeType": "S"}
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        dynamodb.Table(AUDIT_TABLE).wait_until_exists()
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "ResourceInUseException":
            raise

    for bucket in (STORAGE_BUCKET, QUARANTINE_BUCKET):
        objects = s3.list_objects_v2(Bucket=bucket).get("Contents", [])
        if objects:
            s3.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": item["Key"]} for item in objects]},
            )
    for table in (dynamodb.Table(METADATA_TABLE), dynamodb.Table(AUDIT_TABLE)):
        with table.batch_writer() as batch:
            scan = table.scan()
            for item in scan.get("Items", []):
                key_name = "id" if table.name == METADATA_TABLE else "object_key"
                batch.delete_item(Key={key_name: item[key_name]})

    metadata = dynamodb.Table(METADATA_TABLE)
    for i in range(72):
        key = f"files/demo/file-{i:03d}.bin"
        s3.put_object(Bucket=STORAGE_BUCKET, Key=key, Body=f"file-{i}-demo".encode())
        metadata.put_item(
            Item={
                "id": f"file-{i:03d}",
                "s3_key": key,
                "file_name": "Fichier Δ ☕" if i == 7 else f"File {i}",
                "extra_attribute": "ignored",
            }
        )
    orphan_keys = [f"files/demo/orphan-{i:03d}.bin" for i in range(4)]
    for key in orphan_keys:
        s3.put_object(Bucket=STORAGE_BUCKET, Key=key, Body=b"orphan")
    reverse_item = {
        "id": "reverse-orphan",
        "s3_key": "files/demo/missing-reverse.bin",
        "file_name": "reverse",
    }
    metadata.put_item(Item=reverse_item)
    metadata.put_item(Item={"id": "empty-reference", "s3_key": ""})

    yield {
        "s3": s3,
        "dynamodb": dynamodb,
        "metadata": metadata,
        "reverse_item": reverse_item,
        "orphan_keys": orphan_keys,
    }


def test_event_quarantine_set_and_replay(estate):
    module = _load_handler()
    event_time = datetime.now(timezone.utc)
    for key in estate["orphan_keys"]:
        assert module.handler(_event(key, event_time), None)["status"] == "quarantined"

    quarantine_keys = [
        item["Key"]
        for page in estate["s3"]
        .get_paginator("list_objects_v2")
        .paginate(Bucket=QUARANTINE_BUCKET, Prefix=f"quarantined/{RUN_DATE}/")
        for item in page.get("Contents", [])
    ]
    assert set(quarantine_keys) == {
        f"quarantined/{RUN_DATE}/{key}" for key in estate["orphan_keys"]
    }

    storage_keys = {
        item["Key"]
        for page in estate["s3"]
        .get_paginator("list_objects_v2")
        .paginate(Bucket=STORAGE_BUCKET, Prefix="files/")
        for item in page.get("Contents", [])
    }
    assert storage_keys == {f"files/demo/file-{i:03d}.bin" for i in range(72)}

    reverse_after = estate["metadata"].get_item(Key={"id": "reverse-orphan"})["Item"]
    assert reverse_after == estate["reverse_item"]

    audit_before = (
        estate["dynamodb"]
        .Table(AUDIT_TABLE)
        .get_item(Key={"object_key": estate["orphan_keys"][0]})["Item"]
    )
    assert audit_before == {
        "object_key": estate["orphan_keys"][0],
        "decision": "quarantined",
        "quarantine_key": f"quarantined/{RUN_DATE}/{estate['orphan_keys'][0]}",
        "detected_at": event_time.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "trigger_source": "event",
        "size_bytes": 6,
        "freed_bytes": 6,
        "unresolvable_references": 1,
    }
    replay = module.handler(_event(estate["orphan_keys"][0], event_time), None)
    audit_after = (
        estate["dynamodb"]
        .Table(AUDIT_TABLE)
        .get_item(Key={"object_key": estate["orphan_keys"][0]})["Item"]
    )
    assert replay["status"] == "already_quarantined"
    assert audit_after == audit_before


def test_recreated_quarantined_key_is_processed_again(estate):
    module = _load_handler()
    source_key = estate["orphan_keys"][0]
    previous_audit = (
        estate["dynamodb"]
        .Table(AUDIT_TABLE)
        .get_item(Key={"object_key": source_key})["Item"]
    )
    recreated_body = b"recreated"
    estate["s3"].put_object(Bucket=STORAGE_BUCKET, Key=source_key, Body=recreated_body)

    event_time = datetime.now(timezone.utc) + timedelta(days=1)
    result = module.handler(_event(source_key, event_time), None)

    assert result["status"] == "quarantined"
    assert result["quarantine_key"] != previous_audit["quarantine_key"]
    assert (
        estate["s3"]
        .get_object(Bucket=QUARANTINE_BUCKET, Key=result["quarantine_key"])["Body"]
        .read()
        == recreated_body
    )
    with pytest.raises(ClientError):
        estate["s3"].head_object(Bucket=STORAGE_BUCKET, Key=source_key)

    current_audit = (
        estate["dynamodb"]
        .Table(AUDIT_TABLE)
        .get_item(Key={"object_key": source_key})["Item"]
    )
    assert current_audit["quarantine_key"] == result["quarantine_key"]
    assert current_audit["detected_at"] != previous_audit["detected_at"]


def test_unicode_and_empty_reference_are_safe(estate):
    module = _load_handler()
    source_key = "files/demo/space Δ.bin"
    body = b"\x00\xffopaque-\xe2\x98\x95"
    estate["s3"].put_object(Bucket=STORAGE_BUCKET, Key=source_key, Body=body)

    encoded_event_key = quote_plus(source_key)
    result = module.handler(_event(encoded_event_key, datetime.now(timezone.utc)), None)
    destination_key = f"quarantined/{RUN_DATE}/{source_key}"
    assert result["quarantine_key"] == destination_key
    assert (
        estate["s3"]
        .get_object(Bucket=QUARANTINE_BUCKET, Key=destination_key)["Body"]
        .read()
        == body
    )
    with pytest.raises(ClientError):
        estate["s3"].head_object(Bucket=STORAGE_BUCKET, Key=source_key)


def test_retained_rows_are_rechecked_by_event_and_sweep(estate):
    module = _load_handler()
    event_time = datetime.now(timezone.utc)
    event_key = "files/demo/file-000.bin"
    sweep_key = "files/demo/file-001.bin"

    assert module.handler(_event(event_key, event_time), None)["status"] == "retained"
    retained_before = (
        estate["dynamodb"]
        .Table(AUDIT_TABLE)
        .get_item(Key={"object_key": event_key})["Item"]
    )
    retained_replay = module.handler(
        _event(event_key, event_time + timedelta(minutes=1)), None
    )
    assert retained_replay["status"] == "retained"
    retained_after = (
        estate["dynamodb"]
        .Table(AUDIT_TABLE)
        .get_item(Key={"object_key": event_key})["Item"]
    )
    assert retained_after == retained_before

    estate["metadata"].delete_item(Key={"id": "file-000"})
    event_result = module.handler(
        _event(event_key, event_time + timedelta(minutes=2)), None
    )
    assert event_result["status"] == "quarantined"
    event_audit = (
        estate["dynamodb"]
        .Table(AUDIT_TABLE)
        .get_item(Key={"object_key": event_key})["Item"]
    )
    assert event_audit["detected_at"] == retained_before["detected_at"]
    assert event_audit["decision"] == "quarantined"

    assert module.handler(_event(sweep_key, event_time), None)["status"] == "retained"
    sweep_retained = (
        estate["dynamodb"]
        .Table(AUDIT_TABLE)
        .get_item(Key={"object_key": sweep_key})["Item"]
    )
    estate["metadata"].delete_item(Key={"id": "file-001"})

    summary = module.handler({"mode": "reconcile"}, None)
    assert summary["status"] == "reconciled"
    assert summary["orphans_quarantined"] == 1
    assert summary["object_key"].startswith("__sweep__/")
    datetime.fromisoformat(
        summary["object_key"].split("/", 1)[1].replace("Z", "+00:00")
    )
    sweep_audit = (
        estate["dynamodb"]
        .Table(AUDIT_TABLE)
        .get_item(Key={"object_key": sweep_key})["Item"]
    )
    assert sweep_audit["decision"] == "quarantined"
    assert sweep_audit["detected_at"] == sweep_retained["detected_at"]


def test_reconcile_writes_zero_orphan_summary(estate):
    module = _load_handler()
    summary = module.handler({"mode": "reconcile"}, None)
    assert summary["status"] == "reconciled"
    assert summary["decision"] == "sweep_summary"
    assert summary["objects_scanned"] == 70
    assert summary["referenced_objects"] == 70
    assert summary["orphans_found"] == 0
    assert summary["orphans_quarantined"] == 0
    assert summary["freed_bytes"] == 0
    assert summary["unresolvable_references"] == 1
    assert summary["reverse_orphans"] == 1

    stored = (
        estate["dynamodb"]
        .Table(AUDIT_TABLE)
        .get_item(Key={"object_key": summary["object_key"]})["Item"]
    )
    assert stored == summary
    assert (
        estate["metadata"].get_item(Key={"id": "reverse-orphan"})["Item"]
        == estate["reverse_item"]
    )
