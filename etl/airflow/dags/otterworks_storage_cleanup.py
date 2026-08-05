"""Daily orphaned S3 object cleanup DAG.

Migrated from etl/scripts/storage_cleanup_daily.py per etl/ETL_UPGRADE_GUIDE.md.

Tasks:
    [list_s3_objects, list_metadata_references]
        >> find_orphaned_objects >> move_to_quarantine >> generate_storage_report

Configuration:
    Airflow Connections:
        aws_default          -- AWS credentials (S3Hook, DynamoDBHook)
    Airflow Variables:
        otterworks_file_storage_bucket   (default: otterworks-file-storage)
        otterworks_quarantine_bucket     (default: otterworks-file-quarantine)
        otterworks_data_lake_bucket      (default: otterworks-data-lake)
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator
from airflow.providers.amazon.aws.hooks.dynamodb import DynamoDBHook
from airflow.providers.amazon.aws.hooks.s3 import S3Hook

logger = logging.getLogger(__name__)

AWS_CONN_ID = "aws_default"

FILES_PREFIX = "files/"
QUARANTINE_PREFIX = "quarantined"
DYNAMODB_TABLE_NAME = "otterworks-file-metadata"
S3_STANDARD_USD_PER_GB_MONTH = 0.023


# ---------------------------------------------------------------------------
# Pure transform functions (unit-tested in etl/airflow/tests/)
# ---------------------------------------------------------------------------

def build_quarantine_key(quarantine_prefix, ds, source_key):
    """Quarantine destination key: '<prefix>/<YYYY-MM-DD>/<source_key>'."""
    return "%s/%s/%s" % (quarantine_prefix, ds, source_key)


def build_report_key(ds):
    """Data-lake report key: 'reports/storage-cleanup/<YYYY-MM-DD>/report.json'."""
    return "reports/storage-cleanup/%s/report.json" % ds


def find_orphans(all_objects, referenced_keys):
    """Return objects whose key is not referenced in the metadata table."""
    referenced = set(referenced_keys)
    return [obj for obj in all_objects if obj["key"] not in referenced]


def build_report(ds, generated_at, total_objects, total_size_bytes,
                 orphaned_count, orphaned_bytes, moved_count, failed_count,
                 quarantine_bucket):
    savings_gb = orphaned_bytes / (1024 ** 3)
    return {
        "report_type": "storage_cleanup",
        "report_date": ds,
        "generated_at": generated_at,
        "inventory": {
            "total_objects": total_objects,
            "total_size_bytes": total_size_bytes,
            "total_size_gb": round(total_size_bytes / (1024 ** 3), 4),
        },
        "orphans": {
            "orphaned_objects": orphaned_count,
            "orphaned_bytes": orphaned_bytes,
            "orphaned_size_gb": round(savings_gb, 4),
            "orphan_percentage": round(
                (orphaned_count / total_objects * 100) if total_objects else 0, 2
            ),
        },
        "cleanup": {
            "objects_quarantined": moved_count,
            "objects_failed": failed_count,
            "quarantine_bucket": quarantine_bucket,
        },
        "savings": {
            "storage_freed_gb": round(savings_gb, 4),
            "estimated_monthly_savings_usd": round(
                savings_gb * S3_STANDARD_USD_PER_GB_MONTH, 4
            ),
        },
    }


# ---------------------------------------------------------------------------
# Task callables
# ---------------------------------------------------------------------------

def list_s3_objects(**context):
    """Extract: list all objects under the files/ prefix in the storage bucket."""
    bucket = Variable.get(
        "otterworks_file_storage_bucket", default_var="otterworks-file-storage"
    )
    logger.info("Listing objects in s3://%s/%s", bucket, FILES_PREFIX)

    s3_hook = S3Hook(aws_conn_id=AWS_CONN_ID)
    s3_client = s3_hook.get_conn()
    paginator = s3_client.get_paginator("list_objects_v2")

    all_objects = []
    for page in paginator.paginate(Bucket=bucket, Prefix=FILES_PREFIX):
        for obj in page.get("Contents", []):
            all_objects.append({
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            })

    total_size_bytes = sum(o["size"] for o in all_objects)
    logger.info(
        "Found %d objects in S3 (%d bytes total)", len(all_objects), total_size_bytes
    )
    return all_objects


def list_metadata_references(**context):
    """Extract: scan the DynamoDB metadata table for referenced S3 keys."""
    logger.info(
        "Scanning DynamoDB table %s for metadata references...", DYNAMODB_TABLE_NAME
    )
    ddb_hook = DynamoDBHook(aws_conn_id=AWS_CONN_ID)
    table = ddb_hook.get_conn().Table(DYNAMODB_TABLE_NAME)

    referenced_keys = set()
    scan_kwargs = {"ProjectionExpression": "s3_key"}
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

    logger.info("Found %d S3 keys referenced in metadata", len(referenced_keys))
    return sorted(referenced_keys)


def find_orphaned_objects(**context):
    """Compare: identify S3 objects with no metadata reference."""
    ti = context["ti"]
    all_objects = ti.xcom_pull(task_ids="list_s3_objects")
    referenced_keys = ti.xcom_pull(task_ids="list_metadata_references")

    orphaned = find_orphans(all_objects, referenced_keys)
    orphaned_bytes = sum(o["size"] for o in orphaned)
    logger.info(
        "Found %d orphaned objects (%.2f MB)",
        len(orphaned), orphaned_bytes / (1024 * 1024),
    )
    return orphaned


def move_to_quarantine(**context):
    """Quarantine: copy orphans to the quarantine bucket, then delete originals."""
    ti = context["ti"]
    ds = context["ds"]
    orphaned = ti.xcom_pull(task_ids="find_orphaned_objects")

    file_storage_bucket = Variable.get(
        "otterworks_file_storage_bucket", default_var="otterworks-file-storage"
    )
    quarantine_bucket = Variable.get(
        "otterworks_quarantine_bucket", default_var="otterworks-file-quarantine"
    )

    if not orphaned:
        logger.info("No orphaned objects to quarantine")
        return {"moved_count": 0, "failed_count": 0}

    logger.info("Moving %d orphaned objects to quarantine...", len(orphaned))
    s3_client = S3Hook(aws_conn_id=AWS_CONN_ID).get_conn()

    moved_count = 0
    failed_count = 0
    for obj in orphaned:
        source_key = obj["key"]
        dest_key = build_quarantine_key(QUARANTINE_PREFIX, ds, source_key)
        try:
            s3_client.copy_object(
                Bucket=quarantine_bucket,
                Key=dest_key,
                CopySource={"Bucket": file_storage_bucket, "Key": source_key},
                MetadataDirective="COPY",
            )
            s3_client.delete_object(Bucket=file_storage_bucket, Key=source_key)
            moved_count += 1
        except Exception:
            logger.warning("Failed to quarantine %s", source_key, exc_info=True)
            failed_count += 1

    logger.info(
        "Quarantined %d objects (%d failed) to s3://%s/%s/%s/",
        moved_count, failed_count, quarantine_bucket, QUARANTINE_PREFIX, ds,
    )
    return {"moved_count": moved_count, "failed_count": failed_count}


def generate_storage_report(**context):
    """Report: write the storage savings report JSON to the data lake bucket."""
    ti = context["ti"]
    ds = context["ds"]
    all_objects = ti.xcom_pull(task_ids="list_s3_objects")
    orphaned = ti.xcom_pull(task_ids="find_orphaned_objects")
    quarantine_result = ti.xcom_pull(task_ids="move_to_quarantine")

    quarantine_bucket = Variable.get(
        "otterworks_quarantine_bucket", default_var="otterworks-file-quarantine"
    )
    data_lake_bucket = Variable.get(
        "otterworks_data_lake_bucket", default_var="otterworks-data-lake"
    )

    orphaned_bytes = sum(o["size"] for o in orphaned)
    report = build_report(
        ds=ds,
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        total_objects=len(all_objects),
        total_size_bytes=sum(o["size"] for o in all_objects),
        orphaned_count=len(orphaned),
        orphaned_bytes=orphaned_bytes,
        moved_count=quarantine_result["moved_count"],
        failed_count=quarantine_result["failed_count"],
        quarantine_bucket=quarantine_bucket,
    )

    report_key = build_report_key(ds)
    S3Hook(aws_conn_id=AWS_CONN_ID).load_string(
        string_data=json.dumps(report, indent=2),
        key=report_key,
        bucket_name=data_lake_bucket,
        replace=True,
    )
    logger.info(
        "Storage cleanup report written to s3://%s/%s: "
        "%d orphans quarantined, %.4f GB freed, ~$%.4f/month saved",
        data_lake_bucket, report_key,
        quarantine_result["moved_count"],
        report["savings"]["storage_freed_gb"],
        report["savings"]["estimated_monthly_savings_usd"],
    )


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

default_args = {
    "owner": "data-team",
    "depends_on_past": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=1),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
    "email_on_failure": True,
}

with DAG(
    dag_id="otterworks_storage_cleanup",
    description="Daily orphaned S3 object cleanup with quarantine and savings report",
    schedule="30 2 * * *",
    start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["etl", "storage", "cleanup"],
) as dag:
    list_s3_objects_task = PythonOperator(
        task_id="list_s3_objects",
        python_callable=list_s3_objects,
    )
    list_metadata_references_task = PythonOperator(
        task_id="list_metadata_references",
        python_callable=list_metadata_references,
    )
    find_orphaned_objects_task = PythonOperator(
        task_id="find_orphaned_objects",
        python_callable=find_orphaned_objects,
    )
    move_to_quarantine_task = PythonOperator(
        task_id="move_to_quarantine",
        python_callable=move_to_quarantine,
    )
    generate_storage_report_task = PythonOperator(
        task_id="generate_storage_report",
        python_callable=generate_storage_report,
    )

    (
        [list_s3_objects_task, list_metadata_references_task]
        >> find_orphaned_objects_task
        >> move_to_quarantine_task
        >> generate_storage_report_task
    )
