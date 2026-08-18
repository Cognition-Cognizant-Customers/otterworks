#!/usr/bin/env python3
"""Operator DLQ replay: drain the feedback events DLQ back onto the main queue.

The demo beat is "poison message captured, fixed, replayed, nothing lost":
after the operator fixes the underlying fault, this command moves every DLQ
message back to the main queue (send-then-delete, so a crash mid-replay can
duplicate but never lose a message — the consumer dedupes on eventId) and
reports how many were redriven.

Live (parent-run):
  python3 scripts/tp_portal/replay_dlq.py \
    --dlq-url  https://sqs.us-east-1.amazonaws.com/<acct>/ow-tp-portal-<ns>-feedback-events-dlq \
    --queue-url https://sqs.us-east-1.amazonaws.com/<acct>/ow-tp-portal-<ns>-feedback-events

Fixture (LocalStack): add --endpoint-url http://localhost:4570
"""

import argparse
import json
import sys

import boto3


def make_client(endpoint_url):
    kwargs = {"region_name": "us-east-1"}
    if endpoint_url:
        kwargs.update(
            endpoint_url=endpoint_url,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
    return boto3.client("sqs", **kwargs)


def replay(sqs, dlq_url, queue_url, max_messages):
    redriven = 0
    while max_messages is None or redriven < max_messages:
        response = sqs.receive_message(
            QueueUrl=dlq_url, MaxNumberOfMessages=10, WaitTimeSeconds=1
        )
        messages = response.get("Messages", [])
        if not messages:
            break
        for message in messages:
            if max_messages is not None and redriven >= max_messages:
                break
            sqs.send_message(QueueUrl=queue_url, MessageBody=message["Body"])
            sqs.delete_message(QueueUrl=dlq_url, ReceiptHandle=message["ReceiptHandle"])
            redriven += 1
    return redriven


def depth(sqs, queue_url):
    attrs = sqs.get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["ApproximateNumberOfMessages"]
    )["Attributes"]
    return int(attrs["ApproximateNumberOfMessages"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dlq-url", required=True)
    parser.add_argument("--queue-url", required=True)
    parser.add_argument("--endpoint-url", default=None, help="LocalStack fixture endpoint")
    parser.add_argument("--max-messages", type=int, default=None)
    args = parser.parse_args()

    sqs = make_client(args.endpoint_url)
    redriven = replay(sqs, args.dlq_url, args.queue_url, args.max_messages)
    report = {
        "redriven": redriven,
        "dlq_depth_after": depth(sqs, args.dlq_url),
    }
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
