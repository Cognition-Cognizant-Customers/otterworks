"""Step Functions Parse-state Lambda handler."""

from __future__ import annotations

import os

import boto3

from custbill import parse_body, trailer_count
from pipeline import env, namespace_from_key, parsed_key

s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
try:
    billing_table = boto3.resource(
        "dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")
    ).Table(env("TABLE_NAME"))
except RuntimeError:
    billing_table = None


def handler(event: dict, context: object) -> dict[str, object]:
    key = event["key"]
    ns = event.get("ns") or namespace_from_key(key)
    filename = event["filename"]
    bucket = env("BUCKET")

    source = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    output, records = parse_body(source)
    output_key = parsed_key(ns, filename[:-4] + ".psv" if filename.endswith(".dat") else filename)
    s3.put_object(Bucket=bucket, Key=output_key, Body=output)

    table = billing_table
    if table is None:
        table = boto3.resource(
            "dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")
        ).Table(env("TABLE_NAME"))
    lines = output.decode("utf-8").splitlines()
    with table.batch_writer() as batch:
        for line_number, line in enumerate(lines, start=1):
            cust_id, cust_name, bill_date, amount, currency, rec_type = line.split("|", 5)
            batch.put_item(
                Item={
                    "ns": ns,
                    "rec": f"{filename}#{line_number:06d}",
                    "cust_id": cust_id,
                    "cust_name": cust_name,
                    "bill_date": bill_date,
                    "amount": amount,
                    "currency": currency,
                    "rec_type": rec_type,
                    "source_key": key,
                }
            )

    expected_trailer = trailer_count(source)
    return {
        "ns": ns,
        "parsed_key": output_key,
        "records": records,
        "trailer_count": expected_trailer,
        "trailer_match": expected_trailer is not None and expected_trailer == records,
    }
