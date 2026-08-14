"""Step Functions task: parse one CUSTBILL fixed-width file.

Writes the pipe-delimited output to s3://<bucket>/parsed/<ns>/<base>.psv and
one item per record to the DynamoDB billing table (on-demand), then archives
the input under archive/<ns>/.
"""

import os

import boto3

from custbill import parse_file

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

TABLE_NAME = os.environ["TABLE_NAME"]


def handler(event, context):
    bucket = event["bucket"]
    key = event["key"]
    ns = event["ns"]
    base = key.split("/")[-1].rsplit(".dat", 1)[0]

    raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    records = parse_file(raw)

    parsed_key = f"parsed/{ns}/{base}.psv"
    body = ("\n".join(records) + "\n") if records else ""
    s3.put_object(Bucket=bucket, Key=parsed_key, Body=body.encode("utf-8"))

    table = dynamodb.Table(TABLE_NAME)
    with table.batch_writer() as batch:
        for i, rec in enumerate(records, start=1):
            cust, name, dt, amt, ccy, rt = rec.split("|")
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
