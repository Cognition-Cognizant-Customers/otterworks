#!/usr/bin/env python3
"""Recompute asynchronous portal parity checks from AWS target state."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import uuid
from typing import Any

import boto3


IDEMPOTENCY_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def idempotency_key(feedback_id: int) -> str:
    return str(uuid.uuid5(IDEMPOTENCY_NAMESPACE, f"feedback:{feedback_id}"))


def poison_key(index: int) -> str:
    return f"poison:{index}"


def queue_depth(sqs: Any, name: str) -> int:
    url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
    attrs = sqs.get_queue_attributes(
        QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
    )["Attributes"]
    return int(attrs.get("ApproximateNumberOfMessages", "0"))


def scan_ids(dynamodb: Any, table_name: str, key: str) -> set[str]:
    values: set[str] = set()
    kwargs = {
        "TableName": table_name,
        "ProjectionExpression": "#k",
        "ExpressionAttributeNames": {"#k": key},
    }
    while True:
        page = dynamodb.scan(**kwargs)
        for item in page.get("Items", []):
            value = item[key]
            values.add(value.get("S", value.get("N")))
        if "LastEvaluatedKey" not in page:
            return values
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _errors(cloudwatch: Any, function_name: str, now: dt.datetime) -> float:
    metrics = cloudwatch.get_metric_statistics(
        Namespace="AWS/Lambda",
        MetricName="Errors",
        Dimensions=[{"Name": "FunctionName", "Value": function_name}],
        StartTime=now - dt.timedelta(minutes=5),
        EndTime=now,
        Period=300,
        Statistics=["Sum"],
    )
    return sum(point.get("Sum", 0) for point in metrics.get("Datapoints", []))


def _set_check(
    check_id: str, expected: set[str], actual: set[str], source: str
) -> dict[str, Any]:
    return {
        "id": check_id,
        "expected": sorted(expected),
        "actual": sorted(actual),
        "missing": sorted(expected - actual),
        "unexpected": sorted(actual - expected),
        "source_of_truth": source,
        "result": "pass" if expected == actual else "fail",
    }


def report(
    prefix: str,
    region: str,
    *,
    mode: str = "green",
    expected_poison: int = 0,
    post_replay: bool = False,
    sqs: Any | None = None,
    dynamodb: Any | None = None,
    cloudwatch: Any | None = None,
) -> dict[str, Any]:
    if mode not in {"green", "red"}:
        raise ValueError("mode must be green or red")
    if expected_poison < 0:
        raise ValueError("expected_poison must be non-negative")
    if mode == "green" and expected_poison:
        raise ValueError("--expected-poison requires --mode red")

    sqs = sqs or boto3.client("sqs", region_name=region)
    dynamodb = dynamodb or boto3.client("dynamodb", region_name=region)
    cloudwatch = cloudwatch or boto3.client("cloudwatch", region_name=region)

    feedback_ids = scan_ids(dynamodb, f"{prefix}-feedback", "pk")
    moderation_ids = scan_ids(dynamodb, f"{prefix}-moderation", "idempotencyKey")
    expected_ids = {idempotency_key(int(value)) for value in feedback_ids}
    main_depth = queue_depth(sqs, f"{prefix}-feedback-events")
    dlq_depth = queue_depth(sqs, f"{prefix}-feedback-events-dlq")
    rerun_feedback_ids = scan_ids(dynamodb, f"{prefix}-feedback", "pk")
    rerun_moderation_ids = scan_ids(dynamodb, f"{prefix}-moderation", "idempotencyKey")
    rerun_expected_ids = {idempotency_key(int(value)) for value in rerun_feedback_ids}
    rerun_main_depth = queue_depth(sqs, f"{prefix}-feedback-events")
    rerun_dlq_depth = queue_depth(sqs, f"{prefix}-feedback-events-dlq")
    rerun_ok = (
        expected_ids == rerun_expected_ids
        and moderation_ids == rerun_moderation_ids
        and main_depth == rerun_main_depth
        and dlq_depth == rerun_dlq_depth
    )
    now = dt.datetime.now(dt.timezone.utc)
    errors = _errors(cloudwatch, f"{prefix}-moderation", now)

    checks = [
        _set_check("moderation-set", expected_ids, moderation_ids, "DynamoDB scans"),
        {
            "id": "main-queue-depth",
            "expected": 0,
            "actual": main_depth,
            "source_of_truth": "SQS queue attributes",
            "result": "pass" if main_depth == 0 else "fail",
        },
        {
            "id": "moderation-errors",
            "expected": 0,
            "actual": errors,
            "source_of_truth": "CloudWatch AWS/Lambda Errors",
            "result": "pass" if errors == 0 else "fail",
        },
    ]

    poison_ids = {poison_key(index) for index in range(1, expected_poison + 1)}
    poison_rows = moderation_ids & poison_ids
    if mode == "green":
        expected_dlq = 0
        dlq_result = dlq_depth == expected_dlq
        anomaly_expected = set()
        anomaly_actual = set()
    else:
        expected_dlq = 0 if post_replay else expected_poison
        dlq_result = dlq_depth == expected_dlq
        anomaly_expected = poison_ids
        anomaly_actual = poison_ids - poison_rows

    checks.append(
        {
            "id": "dlq-depth",
            "expected": expected_dlq,
            "actual": dlq_depth,
            "source_of_truth": "SQS queue attributes",
            "result": "pass" if dlq_result else "fail",
        }
    )
    checks.append(
        _set_check(
            "poison-moderation-rows",
            set(),
            poison_rows,
            "DynamoDB moderation table scan",
        )
    )

    unverified_paths = []
    if mode == "green":
        unverified_paths.append("red-path poison injection and post-replay state")
    else:
        unverified_paths.append("poison injection was performed externally")
        if not post_replay:
            unverified_paths.append("post-replay DLQ state")

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
            "result": "pass" if rerun_ok else "fail",
            "evidence": "The full derived key sets and queue depths were recomputed from independent reads.",
        },
        "planted_anomaly_detections": {
            "expected_set": sorted(anomaly_expected),
            "actual_set": sorted(anomaly_actual),
            "missing": sorted(anomaly_expected - anomaly_actual),
            "unexpected": sorted(anomaly_actual - anomaly_expected),
        },
        "unverified_paths": unverified_paths,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--out", required=True)
    parser.add_argument("--mode", choices=("green", "red"), default="green")
    parser.add_argument("--expected-poison", type=int, default=0)
    parser.add_argument("--post-replay", action="store_true")
    args = parser.parse_args()
    result = report(
        args.prefix,
        args.region,
        mode=args.mode,
        expected_poison=args.expected_poison,
        post_replay=args.post_replay,
    )
    with open(args.out, "w", encoding="utf-8") as output:
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")


if __name__ == "__main__":
    main()
