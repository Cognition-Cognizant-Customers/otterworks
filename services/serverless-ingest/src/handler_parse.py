"""Step Functions task: parse one CUSTBILL fixed-width file.

Writes the pipe-delimited output to s3://<bucket>/parsed/<ns>/<base>.psv and
one item per record to the DynamoDB billing table (on-demand), then archives
the input under archive/<ns>/.
"""

import os

import boto3
from boto3.dynamodb.conditions import Key

from custbill import parse_file

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

TABLE_NAME = os.environ["TABLE_NAME"]


def _delete_existing(table, ns: str, rec_prefix: str) -> None:
    kwargs = {
        "KeyConditionExpression": Key("ns").eq(ns) & Key("rec").begins_with(rec_prefix),
        "ProjectionExpression": "#ns, rec",
        "ExpressionAttributeNames": {"#ns": "ns"},
    }
    with table.batch_writer() as batch:
        while True:
            page = table.query(**kwargs)
            for item in page.get("Items", []):
                batch.delete_item(Key={"ns": item["ns"], "rec": item["rec"]})
            lek = page.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek


def handler(event, context):
    bucket = event["bucket"]
    key = event["key"]
    ns = event["ns"]
    base = key.split("/")[-1].rsplit(".dat", 1)[0]

    # latin-1 is byte-preserving, matching the legacy chain's byte-oriented
    # cut/awk processing (no validation; dirty records pass through)
    raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("latin-1")
    records = parse_file(raw)

    parsed_key = f"parsed/{ns}/{base}.psv"
    body = ("\n".join(records) + "\n") if records else ""
    s3.put_object(Bucket=bucket, Key=parsed_key, Body=body.encode("latin-1"))

    table = dynamodb.Table(TABLE_NAME)
    # Clear any rows from a previous version of this file first: a re-sent
    # file with fewer records must not leave orphaned high-index items behind
    _delete_existing(table, ns, f"{base}#")
    with table.batch_writer() as batch:
        for i, rec in enumerate(records, start=1):
            fields = rec.split("|")
            cust, name, dt, amt, ccy, rt = (fields + [""] * 6)[:6]
            batch.put_item(
                Item={
                    "ns": ns,
                    "rec": f"{base}#{i:05d}",
                    "cust_id": cust,
                    "cust_name": name,
                    "bill_date": dt,
                    "amount": amt,
                    "currency": ccy,
                    "rec_type": rt,
                }
            )

    archive_key = f"archive/{ns}/{base}.dat"
    s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": key}, Key=archive_key)

    return {"bucket": bucket, "ns": ns, "parsed_key": parsed_key, "records": len(records)}
