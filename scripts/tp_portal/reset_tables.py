#!/usr/bin/env python3
"""Reset the ow-tp portal DynamoDB tables to an empty (fresh) state.

Only touches tables under the given prefix, so it is safe to rerun and cannot
affect anything outside this demo's namespace.

Usage:
  reset_tables.py [--prefix ow-tp-portal-demo] [--region us-east-1]
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
    args = parser.parse_args()

    dynamodb = boto3.resource("dynamodb", region_name=args.region)
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
