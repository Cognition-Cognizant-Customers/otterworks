#!/usr/bin/env python3
# storage_cleanup_daily.py - Daily orphaned S3 object cleanup
# Originally Python 2.7, minimally ported to Python 3 in 2021
# Lists S3 objects, compares with DynamoDB metadata, quarantines orphans,
# generates storage savings report
#
# Owner: Jake (data-team@otterworks.dev) -- Jake left mid-2020
# TODO ETL-091: Add S3 lifecycle rules instead of manual cleanup (2019-11-20)
# TODO ETL-156: Parallelize S3 listing for large buckets (deferred Q1 2020)
# TODO ETL-203: Add dry-run mode for testing (never implemented)

import configparser
import json
import sys
from datetime import datetime, timezone

import boto3


FILES_PREFIX = "files/"
QUARANTINE_PREFIX = "quarantined"
DYNAMODB_TABLE_NAME = "otterworks-file-metadata"


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


def list_all_objects(s3_client, bucket):
    """Return every object under FILES_PREFIX as {key, size, last_modified} dicts."""
    print("[%s] Listing objects in s3://%s/%s" % (now_str(), bucket, FILES_PREFIX))

    all_objects = []
    paginator = s3_client.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix=FILES_PREFIX):
        for obj in page.get("Contents", []):
            all_objects.append({
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            })

    print("[%s] Found %d objects in S3 (%d bytes total)" % (
        now_str(), len(all_objects), sum(o["size"] for o in all_objects)
    ))
    return all_objects


def fetch_referenced_keys(credentials):
    """Return the set of S3 keys still referenced by DynamoDB file metadata."""
    print("[%s] Scanning DynamoDB table %s for metadata references..." % (
        now_str(), DYNAMODB_TABLE_NAME
    ))

    dynamodb = boto3.resource("dynamodb", **credentials)
    table = dynamodb.Table(DYNAMODB_TABLE_NAME)

    referenced_keys = set()
    scan_kwargs = {
        "ProjectionExpression": "s3_key",
    }

    while True:
        response = table.scan(**scan_kwargs)
        for item in response.get("Items", []):
            s3_key = item.get("s3_key", "")
            if s3_key:
                referenced_keys.add(s3_key)

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    print("[%s] Found %d S3 keys referenced in metadata" % (now_str(), len(referenced_keys)))
    return referenced_keys


def find_orphans(all_objects, referenced_keys):
    """Return (orphaned objects, total orphaned bytes)."""
    orphaned = [obj for obj in all_objects if obj["key"] not in referenced_keys]
    orphaned_bytes = sum(obj["size"] for obj in orphaned)

    print("[%s] Found %d orphaned objects (%.2f MB)" % (
        now_str(), len(orphaned), orphaned_bytes / (1024 * 1024),
    ))
    return orphaned, orphaned_bytes


def quarantine_object(s3_client, source_bucket, quarantine_bucket, source_key, ds):
    """Copy one object into quarantine and delete the original. True when moved."""
    dest_key = "%s/%s/%s" % (QUARANTINE_PREFIX, ds, source_key)
    try:
        s3_client.copy_object(
            Bucket=quarantine_bucket,
            Key=dest_key,
            CopySource={"Bucket": source_bucket, "Key": source_key},
            MetadataDirective="COPY",
        )
        s3_client.delete_object(Bucket=source_bucket, Key=source_key)
        return True
    except Exception as e:
        print("[%s] WARNING: Failed to quarantine %s: %s" % (now_str(), source_key, str(e)))
        return False


def quarantine_orphans(s3_client, orphaned, source_bucket, quarantine_bucket, ds):
    """Move orphaned objects to quarantine. Returns (moved_count, failed_count)."""
    if not orphaned:
        print("[%s] No orphaned objects to quarantine" % now_str())
        # Still generate report even with 0 orphans
        return 0, 0

    print("[%s] Moving %d orphaned objects to quarantine..." % (now_str(), len(orphaned)))

    moved_count = 0
    for obj in orphaned:
        if quarantine_object(s3_client, source_bucket, quarantine_bucket, obj["key"], ds):
            moved_count += 1
    failed_count = len(orphaned) - moved_count

    print("[%s] Quarantined %d objects (%d failed) to s3://%s/%s/%s/" % (
        now_str(), moved_count, failed_count, quarantine_bucket, QUARANTINE_PREFIX, ds,
    ))
    return moved_count, failed_count


def build_report(ds, all_objects, orphaned, orphaned_bytes, moved_count, failed_count,
                 quarantine_bucket, savings_gb, estimated_monthly_savings):
    total_objects = len(all_objects)
    total_size_bytes = sum(o["size"] for o in all_objects)

    return {
        "report_type": "storage_cleanup",
        "report_date": ds,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "inventory": {
            "total_objects": total_objects,
            "total_size_bytes": total_size_bytes,
            "total_size_gb": round(total_size_bytes / (1024 ** 3), 4),
        },
        "orphans": {
            "orphaned_objects": len(orphaned),
            "orphaned_bytes": orphaned_bytes,
            "orphaned_size_gb": round(savings_gb, 4),
            "orphan_percentage": round(
                (len(orphaned) / total_objects * 100) if total_objects else 0, 2
            ),
        },
        "cleanup": {
            "objects_quarantined": moved_count,
            "objects_failed": failed_count,
            "quarantine_bucket": quarantine_bucket,
        },
        "savings": {
            "storage_freed_gb": round(savings_gb, 4),
            "estimated_monthly_savings_usd": estimated_monthly_savings,
        },
    }


def main():
    print("[%s] storage_cleanup_daily.py starting..." % now_str())

    config = load_config()
    credentials = aws_credentials(config)

    file_storage_bucket = config.get("s3", "file_storage_bucket")
    quarantine_bucket = config.get("s3", "quarantine_bucket")
    data_lake_bucket = config.get("s3", "data_lake_bucket")

    ds = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    s3_client = boto3.client("s3", **credentials)

    all_objects = list_all_objects(s3_client, file_storage_bucket)
    referenced_keys = fetch_referenced_keys(credentials)
    orphaned, orphaned_bytes = find_orphans(all_objects, referenced_keys)

    moved_count, failed_count = quarantine_orphans(
        s3_client, orphaned, file_storage_bucket, quarantine_bucket, ds
    )

    savings_gb = orphaned_bytes / (1024 ** 3)
    estimated_monthly_savings = round(savings_gb * 0.023, 4)

    report = build_report(
        ds, all_objects, orphaned, orphaned_bytes, moved_count, failed_count,
        quarantine_bucket, savings_gb, estimated_monthly_savings,
    )

    report_key = "reports/storage-cleanup/%s/report.json" % ds
    s3_client_report = boto3.client("s3", **credentials)
    s3_client_report.put_object(
        Bucket=data_lake_bucket,
        Key=report_key,
        Body=json.dumps(report, indent=2).encode("utf-8"),
    )

    print("[%s] Storage cleanup report: %d orphans quarantined, %.4f GB freed, ~$%.4f/month saved" % (
        now_str(), moved_count, savings_gb, estimated_monthly_savings,
    ))
    print("[%s] storage_cleanup_daily.py completed successfully" % now_str())


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[%s] FATAL: %s" % (now_str(), str(e)))
        sys.exit(1)
