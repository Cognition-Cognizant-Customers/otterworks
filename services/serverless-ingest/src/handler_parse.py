"""Step Functions Parse-state Lambda handler."""

from __future__ import annotations

import boto3

from custbill import parse_body, parse_records, trailer_count
from pipeline import env, namespace_from_key, parsed_key

s3 = boto3.client("s3")
billing_table = None


def _validate_segment(value: str, label: str) -> None:
    if not isinstance(value, str) or not value or "/" in value or value in {".", ".."}:
        raise ValueError(f"{label} must be a single path segment")


def _get_billing_table():
    global billing_table
    if billing_table is None:
        billing_table = boto3.resource("dynamodb").Table(env("TABLE_NAME"))
    return billing_table


def handler(event: dict, context: object) -> dict[str, object]:
    key = event["key"]
    ns = event["ns"] if "ns" in event and event["ns"] is not None else namespace_from_key(key)
    filename = event["filename"]
    _validate_segment(ns, "namespace")
    _validate_segment(filename, "filename")
    output_filename = filename[:-4] + ".psv" if filename.endswith(".dat") else filename
    bucket = env("BUCKET")

    source = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    output, records = parse_body(source)
    output_key = parsed_key(ns, output_filename)
    s3.put_object(Bucket=bucket, Key=output_key, Body=output)

    with _get_billing_table().batch_writer() as batch:
        for line_number, fields in enumerate(parse_records(source), start=1):
            cust_id, cust_name, bill_date, amount, currency, rec_type = fields
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
