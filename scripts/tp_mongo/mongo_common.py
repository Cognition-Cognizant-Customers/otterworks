"""Shared helpers for the tech-partnerships MongoDB migration units.

The target connection is taken from a single environment variable, `MONGO_URI`,
defaulting to the shared local `docker-compose.mongo-fixture.yml` fixture, with
the database name from `MONGO_DB` (default `ow_tp_<ns>`). Nothing here is
Atlas-specific: pointing `MONGO_URI` at a cluster is the only change needed to
run the same migration and recon against Atlas.
"""

from __future__ import annotations

import hashlib
import os
import re
from decimal import Decimal
from typing import Any

import boto3
from bson.binary import Binary
from pymongo import MongoClient

DB_PREFIX = "ow_tp"
DYNAMO_TABLE = "otterworks-file-metadata"
FILES_BUCKET = "otterworks-files"
FILES_COLLECTION = "files"
QUARANTINE_COLLECTION = "files_quarantine"
# Key segment the legacy seed uses to plant metadata whose object was never
# written; it is how the golden baseline validator enumerates the orphans.
ORPHAN_KEY_SEGMENT = "/missing/"
NS_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def mongo_uri() -> str:
    """The target, or the shared local fixture on whichever port it publishes."""
    fixture = f"mongodb://localhost:{os.getenv('MONGO_FIXTURE_PORT', '27017')}"
    return os.getenv("MONGO_URI") or fixture


def validate_ns(ns: str) -> str:
    if not NS_PATTERN.fullmatch(ns):
        raise SystemExit("--ns must match [A-Za-z0-9_]+ and must not be empty")
    return ns


def database_name(ns: str) -> str:
    return os.getenv("MONGO_DB") or f"{DB_PREFIX}_{validate_ns(ns)}"


def mongo_client() -> MongoClient:
    return MongoClient(mongo_uri(), serverSelectionTimeoutMS=30_000, tz_aware=True)


def aws_client(service: str):
    return boto3.client(
        service,
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


def aws_resource(service: str):
    return boto3.resource(
        service,
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


def bson_value(value: Any) -> Any:
    """Convert a boto3 DynamoDB attribute value to its BSON counterpart.

    Numbers stay numbers (integral values become BSON integers, fractional ones
    doubles), binary stays binary, sets become arrays, and maps/lists recurse.
    Nothing is stringified.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return Binary(bytes(value))
    if isinstance(value, set):
        return [bson_value(v) for v in sorted(value, key=repr)]
    if isinstance(value, list):
        return [bson_value(v) for v in value]
    if isinstance(value, dict):
        return {k: bson_value(v) for k, v in value.items()}
    return value


class Checksum:
    """Order-independent md5 fold, byte-compatible with the legacy manifest.

    Mirrors `testdata/legacy/legacy_common.Checksum` so a checksum recomputed
    from the migrated collection is directly comparable to the seed manifest.
    """

    _MOD = 1 << 128

    def __init__(self) -> None:
        self._total = 0
        self.count = 0

    def add(self, line: str) -> None:
        digest = hashlib.md5(line.encode()).digest()
        self._total = (self._total + int.from_bytes(digest, "big")) % self._MOD
        self.count += 1

    def hexdigest(self) -> str:
        return f"{self._total:032x}"
