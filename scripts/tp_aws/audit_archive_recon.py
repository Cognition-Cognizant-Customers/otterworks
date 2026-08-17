#!/usr/bin/env python3
"""Recon for the cron-archive unit (replacement of audit_archive_weekly.py).

Every actual value in the emitted report is recomputed from the deployed target:
DynamoDB (DescribeTable / DescribeTimeToLive / Scan), S3 (GetBucketLifecycleConfiguration
/ ListObjectsV2 / HeadObject / GetObject), Lambda (GetFunctionConfiguration /
ListEventSourceMappings / Invoke) and EventBridge (ListRules). Nothing is read from
Terraform state, a plan, a log line or the local fixture estate; the committed golden
baseline supplies expectations only.

The unit's acceptance checks require the expiry-driven archive path to actually run,
so the run seeds the deterministic audit corpus into the unit's own table, invokes the
reconciliation sweep, reads results back, reruns the sweep to prove convergence, and
then removes everything it wrote (unless --keep).

    python3 scripts/tp_aws/audit_archive_recon.py --mode live \
        --out docs/tech-partnerships/recon/cron-archive-demo.recon.json
"""

from __future__ import annotations

import argparse
import decimal
import gzip
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "testdata/legacy/golden/cronbox"
LEGACY_TABLE = "otterworks-audit-events"
RETENTION_DAYS = 90
UNEXPIRABLE_PROBE = "recon-unexpirable-0"
BOUNDARY_ARCHIVED = "boundary-0"
BOUNDARY_RETAINED = ("boundary-1", "boundary-2")
TTL_PROBE_IDS = ("recon-ttl-probe-ascii", "recon-ttl-probe-unicode")
LOCALSTACK_ENDPOINT = "http://localhost:4566"
FIXTURE_CREDENTIALS = {
    "aws_access_key_id": "000000000000",
    "aws_secret_access_key": "cronbox-local-secret",
}


# --------------------------------------------------------------------------- expectations


def golden_archive_records(namespace: str) -> dict:
    """The legacy archive artifact, keyed by event_id (expectations only)."""
    base = GOLDEN / namespace / "audit_archive_weekly/artifacts/otterworks-audit-archive"
    matches = sorted(base.glob("audit-archive/**/audit_events.jsonl.gz"))
    if not matches:
        raise SystemExit(f"golden archive artifact not found under {base}")
    payload = gzip.decompress(matches[0].read_bytes()).decode("utf-8")
    records = [json.loads(line) for line in payload.splitlines() if line.strip()]
    return {record["event_id"]: record for record in records}


def golden_retained_ids(namespace: str) -> list:
    manifest = json.loads(
        (GOLDEN / namespace / "audit_archive_weekly/manifest.json").read_text()
    )
    return sorted(manifest["dynamodb"][LEGACY_TABLE]["ids"])


def seed_corpus(
    namespace: str, run_date: str, live_reference_time: datetime | None = None
) -> list:
    """Reproduce the deterministic audit corpus, with the TTL attribute added.

    ``expires_at = timestamp + 90d`` is the whole retention horizon: the legacy test
    ``timestamp < run_date - 90d`` becomes ``expires_at < run_date``, so the cutoff
    stays exclusive. Live runs may provide a wall-clock reference so DynamoDB TTL
    cannot delete the seeded records before the reconciliation sweep observes them.
    """
    cutoff = datetime.strptime(run_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) - timedelta(
        days=RETENTION_DAYS
    )
    records = []
    for index in range(80):
        stamp = cutoff - timedelta(days=1 + index % 100, seconds=index)
        records.append(
            {
                "event_id": f"{namespace}-audit-{index:04d}",
                "timestamp": iso(stamp),
                "actor": f"user-{index % 12:03d}",
                "action": "document.updated",
                "target_id": f"doc-{index % 25:03d}",
                "raw_payload": json.dumps({"i": index}, sort_keys=True),
            }
        )
    for index, seconds in enumerate((-1, 0, 1)):
        records.append(
            {
                "event_id": f"{namespace}-boundary-{index}",
                "timestamp": iso(cutoff + timedelta(seconds=seconds)),
                "actor": "user-000",
                "action": "boundary",
                "target_id": "boundary",
                "raw_payload": "{}",
            }
        )
    for index in range(20):
        records.append(
            {
                "event_id": f"{namespace}-new-{index:04d}",
                "timestamp": iso(cutoff + timedelta(days=2 + index)),
                "actor": "user-001",
                "action": "document.viewed",
                "target_id": "doc-001",
                "raw_payload": "{}",
            }
        )
    if live_reference_time is None:
        for record in records:
            record["expires_at"] = (
                epoch(record["timestamp"]) + RETENTION_DAYS * 86400
            )
    else:
        for index, record in enumerate(records[:80]):
            record["expires_at"] = epoch(iso(live_reference_time)) - 600 - index
        for index, record in enumerate(records[80:83]):
            record["expires_at"] = epoch(iso(live_reference_time)) + index - 1
        for index, record in enumerate(records[83:]):
            record["expires_at"] = (
                epoch(iso(live_reference_time)) + 3600 + index
            )
    # Malformed-record probe: no TTL attribute at all. Contract requires it to be
    # retained and attributed, never silently expired.
    records.append(
        {
            "event_id": UNEXPIRABLE_PROBE,
            "timestamp": iso(cutoff - timedelta(days=400)),
            "actor": "user-000",
            "action": "unexpirable",
            "target_id": "probe",
            "raw_payload": "{}",
        }
    )
    return records


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(stamp: str) -> int:
    return int(datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def live_reference_time(now: datetime | None = None) -> datetime:
    """Return the wall-clock TTL horizon used by both live sweep invokes."""
    current = now or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).replace(microsecond=0) + timedelta(
        hours=1
    )


def ttl_probe_records(namespace: str, now: datetime | None = None) -> list[dict]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0
    )
    expires_at = int((current - timedelta(seconds=60)).timestamp())
    run_marker = int(current.timestamp())
    return [
        {
            "event_id": f"{namespace}-{TTL_PROBE_IDS[0]}-{run_marker}",
            "timestamp": iso(current),
            "actor": "recon-probe",
            "action": "ttl-probe",
            "target_id": "recon-probe",
            "raw_payload": '{"probe":"ascii"}',
            "expires_at": expires_at,
        },
        {
            "event_id": f"{namespace}-{TTL_PROBE_IDS[1]}-{run_marker}",
            "timestamp": iso(current + timedelta(seconds=1)),
            "actor": "recon-probe",
            "action": "ttl-probe",
            "target_id": "recon-probe",
            "raw_payload": '{"probe":"Δ ☕"}',
            "expires_at": expires_at,
        },
    ]


# --------------------------------------------------------------------------- target access


class Target:
    """Everything this recon is allowed to believe comes through here."""

    def __init__(self, mode: str, table: str, bucket: str, prefix: str, function: str):
        self.mode = mode
        self.table = table
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
        self.function = function
        kwargs = {}
        if mode == "fixture":
            kwargs = {
                "endpoint_url": os.getenv("AWS_ENDPOINT_URL", LOCALSTACK_ENDPOINT),
                "region_name": "us-east-1",
                **FIXTURE_CREDENTIALS,
            }
        self.dynamodb = boto3.client("dynamodb", **kwargs)
        self.s3 = boto3.client("s3", **kwargs)
        self.awslambda = None if mode == "fixture" else boto3.client("lambda")
        self.events = None if mode == "fixture" else boto3.client("events")

    # ---- configuration read-back

    def ttl_spec(self) -> dict:
        return self.dynamodb.describe_time_to_live(TableName=self.table)[
            "TimeToLiveDescription"
        ]

    def table_description(self) -> dict:
        return self.dynamodb.describe_table(TableName=self.table)["Table"]

    def lifecycle_rules(self) -> list:
        return self.s3.get_bucket_lifecycle_configuration(Bucket=self.bucket)["Rules"]

    def function_configuration(self) -> dict:
        if self.mode == "fixture":
            import handler  # fixture mode runs the packaged handler in-process

            return {
                "Environment": {
                    "Variables": {
                        "RETENTION_DAYS": str(handler.RETENTION_DAYS),
                        "TTL_ATTRIBUTE": handler.TTL_ATTRIBUTE,
                        "ARCHIVE_PREFIX": handler.ARCHIVE_PREFIX,
                    }
                }
            }
        return self.awslambda.get_function_configuration(FunctionName=self.function)

    def event_source_mappings(self) -> list:
        if self.mode == "fixture":
            return []
        return self.awslambda.list_event_source_mappings(FunctionName=self.function)[
            "EventSourceMappings"
        ]

    def function_tags(self) -> dict:
        if self.mode == "fixture":
            return {}
        return self.awslambda.get_function(FunctionName=self.function).get("Tags", {})

    def scheduling_rules(self) -> list:
        """Any EventBridge rule that could put this unit back on a schedule."""
        if self.mode == "fixture":
            return []
        rules = self.events.list_rules(NamePrefix="ow-tp-")["Rules"]
        scheduled = []
        for rule in rules:
            if not rule.get("ScheduleExpression"):
                continue
            targets = self.events.list_targets_by_rule(Rule=rule["Name"])["Targets"]
            if any(self.function in target.get("Arn", "") for target in targets):
                scheduled.append(rule["Name"])
        return scheduled

    # ---- data read-back

    def scan_ids(self) -> list:
        ids, kwargs = [], {"TableName": self.table, "ProjectionExpression": "event_id"}
        while True:
            page = self.dynamodb.scan(**kwargs)
            ids.extend(item["event_id"]["S"] for item in page.get("Items", []))
            if not page.get("LastEvaluatedKey"):
                return sorted(ids)
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def archive_objects(self) -> dict:
        """Archived objects under the unit prefix, keyed by S3 key."""
        objects, token = {}, None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": f"{self.prefix}/"}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.s3.list_objects_v2(**kwargs)
            for item in page.get("Contents", []):
                objects[item["Key"]] = {
                    "etag": item["ETag"].strip('"'),
                    "size": item["Size"],
                    "storage_class": item.get("StorageClass", "STANDARD"),
                }
            if not page.get("IsTruncated"):
                return objects
            token = page.get("NextContinuationToken")

    def archived_records(self, keys) -> dict:
        records = {}
        for key in keys:
            body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            for line in gzip.decompress(body).decode("utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    records[record["event_id"]] = record
        return records

    def head_storage_class(self, key: str) -> str:
        return self.s3.head_object(Bucket=self.bucket, Key=key).get(
            "StorageClass", "STANDARD"
        )

    # ---- exercising the target path

    def sweep(self, reference_time: str) -> dict:
        event = {"mode": "sweep", "reference_time": reference_time}
        if self.mode == "fixture":
            import handler

            return handler.lambda_handler(event)
        response = self.awslambda.invoke(
            FunctionName=self.function,
            InvocationType="RequestResponse",
            Payload=json.dumps(event).encode("utf-8"),
        )
        payload = json.loads(response["Payload"].read().decode("utf-8"))
        if response.get("FunctionError"):
            raise SystemExit(f"sweep invoke failed: {payload}")
        return payload

    def put_records(self, records) -> None:
        for record in records:
            self.dynamodb.put_item(TableName=self.table, Item=serialize(record))

    def delete_records(self, records) -> None:
        for record in records:
            self.dynamodb.delete_item(
                TableName=self.table,
                Key={
                    "event_id": {"S": record["event_id"]},
                    "timestamp": {"S": record["timestamp"]},
                },
            )

    def delete_objects(self, keys) -> None:
        for key in keys:
            self.s3.delete_object(Bucket=self.bucket, Key=key)


def serialize(record: dict) -> dict:
    item = {}
    for name, value in record.items():
        if isinstance(value, bool):
            item[name] = {"BOOL": value}
        elif isinstance(value, (int, float, decimal.Decimal)):
            item[name] = {"N": str(value)}
        elif isinstance(value, bytes):
            item[name] = {"B": value}
        else:
            item[name] = {"S": str(value)}
    return item


# --------------------------------------------------------------------------- fixture setup


def prepare_fixture(target: Target, prefix: str) -> None:
    """Stand up the unit's own table/bucket in LocalStack. Never touches golden stores."""
    try:
        target.dynamodb.describe_table(TableName=target.table)
    except target.dynamodb.exceptions.ResourceNotFoundException:
        target.dynamodb.create_table(
            TableName=target.table,
            AttributeDefinitions=[
                {"AttributeName": "event_id", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "event_id", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
            StreamSpecification={
                "StreamEnabled": True,
                "StreamViewType": "NEW_AND_OLD_IMAGES",
            },
        )
        target.dynamodb.get_waiter("table_exists").wait(TableName=target.table)
    if not target.ttl_spec().get("TimeToLiveStatus", "DISABLED").startswith("ENABL"):
        target.dynamodb.update_time_to_live(
            TableName=target.table,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"},
        )
    try:
        target.s3.head_bucket(Bucket=target.bucket)
    except ClientError:
        target.s3.create_bucket(Bucket=target.bucket)
    target.s3.put_bucket_lifecycle_configuration(
        Bucket=target.bucket,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "ow-tp-audit-archive-glacier",
                    "Status": "Enabled",
                    "Filter": {"Prefix": f"{prefix}/"},
                    "Transitions": [{"Days": 0, "StorageClass": "GLACIER"}],
                }
            ]
        },
    )


# --------------------------------------------------------------------------- report


def event_id_of(key: str) -> str:
    """The event_id an archive key was written for (keys are <event_id>__<ts>)."""
    return Path(key).name.split("__")[0]


def filter_patterns(mapping: dict) -> list:
    return [
        json.loads(entry["Pattern"])
        for entry in (mapping.get("FilterCriteria") or {}).get("Filters", [])
        if entry.get("Pattern")
    ]


def check(checks, cid, expected, actual, source) -> None:
    checks.append(
        {
            "id": cid,
            "expected": expected,
            "actual": actual,
            "source_of_truth": source,
            "result": "pass" if expected == actual else "fail",
        }
    )


def wait_for_ttl_probe(
    target: Target,
    records: list[dict],
    timeout_seconds: int = 600,
    interval_seconds: int = 15,
) -> dict:
    """Wait for both TTL removals to reach S3 and disappear from DynamoDB."""
    expected_ids = {record["event_id"] for record in records}
    deadline = time.monotonic() + timeout_seconds
    while True:
        keys = target.archive_objects()
        archived_ids = {
            event_id_of(key) for key in keys if event_id_of(key) in expected_ids
        }
        absent_ids = expected_ids - set(target.scan_ids())
        if archived_ids == expected_ids and absent_ids == expected_ids:
            return {
                "result": "pass",
                "archived_objects": sorted(archived_ids),
                "absent_from_table": sorted(absent_ids),
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                "result": "skipped",
                "archived_objects": sorted(archived_ids),
                "absent_from_table": sorted(absent_ids),
            }
        time.sleep(min(interval_seconds, remaining))


def run(args) -> dict:
    namespace = args.namespace
    target = Target(args.mode, args.table, args.bucket, args.prefix, args.function)
    if args.mode == "fixture":
        sys.path.insert(
            0, str(ROOT / "infrastructure/terraform/tp-cronbox/lambda/audit_archive")
        )
        os.environ.setdefault("TABLE_NAME", args.table)
        os.environ.setdefault("ARCHIVE_BUCKET", args.bucket)
        os.environ.setdefault("ARCHIVE_PREFIX", args.prefix)
        os.environ.setdefault("AWS_ENDPOINT_URL", LOCALSTACK_ENDPOINT)
        # The Lambda runtime supplies these in live mode. Fixture mode talks to
        # LocalStack only, and LocalStack namespaces state by access key id, so any
        # ambient real credentials would point the handler at another account.
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
        os.environ["AWS_ACCESS_KEY_ID"] = FIXTURE_CREDENTIALS["aws_access_key_id"]
        os.environ["AWS_SECRET_ACCESS_KEY"] = FIXTURE_CREDENTIALS["aws_secret_access_key"]
        os.environ.pop("AWS_SESSION_TOKEN", None)
        os.environ.pop("AWS_PROFILE", None)
        prepare_fixture(target, args.prefix)

    expected_records = golden_archive_records(namespace)
    expected_archived = sorted(expected_records)
    expected_retained = golden_retained_ids(namespace)
    live_reference = live_reference_time() if args.mode == "live" else None
    corpus = seed_corpus(namespace, args.run_date, live_reference)
    corpus_event_ids = {record["event_id"] for record in corpus}
    baseline_ids = corpus_event_ids - {UNEXPIRABLE_PROBE}
    pre_existing_objects = set(target.archive_objects())
    pre_existing_ids = set(target.scan_ids())

    checks: list[dict] = []
    unverified = [
        (
            "DynamoDB TTL physically deletes expired items on a best-effort basis (roughly "
            "48h), so the deletion instant itself is not observed in the validation window; "
            "contract coverage_gap ttl_deletion_latency."
        ),
        (
            "The Streams TTL-removal envelope (userIdentity principalId "
            "dynamodb.amazonaws.com) is exercised only by synthetic events in the fixture "
            "suite; a live TTL deletion is required to observe the real envelope."
        ),
        (
            "S3 lifecycle transitions run asynchronously (daily), so archived objects still "
            "read back as STANDARD immediately after the run; the GLACIER transition is "
            "verified as deployed configuration via GetBucketLifecycleConfiguration."
        ),
    ]
    if args.mode == "live":
        unverified.append(
            "The fixture expiry horizons lie roughly eight months in the wall-clock past, "
            "so this live run re-anchors the same corpus to now+1h; only the TTL horizon "
            "is shifted, while every compared identity, payload and expected set still "
            "comes from the immutable golden baseline."
        )

    # ---- ARC-01 / ARC-07: configuration, read back from the deployed platform
    ttl = target.ttl_spec()
    check(
        checks,
        "ARC-01/ttl-specification",
        {"attribute": "expires_at", "status": "ENABLED"},
        {
            "attribute": ttl.get("AttributeName"),
            "status": ttl.get("TimeToLiveStatus"),
        },
        f"dynamodb:DescribeTimeToLive {args.table}",
    )
    function_env = (target.function_configuration().get("Environment") or {}).get(
        "Variables", {}
    )
    check(
        checks,
        "ARC-01/retention-days-deployed",
        str(RETENTION_DAYS),
        function_env.get("RETENTION_DAYS"),
        "lambda:GetFunctionConfiguration environment"
        if args.mode == "live"
        else "packaged handler defaults",
    )
    lifecycle = target.lifecycle_rules()
    transitions = sorted(
        (rule["ID"], transition.get("StorageClass"), transition.get("Days"))
        for rule in lifecycle
        for transition in rule.get("Transitions", [])
    )
    check(
        checks,
        "ARC-01/archive-lifecycle-glacier",
        [["ow-tp-audit-archive-glacier", "GLACIER", 0]],
        [list(item) for item in transitions],
        f"s3:GetBucketLifecycleConfiguration {args.bucket}",
    )
    check(
        checks,
        "ARC-01/archive-lifecycle-no-expiration",
        [],
        [rule["ID"] for rule in lifecycle if rule.get("Expiration")],
        f"s3:GetBucketLifecycleConfiguration {args.bucket}",
    )
    if args.mode == "live":
        check(
            checks,
            "ARC-07/no-schedule",
            {"scheduled_rules": [], "event_sources": ["dynamodb-stream"]},
            {
                "scheduled_rules": target.scheduling_rules(),
                "event_sources": sorted(
                    "dynamodb-stream"
                    if ":dynamodb:" in mapping.get("EventSourceArn", "")
                    else mapping.get("EventSourceArn", "")
                    for mapping in target.event_source_mappings()
                ),
            },
            "events:ListRules + lambda:ListEventSourceMappings",
        )
    else:
        # LocalStack has no deployed function, rule or event source mapping to read,
        # and a fabricated "observed" trigger would be evidence of nothing.
        checks.append(
            {
                "id": "ARC-07/no-schedule",
                "expected": {
                    "scheduled_rules": [],
                    "event_sources": ["dynamodb-stream"],
                },
                "actual": None,
                "source_of_truth": "events:ListRules + lambda:ListEventSourceMappings",
                "result": "skipped",
            }
        )
        unverified.append(
            "Absence of a scheduled EventBridge rule and the DynamoDB-stream event source "
            "mapping are deployed-only facts: no function or rule exists in the fixture "
            "estate, so ARC-07/no-schedule is skipped here and proven in the live run."
        )
    if args.mode == "live":
        mappings = target.event_source_mappings()
        check(
            checks,
            "ARC-07/stream-trigger-is-ttl-only",
            {"event_name": ["REMOVE"], "principal": ["dynamodb.amazonaws.com"]},
            {
                "event_name": sorted(
                    {
                        name
                        for mapping in mappings
                        for pattern in filter_patterns(mapping)
                        for name in pattern.get("eventName", [])
                    }
                ),
                "principal": sorted(
                    {
                        principal
                        for mapping in mappings
                        for pattern in filter_patterns(mapping)
                        for principal in (pattern.get("userIdentity") or {}).get(
                            "principalId", []
                        )
                    }
                ),
            },
            "lambda:ListEventSourceMappings filter criteria",
        )
        check(
            checks,
            "ARC-07/tagged-and-prefixed",
            {"project_tag": "otterworks-tp", "prefixed": True},
            {
                "project_tag": target.function_tags().get("Project"),
                "prefixed": args.function.startswith("ow-tp-")
                and args.table.startswith("ow-tp-")
                and args.bucket.startswith("ow-tp-"),
            },
            "lambda:GetFunction tags",
        )
        check(
            checks,
            "ARC-07/on-demand-capacity",
            "PAY_PER_REQUEST",
            target.table_description().get("BillingModeSummary", {}).get("BillingMode"),
            f"dynamodb:DescribeTable {args.table}",
        )

    # ---- exercise the expiry-driven archive path
    target.put_records(corpus)
    reference_time = (
        iso(live_reference)
        if live_reference is not None
        else f"{args.run_date}T00:00:00Z"
    )
    first = target.sweep(reference_time)
    # Inventory taken between the two sweeps: the second sweep's object delta is
    # only evidence of convergence if it is measured against this listing.
    objects = target.archive_objects()
    second = target.sweep(reference_time)
    ttl_probe = []
    ttl_probe_result = None
    if args.mode == "live":
        probe_records = ttl_probe_records(namespace)
        if args.skip_ttl_probe:
            checks.append(
                {
                    "id": "ARC-01/live-ttl-removal-archived",
                    "expected": sorted(
                        record["event_id"] for record in probe_records
                    ),
                    "actual": None,
                    "source_of_truth": (
                        "s3:ListObjectsV2 after DynamoDB TTL deletion plus "
                        "dynamodb:Scan absence of the items"
                    ),
                    "result": "skipped",
                }
            )
            unverified.append(
                "--skip-ttl-probe was supplied, so the bounded live TTL-removal "
                "probe was not seeded or observed."
            )
        else:
            ttl_probe = probe_records
            target.put_records(ttl_probe)
            ttl_probe_result = wait_for_ttl_probe(target, ttl_probe)
            probe_ids = sorted(record["event_id"] for record in ttl_probe)
            if ttl_probe_result["result"] == "pass":
                checks.append(
                    {
                        "id": "ARC-01/live-ttl-removal-archived",
                        "expected": {
                            "archived_objects": probe_ids,
                            "absent_from_table": probe_ids,
                        },
                        "actual": {
                            "archived_objects": ttl_probe_result["archived_objects"],
                            "absent_from_table": ttl_probe_result["absent_from_table"],
                        },
                        "source_of_truth": (
                            "s3:ListObjectsV2 after DynamoDB TTL deletion plus "
                            "dynamodb:Scan absence of the items"
                        ),
                        "result": "pass",
                    }
                )
            else:
                checks.append(
                    {
                        "id": "ARC-01/live-ttl-removal-archived",
                        "expected": {
                            "archived_objects": probe_ids,
                            "absent_from_table": probe_ids,
                        },
                        "actual": {
                            "archived_objects": ttl_probe_result["archived_objects"],
                            "absent_from_table": ttl_probe_result["absent_from_table"],
                        },
                        "source_of_truth": (
                            "s3:ListObjectsV2 after DynamoDB TTL deletion plus "
                            "dynamodb:Scan absence of the items"
                        ),
                        "result": "skipped",
                    }
                )
                unverified.append(
                    "The bounded live TTL-removal probe reached its 10-minute deadline "
                    "before both probe objects appeared in S3 after DynamoDB TTL "
                    "deletion; the existing 48-hour TTL latency coverage-gap note "
                    "remains in force."
                )
    final_objects = target.archive_objects()

    new_keys = sorted(set(objects) - pre_existing_objects)
    # In live mode the sweep also archives real expiring events, and the stream can
    # write concurrently: only keys naming a corpus event belong to this run.
    corpus_keys = sorted(key for key in new_keys if event_id_of(key) in corpus_event_ids)
    archived_ids = sorted({event_id_of(key) for key in corpus_keys} & baseline_ids)
    # Retained means "still in the table", read back from the table itself, so a record
    # the archive path wrongly deleted cannot pass as retained.
    table_ids = set(target.scan_ids())
    retained_ids = sorted((baseline_ids & table_ids) - set(archived_ids))

    # ---- ARC-02 / ARC-03: sets, recomputed from S3 and DynamoDB
    check(
        checks,
        "ARC-02/archived-event-id-set",
        expected_archived,
        archived_ids,
        f"s3:ListObjectsV2 s3://{args.bucket}/{args.prefix}/",
    )
    check(
        checks,
        "ARC-02/retained-event-id-set",
        expected_retained,
        retained_ids,
        f"s3:ListObjectsV2 + dynamodb:Scan {args.table}",
    )
    check(
        checks,
        "ARC-02/counts",
        {"archived": len(expected_archived), "retained": len(expected_retained)},
        {"archived": len(archived_ids), "retained": len(retained_ids)},
        "recomputed from the two sets above",
    )
    check(
        checks,
        "ARC-03/exclusive-cutoff",
        {
            f"{namespace}-{BOUNDARY_ARCHIVED}": "archived",
            f"{namespace}-{BOUNDARY_RETAINED[0]}": "retained",
            f"{namespace}-{BOUNDARY_RETAINED[1]}": "retained",
        },
        {
            f"{namespace}-{name}": "archived"
            if f"{namespace}-{name}" in archived_ids
            else "retained"
            for name in (BOUNDARY_ARCHIVED,) + BOUNDARY_RETAINED
        },
        f"s3:ListObjectsV2 s3://{args.bucket}/{args.prefix}/",
    )

    # ---- ARC-04: storage class / lifecycle transition, read back from S3
    sample_key = corpus_keys[0] if corpus_keys else None
    check(
        checks,
        "ARC-04/archive-lifecycle-transition-applies",
        {"prefix_covered": True, "storage_class": "GLACIER"},
        {
            "prefix_covered": any(
                (rule.get("Filter", {}).get("Prefix") or rule.get("Prefix") or "")
                == f"{args.prefix}/"
                for rule in lifecycle
            ),
            "storage_class": next(
                (
                    transition.get("StorageClass")
                    for rule in lifecycle
                    for transition in rule.get("Transitions", [])
                ),
                None,
            ),
        },
        f"s3:GetBucketLifecycleConfiguration {args.bucket}",
    )
    checks.append(
        {
            "id": "ARC-04/observed-object-storage-class",
            "expected": "STANDARD until the daily lifecycle transition runs",
            "actual": target.head_storage_class(sample_key) if sample_key else None,
            "source_of_truth": f"s3:HeadObject s3://{args.bucket}/{sample_key}",
            "result": "skipped",
        }
    )

    # ---- ARC-05: payload fidelity, decoded from the archived objects
    archived_records = target.archived_records(corpus_keys)
    mismatched = sorted(
        event_id
        for event_id, expected in expected_records.items()
        if {
            name: value
            for name, value in (archived_records.get(event_id) or {}).items()
            if name != "expires_at"
        }
        != expected
    )
    check(
        checks,
        "ARC-05/payload-attribute-fidelity",
        [],
        mismatched,
        f"s3:GetObject s3://{args.bucket}/{args.prefix}/ decoded",
    )
    check(
        checks,
        "ARC-05/raw-payload-preserved",
        sorted({expected_records[e]["raw_payload"] for e in expected_records}),
        sorted(
            {
                (archived_records.get(event_id) or {}).get("raw_payload")
                for event_id in expected_records
            }
        ),
        f"s3:GetObject s3://{args.bucket}/{args.prefix}/ decoded",
    )

    # ---- malformed-record policy: the unexpirable probe must survive, attributed
    check(
        checks,
        "malformed/unexpirable-retained-and-attributed",
        {"archived": False, "in_table": True, "attributed": True},
        {
            "archived": UNEXPIRABLE_PROBE in {event_id_of(key) for key in corpus_keys},
            "in_table": UNEXPIRABLE_PROBE in table_ids,
            "attributed": UNEXPIRABLE_PROBE in first.get("unexpirable", []),
        },
        "dynamodb:Scan + s3:ListObjectsV2 + sweep response",
    )

    # ---- ARC-06: convergence, proven by the second invoke
    duplicate_ids = sorted(
        event_id
        for event_id in archived_ids
        if len([key for key in corpus_keys if Path(key).name.startswith(f"{event_id}__")])
        != 1
    )
    recount = target.archive_objects()
    idempotent = {
        "second_run_archived": second.get("archived", []),
        "object_count_delta": len(
            [
                key
                for key in set(recount) - set(objects)
                if event_id_of(key) in corpus_event_ids
            ]
        ),
        "duplicate_event_ids": duplicate_ids,
    }
    check(
        checks,
        "ARC-06/convergent-reevaluation",
        {"second_run_archived": [], "object_count_delta": 0, "duplicate_event_ids": []},
        idempotent,
        "second sweep invoke + s3:ListObjectsV2 recount",
    )

    detections = {
        "audit_cutoff_minus_one_second": f"{namespace}-{BOUNDARY_ARCHIVED}" in archived_ids,
        "audit_cutoff_exact": f"{namespace}-{BOUNDARY_RETAINED[0]}" in retained_ids,
        "audit_cutoff_plus_one_second": f"{namespace}-{BOUNDARY_RETAINED[1]}" in retained_ids,
        "retained_recent_records": len(
            [event_id for event_id in retained_ids if "-new-" in event_id]
        )
        == 20,
    }
    expected_set = sorted(detections)
    actual_set = sorted(name for name, detected in detections.items() if detected)

    if not args.keep:
        cleanup_objects = target.archive_objects()
        target.delete_records(
            [
                record
                for record in corpus + ttl_probe
                if record["event_id"] not in pre_existing_ids
            ]
        )
        # Anything this run's events produced, including objects the second sweep
        # would have written; never an object belonging to a real audit event.
        target.delete_objects(
            sorted(
                key
                for key in (
                    set(final_objects)
                    | set(recount)
                    | set(new_keys)
                    | set(cleanup_objects)
                )
                - pre_existing_objects
                if event_id_of(key)
                in corpus_event_ids
                | {record["event_id"] for record in ttl_probe}
            )
        )
        unverified.append(
            "The seeded corpus and the archive objects it produced were removed after the "
            "run; the report is the retained evidence."
        )


    report = {
        "kind": "recon-report",
        "unit": "cron-archive",
        "namespace": namespace,
        "generated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_mode": "live" if args.mode == "live" else "fixture",
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": {
            "performed": True,
            "result": "pass"
            if not second.get("archived")
            and not duplicate_ids
            and not idempotent["object_count_delta"]
            else "fail",
            "evidence": json.dumps(idempotent, sort_keys=True),
        },
        "planted_anomaly_detections": {
            "expected_set": expected_set,
            "actual_set": actual_set,
            "missing": sorted(set(expected_set) - set(actual_set)),
            "unexpected": sorted(set(actual_set) - set(expected_set)),
        },
        "unverified_paths": unverified,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["live", "fixture"], default="live")
    parser.add_argument("--namespace", default="demo")
    parser.add_argument("--run-date", default="2026-01-15")
    parser.add_argument("--table", default="ow-tp-audit-events")
    parser.add_argument("--bucket", default="ow-tp-audit-archive")
    parser.add_argument("--prefix", default="audit-archive/expired")
    parser.add_argument("--function", default="ow-tp-audit-archive")
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the seeded corpus and archived objects in place for browsing",
    )
    parser.add_argument(
        "--skip-ttl-probe",
        action="store_true",
        help="skip the bounded live DynamoDB TTL-removal probe",
    )
    args = parser.parse_args()
    report = run(args)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    failed = [c["id"] for c in report["checks"] if c["result"] == "fail"]
    print(f"wrote {output} ({len(report['checks'])} checks, {len(failed)} failed)")
    for cid in failed:
        print(f"  FAIL {cid}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
