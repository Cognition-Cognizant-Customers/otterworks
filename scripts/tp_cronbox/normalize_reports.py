#!/usr/bin/env python3
"""Canonicalize legacy report timestamps for repeatable local captures."""

from __future__ import annotations

import json

from common import clients, s3_keys_all

ANCHOR_VALUE = "2026-01-15T00:00:00+00:00"


def main():
    s3, _, _ = clients()
    for bucket in (
        "otterworks-data-lake",
        "otterworks-audit-archive",
    ):
        for key in s3_keys_all(s3, bucket):
            if not key.endswith(".json"):
                continue
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            try:
                value = json.loads(body)
            except json.JSONDecodeError:
                continue
            if "generated_at" not in value:
                continue
            value["generated_at"] = ANCHOR_VALUE
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=(json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode(),
                ContentType="application/json",
            )


if __name__ == "__main__":
    main()
