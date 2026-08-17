#!/usr/bin/env python3
"""Reset the ow-tp portal DynamoDB tables to an empty (fresh) state.

Only touches tables under the given prefix, so it is safe to rerun and cannot
affect anything outside this demo's namespace.

Usage:
  reset_tables.py [--prefix ow-tp-portal-demo] [--region us-east-1]
"""
from __future__ import annotations

import argparse
import math
import time

import boto3
from botocore.exceptions import ClientError

TABLE_KEYS = {
    "announcements": "pk",
    "preferences": "userId",
    "feedback": "pk",
    "moderation": "idempotencyKey",
}
QUEUE_WAIT_SECONDS = 10
EMPTY_RECEIVES_REQUIRED = 3
DRAIN_TIMEOUT_SECONDS = 120


def drain_queue(sqs, queue_url: str) -> int:
    deleted = 0
    empty_receives = 0
    deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        remaining_seconds = max(1, math.ceil(deadline - time.monotonic()))
        response = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=min(QUEUE_WAIT_SECONDS, remaining_seconds),
            VisibilityTimeout=0,
        )
        messages = response.get("Messages", [])
        if not messages:
            empty_receives += 1
            if empty_receives >= EMPTY_RECEIVES_REQUIRED:
                return deleted
            continue
        empty_receives = 0
        for message in messages:
            sqs.delete_message(
                QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"]
            )
            deleted += 1
    raise TimeoutError(
        f"timed out draining queue {queue_url} after {DRAIN_TIMEOUT_SECONDS}s"
    )


def reset_queue(sqs, queue_name: str) -> None:
    queue_url = sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
    try:
        sqs.purge_queue(QueueUrl=queue_url)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "PurgeQueueInProgress":
            raise
        print(f"{queue_name}: purge in progress; draining explicitly")
    deleted = drain_queue(sqs, queue_url)
    print(f"{queue_name}: drained {deleted} messages")


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

    sqs = boto3.client("sqs", region_name=args.region)
    for queue_suffix in ("feedback-events", "feedback-events-dlq"):
        reset_queue(sqs, f"{args.prefix}-{queue_suffix}")


if __name__ == "__main__":
    main()
