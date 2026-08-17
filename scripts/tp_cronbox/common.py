"""Shared deterministic helpers for the Cron Box fixture estate."""

from __future__ import annotations

import hashlib
import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ANCHOR = datetime(2026, 1, 15, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
LOGS = STATE / "logs"
GOLDEN = Path(__file__).resolve().parents[2] / "testdata/legacy/golden/cronbox"
AWS_ENDPOINT = os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566")
AWS_REGION = "us-east-1"
ACCESS_KEY = "123456789012"
SECRET_KEY = "cronbox-local-secret"
BUCKETS = (
    "otterworks-file-storage",
    "otterworks-file-quarantine",
    "otterworks-data-lake",
    "otterworks-audit-archive",
)


def rng(ns: str, label: str = "") -> random.Random:
    seed = int(hashlib.sha256(ns.encode()).hexdigest()[:8], 16)
    return random.Random(f"{seed}:{label}")


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def date_value(run_date: str) -> datetime:
    return datetime.strptime(run_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def clients():
    import boto3

    kwargs = {
        "endpoint_url": AWS_ENDPOINT,
        "region_name": AWS_REGION,
        "aws_access_key_id": ACCESS_KEY,
        "aws_secret_access_key": SECRET_KEY,
    }
    return (
        boto3.client("s3", **kwargs),
        boto3.resource("dynamodb", **kwargs),
        boto3.client("sqs", **kwargs),
    )


def dynamo_scan_all(table):
    items = []
    kwargs = {}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        kwargs = {"ExclusiveStartKey": page["LastEvaluatedKey"]}


def s3_objects_all(s3, bucket):
    objects = []
    kwargs = {"Bucket": bucket}
    while True:
        page = s3.list_objects_v2(**kwargs)
        objects.extend(page.get("Contents", []))
        if not page.get("IsTruncated"):
            return objects
        kwargs["ContinuationToken"] = page["NextContinuationToken"]


def s3_keys_all(s3, bucket):
    return [item["Key"] for item in s3_objects_all(s3, bucket)]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pg_kwargs(dbname: str = "otterworks_analytics") -> dict:
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "dbname": dbname,
        "user": os.getenv("DB_USER", "otterworks"),
        "password": os.getenv("DB_PASSWORD", "otterworks_dev"),
    }


def days(run_date: str, count: int):
    anchor = date_value(run_date)
    return [anchor - timedelta(days=i) for i in range(count)]
