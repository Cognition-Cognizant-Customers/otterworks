#!/usr/bin/env python3
"""Reset the ow-tp portal DynamoDB tables to an empty (fresh) state.

Only touches tables under the given prefix, so it is safe to rerun and cannot
affect anything outside this demo's namespace.

Usage:
  reset_tables.py [--prefix ow-tp-portal-demo] [--region us-east-1] \
      [--endpoint-url http://localhost:4566]   # LocalStack fixture tables
"""
from __future__ import annotations

import argparse

import boto3

TABLE_KEYS = {
    "announcements": "pk",
    "preferences": "userId",
    "feedback": "pk",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="ow-tp-portal-demo")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--endpoint-url", default=None,
                        help="Non-AWS endpoint (e.g. LocalStack) for fixture runs")
    args = parser.parse_args()

    extra = {}
    if args.endpoint_url:
        extra = {"endpoint_url": args.endpoint_url,
                 "aws_access_key_id": "test", "aws_secret_access_key": "test"}
    dynamodb = boto3.resource("dynamodb", region_name=args.region, **extra)
    for context, key in TABLE_KEYS.items():
        table = dynamodb.Table(f"{args.prefix}-{context}")
        deleted = 0
        scan = table.scan(ProjectionExpression="#k", ExpressionAttributeNames={"#k": key})
        while True:
            with table.batch_writer() as batch:
                for item in scan["Items"]:
                    batch.delete_item(Key={key: item[key]})
                    deleted += 1
            if "LastEvaluatedKey" not in scan:
                break
            scan = table.scan(
                ProjectionExpression="#k",
                ExpressionAttributeNames={"#k": key},
                ExclusiveStartKey=scan["LastEvaluatedKey"],
            )
        print(f"{table.name}: deleted {deleted} items")


if __name__ == "__main__":
    main()
