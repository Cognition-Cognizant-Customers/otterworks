"""Shared helpers for the `files` workload (DynamoDB metadata -> Atlas).

Connection defaults follow the rest of the tech-partnerships tooling:
`AWS_ENDPOINT_URL` / `AWS_REGION` for the LocalStack DynamoDB source and
`MONGODB_ATLAS_URI` for the Atlas target. The target database is derived from
the namespace (`ow_tp_<ns>`) so a namespace is a self-contained slice.
"""

import hashlib
import os
import re

DYNAMO_TABLE = "otterworks-file-metadata"
SOURCE_TABLE = f"dynamodb:{DYNAMO_TABLE}"
COLLECTION = "files"

NS_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def valid_ns(ns: str) -> bool:
    return bool(NS_PATTERN.fullmatch(ns))


def db_name(ns: str) -> str:
    return os.getenv("MONGODB_DB") or f"ow_tp_{ns}"


def mongo_client():
    """Client for the Atlas cluster; the URI carries the credentials."""
    from pymongo import MongoClient

    uri = os.getenv("MONGODB_ATLAS_URI")
    if not uri:
        raise SystemExit("MONGODB_ATLAS_URI is not set")
    return MongoClient(uri, serverSelectionTimeoutMS=30_000)


def mongo_collection(ns: str, client=None):
    client = client or mongo_client()
    return client[db_name(ns)][COLLECTION]


def dynamo_table(name: str = DYNAMO_TABLE):
    import boto3

    return boto3.resource(
        "dynamodb",
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    ).Table(name)


class Checksum:
    """Order-independent, constant-memory checksum over a set of lines.

    Same definition as the seed manifest's (`testdata/legacy/README.md`): the
    sum of each line's md5 digest modulo 2**128, rendered as 32 hex chars, so
    lines fold in any order without materializing the whole set.
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


def log(msg: str) -> None:
    print(f"[mongo-files] {msg}", flush=True)
