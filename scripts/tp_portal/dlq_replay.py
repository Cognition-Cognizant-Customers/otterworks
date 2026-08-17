#!/usr/bin/env python3
"""Replay portal feedback messages from the namespace-scoped DLQ."""

import argparse
import json

import boto3


def replay(prefix: str, region: str) -> dict[str, int]:
    sqs = boto3.client("sqs", region_name=region)
    main = sqs.get_queue_url(QueueName=f"{prefix}-feedback-events")["QueueUrl"]
    dlq = sqs.get_queue_url(QueueName=f"{prefix}-feedback-events-dlq")["QueueUrl"]
    redriven = 0
    while True:
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
            sqs.send_message(QueueUrl=main, MessageBody=message["Body"])
            sqs.delete_message(QueueUrl=dlq, ReceiptHandle=message["ReceiptHandle"])
            redriven += 1
    attributes = sqs.get_queue_attributes(
        QueueUrl=dlq, AttributeNames=["ApproximateNumberOfMessages"]
    )["Attributes"]
    return {
        "redriven": redriven,
        "remaining": int(attributes.get("ApproximateNumberOfMessages", "0")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    print(json.dumps(replay(args.prefix, args.region), sort_keys=True))


if __name__ == "__main__":
    main()
