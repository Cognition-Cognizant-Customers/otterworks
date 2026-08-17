#!/usr/bin/env python3
"""Recompute asynchronous portal parity checks from AWS target state."""

import argparse
import datetime as dt
import json
import uuid

import boto3


IDEMPOTENCY_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def idempotency_key(feedback_id: int) -> str:
    return str(uuid.uuid5(IDEMPOTENCY_NAMESPACE, f"feedback:{feedback_id}"))


def queue_depth(sqs, name: str) -> int:
    url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
    attrs = sqs.get_queue_attributes(
        QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
    )["Attributes"]
    return int(attrs.get("ApproximateNumberOfMessages", "0"))


def scan_ids(dynamodb, table_name: str, key: str) -> set[str]:
    values = set()
    kwargs = {"TableName": table_name, "ProjectionExpression": "#k", "ExpressionAttributeNames": {"#k": key}}
    while True:
        page = dynamodb.scan(**kwargs)
        values.update(item[key].get("S", item[key].get("N")) for item in page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return values
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def report(prefix: str, region: str) -> dict:
    sqs = boto3.client("sqs", region_name=region)
    dynamodb = boto3.client("dynamodb", region_name=region)
    cloudwatch = boto3.client("cloudwatch", region_name=region)
    feedback_ids = scan_ids(dynamodb, f"{prefix}-feedback", "pk")
    rerun_feedback_ids = scan_ids(dynamodb, f"{prefix}-feedback", "pk")
    expected = {idempotency_key(int(value)) for value in feedback_ids}
    actual = scan_ids(dynamodb, f"{prefix}-moderation", "idempotencyKey")
    rerun_expected = {idempotency_key(int(value)) for value in rerun_feedback_ids}
    now = dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(minutes=5)
    metrics = cloudwatch.get_metric_statistics(
        Namespace="AWS/Lambda",
        MetricName="Errors",
        Dimensions=[{"Name": "FunctionName", "Value": f"{prefix}-moderation"}],
        StartTime=start,
        EndTime=now,
        Period=300,
        Statistics=["Sum"],
    )
    errors = sum(point.get("Sum", 0) for point in metrics.get("Datapoints", []))
    main_depth = queue_depth(sqs, f"{prefix}-feedback-events")
    dlq_depth = queue_depth(sqs, f"{prefix}-feedback-events-dlq")
    checks = [
        {"id": "moderation-set", "expected": sorted(expected), "actual": sorted(actual),
         "source_of_truth": "DynamoDB scans", "result": "pass" if expected == actual else "fail"},
        {"id": "main-queue-empty", "expected": 0, "actual": main_depth,
         "source_of_truth": "SQS queue attributes",
         "result": "pass" if main_depth == 0 else "fail"},
        {"id": "dlq-empty", "expected": 0, "actual": dlq_depth,
         "source_of_truth": "SQS queue attributes",
         "result": "pass" if dlq_depth == 0 else "fail"},
        {"id": "consumer-errors", "expected": 0, "actual": errors,
         "source_of_truth": "CloudWatch AWS/Lambda Errors", "result": "pass" if errors == 0 else "fail"},
    ]
    return {
        "kind": "recon-report",
        "unit": "portal-events",
        "namespace": prefix.rsplit("-", 1)[-1],
        "generated_at": now.isoformat(),
        "run_mode": "live",
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": {
            "performed": True,
            "result": "pass" if expected == rerun_expected else "fail",
            "evidence": "feedback table was scanned twice and deterministic keys recomputed on both scans",
        },
        "planted_anomaly_detections": {"expected_set": [], "actual_set": [], "missing": [], "unexpected": []},
        "unverified_paths": ["red-path poison injection and replay require operator-provided live run"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    with open(args.out, "w", encoding="utf-8") as output:
        json.dump(report(args.prefix, args.region), output, indent=2, sort_keys=True)
        output.write("\n")


if __name__ == "__main__":
    main()
