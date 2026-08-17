#!/usr/bin/env python3
"""Replay portal feedback messages from the namespace-scoped DLQ."""

import argparse
import json

import boto3


def queue_depth(sqs, queue_url: str) -> int:
    attributes = sqs.get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["ApproximateNumberOfMessages"]
    )["Attributes"]
    return int(attributes.get("ApproximateNumberOfMessages", "0"))


def replay(prefix: str, region: str) -> dict[str, int]:
    sqs = boto3.client("sqs", region_name=region)
    main = sqs.get_queue_url(QueueName=f"{prefix}-feedback-events")["QueueUrl"]
    dlq = sqs.get_queue_url(QueueName=f"{prefix}-feedback-events-dlq")["QueueUrl"]
    snapshot = queue_depth(sqs, dlq)
    redriven = 0
    skipped = 0
    seen_ids: set[str] = set()
    while redriven < snapshot:
        response = sqs.receive_message(
            QueueUrl=dlq,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=10,
            VisibilityTimeout=30,
        )
        messages = response.get("Messages", [])
        if not messages:
            break
        for message in messages:
            if redriven >= snapshot:
                skipped += 1
                continue
            message_id = message["MessageId"]
            if message_id in seen_ids:
                skipped += 1
                continue
            seen_ids.add(message_id)
            sqs.send_message(QueueUrl=main, MessageBody=message["Body"])
            sqs.delete_message(QueueUrl=dlq, ReceiptHandle=message["ReceiptHandle"])
            redriven += 1
    remaining = queue_depth(sqs, dlq)
    if redriven >= snapshot:
        skipped = max(skipped, remaining)
    return {
        "redriven": redriven,
        "skipped": skipped,
        "remaining": remaining,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    print(json.dumps(replay(args.prefix, args.region), sort_keys=True))


if __name__ == "__main__":
    main()
