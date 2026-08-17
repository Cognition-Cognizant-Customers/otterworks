#!/usr/bin/env python3
"""Seed the deterministic local estate consumed by the five legacy cron jobs."""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import urllib.error
import urllib.request
from collections import Counter
from datetime import timedelta
from pathlib import Path

import psycopg2
from common import (
    ANCHOR,
    BUCKETS,
    clients,
    date_value,
    days,
    dynamo_scan_all,
    iso,
    pg_kwargs,
    s3_keys_all,
    write_json,
)

ANALYTICS = "otterworks-analytics-events"
AUDIT = "otterworks-audit-events"
FILES = "otterworks-file-metadata"
QUEUE = "otterworks-analytics"


def ensure_table(dynamo, name, definitions, schema):
    try:
        dynamo.meta.client.describe_table(TableName=name)
    except dynamo.meta.client.exceptions.ResourceNotFoundException:
        dynamo.meta.client.create_table(
            TableName=name,
            AttributeDefinitions=definitions,
            KeySchema=schema,
            BillingMode="PAY_PER_REQUEST",
        )
        dynamo.meta.client.get_waiter("table_exists").wait(TableName=name)


def clear_table(table):
    with table.batch_writer() as batch:
        for item in dynamo_scan_all(table):
            keys = {
                k["AttributeName"]: item[k["AttributeName"]] for k in table.key_schema
            }
            batch.delete_item(Key=keys)


def clear_bucket(s3, bucket):
    try:
        keys = s3_keys_all(s3, bucket)
        for offset in range(0, len(keys), 1000):
            s3.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": k} for k in keys[offset : offset + 1000]]},
            )
    except s3.exceptions.NoSuchBucket:
        s3.create_bucket(Bucket=bucket)


def clear_meilisearch():
    base_url = "http://localhost:7700"
    for index_uid in ("documents", "files"):
        request = urllib.request.Request(
            f"{base_url}/indexes/{index_uid}",
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                if response.status not in (200, 202, 204):
                    raise RuntimeError(
                        f"unexpected MeiliSearch delete status {response.status}"
                    )
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise


def seed_events(ns, run_date, sqs, dynamo):
    queue_url = sqs.create_queue(QueueName=QUEUE)["QueueUrl"]
    sqs.purge_queue(QueueUrl=queue_url)
    users = [f"user-{i:03d}" for i in range(12)]
    event_types = [
        "document_created",
        "document_edited",
        "comment_added",
        "file_uploaded",
        "file_shared",
        "file_deleted",
    ]
    fields = ["ownerId", "editedBy", "authorId", "deletedBy", "userId"]
    messages = []
    for i in range(240):
        event = {
            "event_id": f"{ns}-sqs-{i:04d}",
            "eventType": event_types[i % len(event_types)],
            "timestamp": f"{run_date}T{(i % 24):02d}:{(i * 7) % 60:02d}:00Z",
            "title": "Réunion café ☕" if i % 17 == 0 else f"Legacy title {i}",
            "name": "Δelta" if i % 19 == 0 else f"Name {i}",
            "documentId": f"doc-{i % 25:03d}",
            "fileId": f"file-{i % 18:03d}",
            "sizeBytes": 1000 + i,
        }
        if i % 11:
            event[fields[i % len(fields)]] = users[i % len(users)]
        messages.append(event)
    for i in range(8):
        messages.append(f"not-json-{i}")
    for event in messages:
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=event
            if isinstance(event, str)
            else json.dumps(event, ensure_ascii=False, sort_keys=True),
        )

    table = dynamo.Table(ANALYTICS)
    clear_table(table)
    items = []
    for day, count in ((-1, 8), (0, 32), (1, 8)):
        d = date_value(run_date) + timedelta(days=day)
        for i in range(count):
            items.append(
                {
                    "event_id": f"{ns}-ddb-{day + 1}-{i:03d}",
                    "event_date": f"{d:%Y-%m-%d}T{i:02d}:00:00Z",
                    "eventType": event_types[i % len(event_types)],
                    "timestamp": iso(d + timedelta(hours=i)),
                    "userId": users[i % len(users)],
                    "documentId": f"doc-{i % 25:03d}",
                    "fileId": f"file-{i % 18:03d}",
                    "sizeBytes": 2000 + i,
                }
            )
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
    valid = [event for event in messages if isinstance(event, dict)]
    user_fields = {
        field: [event["event_id"] for event in valid if field in event]
        for field in fields
    }
    unknown = [
        event["event_id"]
        for event in valid
        if not any(field in event for field in fields)
    ]
    ddb_by_day = {
        f"{date_value(run_date) + timedelta(days=day):%Y-%m-%d}": count
        for day, count in ((-1, 8), (0, 32), (1, 8))
    }
    return {
        "sqs_messages": len(messages),
        "sqs_valid_events": len(valid),
        "sqs_malformed_messages": len(messages) - len(valid),
        "sqs_unknown_user_events": len(unknown),
        "sqs_user_field_event_ids": user_fields,
        "sqs_unknown_user_event_ids": unknown,
        "dynamodb_events": len(items),
        "dynamodb_run_date_events": ddb_by_day[run_date],
        "dynamodb_boundary_excluded": sum(ddb_by_day.values()) - ddb_by_day[run_date],
    }


def reshape_audit_table(dynamo):
    client = dynamo.meta.client
    try:
        description = client.describe_table(TableName=AUDIT)["Table"]
        keys = description["KeySchema"]
        if keys == [
            {"AttributeName": "event_id", "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"},
        ]:
            return
        client.delete_table(TableName=AUDIT)
        client.get_waiter("table_not_exists").wait(TableName=AUDIT)
    except client.exceptions.ResourceNotFoundException:
        pass
    client.create_table(
        TableName=AUDIT,
        AttributeDefinitions=[
            {"AttributeName": "event_id", "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "event_id", "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=AUDIT)


def seed_postgres(ns, run_date):
    admin = psycopg2.connect(**{**pg_kwargs("postgres"), "dbname": "postgres"})
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname='otterworks_analytics'")
        if not cur.fetchone():
            try:
                cur.execute("CREATE DATABASE otterworks_analytics")
            except psycopg2.errors.InsufficientPrivilege:
                admin.rollback()
                subprocess.run(
                    [
                        "sudo",
                        "-u",
                        "postgres",
                        "createdb",
                        "-O",
                        "otterworks",
                        "otterworks_analytics",
                    ],
                    check=True,
                )
    admin.close()
    conn = psycopg2.connect(**pg_kwargs())
    conn.autocommit = True
    document_count = 0
    summary_count = 0
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS analytics_daily_summary (
            report_date date PRIMARY KEY, active_users integer NOT NULL, active_documents integer NOT NULL,
            active_files integer NOT NULL, total_events integer NOT NULL, documents_created integer NOT NULL,
            documents_edited integer NOT NULL, comments_added integer NOT NULL, files_uploaded integer NOT NULL,
            files_shared integer NOT NULL, files_deleted integer NOT NULL, bytes_uploaded bigint NOT NULL,
            updated_at timestamptz NOT NULL)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS cronbox_documents (
            id text PRIMARY KEY, title text NOT NULL, content text NOT NULL, owner_id text NOT NULL,
            tags jsonb NOT NULL, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL)""")
        cur.execute("TRUNCATE analytics_daily_summary, cronbox_documents")
        for i in range(125):
            cur.execute(
                "INSERT INTO cronbox_documents VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    f"doc-{i:03d}",
                    "Δocument ☕" if i == 4 else f"Document {i}",
                    f"Content for document {i}",
                    f"user-{i % 12:03d}",
                    json.dumps([f"tag-{i % 5}", "legacy"]),
                    date_value(run_date) - timedelta(days=100 - i % 30),
                    date_value(run_date) - timedelta(days=i % 7),
                ),
            )
            document_count += 1
        for d in reversed(days(run_date, 30)):
            n = 20 + (d.day % 11)
            cur.execute(
                """INSERT INTO analytics_daily_summary VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    d.date(),
                    12,
                    25,
                    18,
                    n,
                    n // 5,
                    n // 4,
                    n // 6,
                    n // 7,
                    n // 8,
                    n // 9,
                    n * 1200,
                    d,
                ),
            )
            summary_count += 1
    conn.close()
    return {"documents": document_count, "summary_days": summary_count}


def seed_files(ns, run_date, s3, dynamo):
    metadata = dynamo.Table(FILES)
    clear_table(metadata)
    referenced = []
    mime_types = (
        "text/plain",
        "application/pdf",
        "image/png",
        "application/json",
    )
    folders = ("folder-1", "folder-2", "folder-3")
    mime_distribution = Counter()
    folder_distribution = Counter()
    tag_distribution = Counter()
    for i in range(72):
        key = f"files/{ns}/file-{i:03d}.bin"
        referenced.append(key)
        mime_type = mime_types[i % len(mime_types)]
        folder_id = folders[i % len(folders)]
        tags = ["legacy", f"tag-{i % 5}"]
        file_name = "Fichier Δ ☕" if i == 7 else f"File {i}"
        mime_distribution[mime_type] += 1
        folder_distribution[folder_id] += 1
        for tag in tags:
            tag_distribution[tag] += 1
        s3.put_object(
            Bucket="otterworks-file-storage", Key=key, Body=f"file-{i}-{ns}".encode()
        )
        metadata.put_item(
            Item={
                "id": f"file-{i:03d}",
                "s3_key": key,
                "file_name": file_name,
                "owner_id": f"user-{i % 12:03d}",
                "mime_type": mime_type,
                "folder_id": folder_id,
                "size_bytes": i + 10,
                "tags": tags,
                "created_at": iso(date_value(run_date) - timedelta(days=i)),
                "updated_at": iso(date_value(run_date)),
            }
        )
    orphan_keys = []
    for i in range(4):
        orphan_key = f"files/{ns}/orphan-{i:03d}.bin"
        orphan_keys.append(orphan_key)
        s3.put_object(
            Bucket="otterworks-file-storage",
            Key=orphan_key,
            Body=b"orphan",
        )
    reverse_ids = ["reverse-orphan"]
    metadata.put_item(
        Item={
            "id": "reverse-orphan",
            "s3_key": f"files/{ns}/missing-reverse.bin",
            "file_name": "reverse",
            "owner_id": "user-000",
            "mime_type": "text/plain",
            "folder_id": "folder-1",
            "size_bytes": 7,
        }
    )
    orphan_keys = [f"files/{ns}/orphan-{i:03d}.bin" for i in range(4)]
    return {
        "referenced_objects": len(referenced),
        "orphan_objects": len(orphan_keys),
        "orphan_keys": orphan_keys,
        "reverse_orphans": len(reverse_ids),
        "reverse_orphan_ids": reverse_ids,
        "mime_type_distribution": dict(sorted(mime_distribution.items())),
        "folder_distribution": dict(sorted(folder_distribution.items())),
        "tag_distribution": dict(sorted(tag_distribution.items())),
        "unicode_file": {"id": "file-007", "file_name": "Fichier Δ ☕"},
    }


def seed_history(ns, run_date, s3):
    count = 0
    for offset, d in enumerate(days(run_date, 30)):
        if offset == 13:
            continue
        lines = []
        for i in range(6):
            lines.append(
                json.dumps(
                    {
                        "user_id": f"user-{i:03d}",
                        "total": 2 + i + offset,
                        "actions": {"document_edited": 1 + i % 3, "file_uploaded": 1},
                    },
                    sort_keys=True,
                )
            )
        raw = ("\n".join(lines) + "\n").encode()
        body = gzip.compress(raw, mtime=0)
        s3.put_object(
            Bucket="otterworks-data-lake",
            Key=f"analytics/daily/year={d:%Y}/month={d:%m}/day={d:%d}/top_users.jsonl.gz",
            Body=body,
        )
        count += 1
    missing_date = (date_value(run_date) - timedelta(days=13)).strftime("%Y-%m-%d")
    return {
        "history_objects": count,
        "expected_history_days": 29,
        "missing_history_day": missing_date,
    }


def seed_audit(ns, run_date, dynamo):
    table = dynamo.Table(AUDIT)
    clear_table(table)
    cutoff = date_value(run_date) - timedelta(days=90)
    records = []
    for i in range(80):
        ts = cutoff - timedelta(days=1 + i % 100, seconds=i)
        records.append(
            {
                "event_id": f"{ns}-audit-{i:04d}",
                "timestamp": iso(ts),
                "actor": f"user-{i % 12:03d}",
                "action": "document.updated",
                "target_id": f"doc-{i % 25:03d}",
                "raw_payload": json.dumps({"i": i}, sort_keys=True),
            }
        )
    for i, seconds in enumerate((-1, 0, 1)):
        records.append(
            {
                "event_id": f"{ns}-boundary-{i}",
                "timestamp": iso(cutoff + timedelta(seconds=seconds)),
                "actor": "user-000",
                "action": "boundary",
                "target_id": "boundary",
                "raw_payload": "{}",
            }
        )
    for i in range(20):
        records.append(
            {
                "event_id": f"{ns}-new-{i:04d}",
                "timestamp": iso(cutoff + timedelta(days=2 + i)),
                "actor": "user-001",
                "action": "document.viewed",
                "target_id": "doc-001",
                "raw_payload": "{}",
            }
        )
    with table.batch_writer() as batch:
        for item in records:
            batch.put_item(Item=item)
    cutoff_iso = iso(cutoff)
    archivable = [
        item["event_id"] for item in records if item["timestamp"] < cutoff_iso
    ]
    boundary_ids = {
        item["event_id"]: item["timestamp"]
        for item in records
        if item["action"] == "boundary"
    }
    return {
        "audit_total": len(records),
        "audit_archivable": len(archivable),
        "archivable_event_ids": archivable,
        "boundary_event_ids": boundary_ids,
        "boundary_archivable_event_id": f"{ns}-boundary-0",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ns", required=True)
    parser.add_argument("--run-date", required=True)
    args = parser.parse_args()
    s3, dynamo, sqs = clients()
    clear_meilisearch()
    for bucket in BUCKETS:
        clear_bucket(s3, bucket)
    ensure_table(
        dynamo,
        ANALYTICS,
        [{"AttributeName": "event_id", "AttributeType": "S"}],
        [{"AttributeName": "event_id", "KeyType": "HASH"}],
    )
    reshape_audit_table(dynamo)
    ensure_table(
        dynamo,
        FILES,
        [{"AttributeName": "id", "AttributeType": "S"}],
        [{"AttributeName": "id", "KeyType": "HASH"}],
    )
    stores = {
        "events": seed_events(args.ns, args.run_date, sqs, dynamo),
        "postgres": seed_postgres(args.ns, args.run_date),
        "files": seed_files(args.ns, args.run_date, s3, dynamo),
        "history": seed_history(args.ns, args.run_date, s3),
        "audit": seed_audit(args.ns, args.run_date, dynamo),
    }
    manifest = {
        "namespace": args.ns,
        "anchor": iso(ANCHOR),
        "run_date": args.run_date,
        "stores": stores,
        "planted_anomalies": {
            "malformed_sqs_bodies": {
                "count": stores["events"]["sqs_malformed_messages"],
                "message_bodies": [
                    f"not-json-{i}"
                    for i in range(stores["events"]["sqs_malformed_messages"])
                ],
            },
            "unknown_user_events": {
                "count": stores["events"]["sqs_unknown_user_events"],
                "event_ids": stores["events"]["sqs_unknown_user_event_ids"],
            },
            "dynamodb_adjacent_day_events": {
                "count": stores["events"]["dynamodb_boundary_excluded"],
                "run_date_count": stores["events"]["dynamodb_run_date_events"],
            },
            "s3_orphan_objects": {
                "count": stores["files"]["orphan_objects"],
                "keys": stores["files"]["orphan_keys"],
            },
            "reverse_metadata_orphan": {
                "count": stores["files"]["reverse_orphans"],
                "ids": stores["files"]["reverse_orphan_ids"],
            },
            "missing_history_day": stores["history"]["missing_history_day"],
            "audit_boundaries": {
                "event_ids": stores["audit"]["boundary_event_ids"],
                "archivable_event_id": stores["audit"]["boundary_archivable_event_id"],
            },
        },
    }
    output = (
        Path(__file__).parents[2]
        / "testdata"
        / "legacy"
        / "golden"
        / "cronbox"
        / args.ns
        / "seed-manifest.json"
    )
    write_json(output, manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
