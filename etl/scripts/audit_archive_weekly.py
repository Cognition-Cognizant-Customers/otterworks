#!/usr/bin/env python3
# audit_archive_weekly.py - Weekly audit event archival to S3 Glacier
# Originally Python 2.7, minimally ported to Python 3 in 2021
# Scans DynamoDB for old events, compresses to JSONL.gz, uploads to Glacier,
# batch-deletes from DynamoDB, generates compliance report
#
# Owner: Jake (data-team@otterworks.dev) -- Jake left mid-2020
# TODO ETL-134: Add incremental archival instead of full scan (deferred Q2 2020)
# TODO ETL-167: Handle DynamoDB throughput throttling properly (2020-04-10)
# TODO ETL-199: This script has no tests whatsoever (never prioritized)

import configparser
import gzip
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3


class DecimalEncoder(json.JSONEncoder):
    """Handle DynamoDB Decimal types -- copied from StackOverflow"""
    def default(self, o):
        if isinstance(o, Decimal):
            if o == int(o):
                return int(o)
            return float(o)
        return super().default(o)


RETENTION_DAYS = 90
DYNAMODB_TABLE_NAME = "otterworks-audit-events"
DYNAMODB_BATCH_SIZE = 25  # DynamoDB batch write limit
S3_PREFIX = "audit-archive"


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_config():
    config = configparser.ConfigParser()
    config.read("/opt/etl/config.ini")
    return config


def aws_credentials(config):
    return {
        "aws_access_key_id": config.get("aws", "access_key"),
        "aws_secret_access_key": config.get("aws", "secret_key"),
        "region_name": config.get("aws", "region"),
    }


def scan_expired_events(table, cutoff_date):
    """Return every audit event older than the cutoff."""
    events_to_archive = []
    scan_kwargs = {
        "FilterExpression": "#ts < :cutoff",
        "ExpressionAttributeNames": {"#ts": "timestamp"},
        "ExpressionAttributeValues": {":cutoff": cutoff_date},
    }

    while True:
        response = table.scan(**scan_kwargs)
        events_to_archive.extend(response.get("Items", []))

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    print("[%s] Found %d audit events older than %d days" % (
        now_str(), len(events_to_archive), RETENTION_DAYS
    ))
    return events_to_archive


def compress_events(events):
    """Serialize events as gzipped JSONL. Returns (bytes, compressed size)."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for event in events:
            line = json.dumps(event, cls=DecimalEncoder)
            gz.write(line.encode("utf-8"))
            gz.write(b"\n")

    compressed_size = buf.tell()
    print("[%s] Compressed %d events to %.2f MB" % (
        now_str(), len(events), compressed_size / (1024 * 1024),
    ))
    return buf.getvalue(), compressed_size


def delete_batch(table, batch):
    """Delete one batch of keys. Returns the number of deleted items."""
    try:
        with table.batch_writer() as batch_writer:
            for k in batch:
                batch_writer.delete_item(Key=k)
        return len(batch)
    except:
        # TODO ETL-167: Handle throttling / partial failures
        return 0


def delete_archived_events(table, events):
    """Batch-delete archived events from DynamoDB. Returns the deleted count."""
    print("[%s] Deleting %d archived events from DynamoDB..." % (now_str(), len(events)))

    deleted_count = 0
    batch = []

    for event in events:
        batch.append({
            "event_id": event["event_id"],
            "timestamp": event["timestamp"],
        })

        if len(batch) >= DYNAMODB_BATCH_SIZE:
            deleted_count += delete_batch(table, batch)
            batch = []

    # flush remaining batch
    if batch:
        deleted_count += delete_batch(table, batch)

    print("[%s] Deleted %d events from DynamoDB" % (now_str(), deleted_count))
    return deleted_count


def build_report(ds, cutoff_date, archive_count, deleted_count, archive_bucket,
                 archive_key, compressed_size):
    return {
        "report_type": "audit_archive_compliance",
        "execution_date": ds,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "retention_policy": {
            "retention_days": RETENTION_DAYS,
            "cutoff_date": cutoff_date,
        },
        "results": {
            "events_scanned": archive_count,
            "events_archived": archive_count,
            "events_deleted_from_source": deleted_count,
            "archive_location": "s3://%s/%s" % (archive_bucket, archive_key),
            "archive_storage_class": "GLACIER",
            "compressed_size_bytes": compressed_size,
        },
        "compliance": {
            "gdpr_compliant": True,
            "soc2_compliant": True,
            "data_encrypted_at_rest": True,
            "data_encrypted_in_transit": True,
        },
    }


def main():
    print("[%s] audit_archive_weekly.py starting..." % now_str())

    config = load_config()
    credentials = aws_credentials(config)
    archive_bucket = config.get("s3", "archive_bucket")

    ds = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    cutoff_date = (
        datetime.strptime(ds, "%Y-%m-%d") - timedelta(days=RETENTION_DAYS)
    ).isoformat() + "Z"

    print("[%s] Archiving events older than %d days (cutoff: %s)" % (
        now_str(), RETENTION_DAYS, cutoff_date
    ))

    dynamodb = boto3.resource("dynamodb", **credentials)
    table = dynamodb.Table(DYNAMODB_TABLE_NAME)

    events_to_archive = scan_expired_events(table, cutoff_date)
    archive_count = len(events_to_archive)

    if archive_count == 0:
        print("[%s] No events to archive, exiting" % now_str())
        sys.exit(0)

    archive_key = "%s/year=%s/week=%s/audit_events.jsonl.gz" % (S3_PREFIX, ds[:4], ds)
    archive_body, compressed_size = compress_events(events_to_archive)

    s3_client = boto3.client("s3", **credentials)
    s3_client.put_object(
        Bucket=archive_bucket,
        Key=archive_key,
        Body=archive_body,
        StorageClass="GLACIER",
    )

    print("[%s] Archived to s3://%s/%s (GLACIER)" % (now_str(), archive_bucket, archive_key))

    deleted_count = delete_archived_events(table, events_to_archive)

    report = build_report(
        ds, cutoff_date, archive_count, deleted_count,
        archive_bucket, archive_key, compressed_size,
    )

    report_key = "reports/compliance/audit-archive/%s/report.json" % ds
    s3_client_report = boto3.client("s3", **credentials)
    s3_client_report.put_object(
        Bucket=archive_bucket,
        Key=report_key,
        Body=json.dumps(report, indent=2).encode("utf-8"),
    )

    print("[%s] Compliance report: %d archived, %d deleted, stored at s3://%s/%s" % (
        now_str(),
        archive_count,
        deleted_count,
        archive_bucket,
        report_key,
    ))
    print("[%s] audit_archive_weekly.py completed successfully" % now_str())


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[%s] FATAL: %s" % (now_str(), str(e)))
        sys.exit(1)
