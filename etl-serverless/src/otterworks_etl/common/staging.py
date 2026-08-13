"""S3 staging for intermediate datasets between Step Functions states.

Step Functions payloads are capped at 256 KB, so extract tasks write their
output to a staging prefix in S3 and pass only the object key downstream.
"""

import gzip
import json
from typing import Any

from otterworks_etl.common.config import client, env


def staging_key(pipeline: str, execution_id: str, name: str) -> str:
    return f"etl-staging/{pipeline}/{execution_id}/{name}.json.gz"


def write_staged(pipeline: str, execution_id: str, name: str, data: Any) -> str:
    key = staging_key(pipeline, execution_id, name)
    body = gzip.compress(json.dumps(data, default=str).encode("utf-8"))
    client("s3").put_object(Bucket=env("DATA_LAKE_BUCKET"), Key=key, Body=body)
    return key


def list_staged(pipeline: str, execution_id: str, prefix: str) -> list[str]:
    full_prefix = f"etl-staging/{pipeline}/{execution_id}/{prefix}"
    paginator = client("s3").get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=env("DATA_LAKE_BUCKET"), Prefix=full_prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return sorted(keys)


def read_staged(key: str) -> Any:
    response = client("s3").get_object(Bucket=env("DATA_LAKE_BUCKET"), Key=key)
    return json.loads(gzip.decompress(response["Body"].read()).decode("utf-8"))
