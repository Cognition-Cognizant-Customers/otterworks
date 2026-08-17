#!/usr/bin/env python3
"""Reconciliation for the cron-cleanup unit (replaces storage_cleanup_daily.py).

Every value in the emitted report is recomputed from the deployed AWS target:
S3 listings and object bodies, DynamoDB items, and the notification / lifecycle /
EventBridge / Lambda configuration read back through the AWS APIs. Nothing is
read from Terraform state, from a log line, or from the local fixture estate.

Single command (parent-owned live validation window):

    uv run --no-project --with boto3==1.35.99 python3 \
        scripts/tp_aws/cron_cleanup_recon.py \
        --out docs/tech-partnerships/recon/cron-cleanup-demo.recon.json

Writes performed: only the ones the contract's acceptance checks require to
exercise the event-driven path (a probe object pair under this unit's own
``files/<ns>/recon-<run-id>/`` prefix, a replayed invocation, and an on-demand
reconciliation sweep). Probe artifacts are cleaned up before exit. Loading the
deterministic estate is a separate parent step,
``scripts/tp_aws/seed_cron_cleanup_estate.py``.

``--mode fixture`` is the child's self-check: the same checks run against the
LocalStack estate under ``-fixture``-suffixed stand-in names, driving the packaged
handler in-process because the fixture has no EventBridge or Lambda. It seeds its
own estate, and every deployed-only fact (bucket notification, lifecycle rule,
EventBridge rule, Lambda/DLQ configuration, tags) is reported ``skipped`` with the
reason recorded in ``unverified_paths`` — it is evidence about the logic, never a
substitute for the live run:

    uv run --no-project --with boto3==1.35.99 python3 \
        scripts/tp_aws/cron_cleanup_recon.py --mode fixture \
        --out docs/tech-partnerships/recon/cron-cleanup-demo.fixture.recon.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "testdata/legacy/golden/cronbox"

STORAGE_BUCKET = os.environ.get("OW_TP_STORAGE_BUCKET", "ow-tp-file-storage")
QUARANTINE_BUCKET = os.environ.get("OW_TP_QUARANTINE_BUCKET", "ow-tp-file-quarantine")
METADATA_TABLE = os.environ.get("OW_TP_METADATA_TABLE", "ow-tp-file-metadata")
AUDIT_TABLE = os.environ.get("OW_TP_AUDIT_TABLE", "ow-tp-orphan-audit")
FUNCTION_NAME = os.environ.get("OW_TP_FUNCTION", "ow-tp-orphan-quarantine")
RULE_NAME = os.environ.get("OW_TP_RULE", "ow-tp-orphan-detect")
DLQ_NAME = os.environ.get("OW_TP_DLQ", "ow-tp-orphan-quarantine-dlq")
REGION = os.environ.get("AWS_REGION", "us-east-1")
LOCALSTACK_ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
HANDLER_PATH = ROOT / "infrastructure/lambda/ow-tp-orphan-quarantine/handler.py"

FILES_PREFIX = "files/"
QUARANTINE_PREFIX = "quarantined"
QUARANTINE_KEY_RE = re.compile(r"^quarantined/(\d{4}-\d{2}-\d{2})/(?P<source>.+)$")
UNICODE_PROBE_NAME = "Fichier \u0394 \u2615.bin"


# --------------------------------------------------------------------------
# expectations: the immutable golden baseline, never the deployed target
# --------------------------------------------------------------------------


def expectations(ns: str) -> dict:
    """Expected sets, derived only from the committed immutable baseline."""
    seed = json.loads((GOLDEN / ns / "seed-manifest.json").read_text())
    job = json.loads(
        (GOLDEN / ns / "storage_cleanup_daily" / "manifest.json").read_text()
    )
    report = json.loads(
        (
            GOLDEN
            / ns
            / "storage_cleanup_daily"
            / "artifacts/otterworks-data-lake/reports/storage-cleanup"
            / job["run_date"]
            / "report.json"
        ).read_text()
    )
    files = seed["stores"]["files"]
    orphan_keys = sorted(files["orphan_keys"])
    referenced = sorted(
        f"files/{ns}/file-{i:03d}.bin" for i in range(files["referenced_objects"])
    )
    quarantined_legacy = sorted(job["s3"]["otterworks-file-quarantine"])
    return {
        "run_date": job["run_date"],
        "orphan_keys": orphan_keys,
        "referenced_keys": referenced,
        "reverse_orphan_ids": sorted(files["reverse_orphan_ids"]),
        "reverse_orphan_key": f"files/{ns}/missing-reverse.bin",
        "legacy_quarantine_keys": quarantined_legacy,
        "orphaned_bytes": report["orphans"]["orphaned_bytes"],
        "total_objects": report["inventory"]["total_objects"],
    }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def now() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_objects(s3, bucket: str, prefix: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=prefix
    ):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = obj["Size"]
    return out


def scan_table(ddb, table: str) -> list[dict]:
    items: list[dict] = []
    kwargs: dict = {"TableName": table}
    while True:
        page = ddb.scan(**kwargs)
        items.extend(page.get("Items", []))
        last = page.get("LastEvaluatedKey")
        if not last:
            return items
        kwargs["ExclusiveStartKey"] = last


def object_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def audit_item(ddb, key: str) -> dict | None:
    got = ddb.get_item(TableName=AUDIT_TABLE, Key={"object_key": {"S": key}})
    return got.get("Item")


def check(checks: list[dict], cid: str, expected, actual, source: str) -> bool:
    ok = expected == actual
    checks.append(
        {
            "id": cid,
            "expected": expected,
            "actual": actual,
            "source_of_truth": source,
            "result": "pass" if ok else "fail",
        }
    )
    return ok


def skip(checks: list[dict], cid: str, expected, reason: str) -> None:
    checks.append(
        {
            "id": cid,
            "expected": expected,
            "actual": None,
            "source_of_truth": reason,
            "result": "skipped",
        }
    )


def source_key_of(quarantine_key: str) -> str | None:
    match = QUARANTINE_KEY_RE.match(quarantine_key)
    return match.group("source") if match else None


# --------------------------------------------------------------------------
# target binding: deployed AWS (live) or the LocalStack fixture estate
# --------------------------------------------------------------------------


def use_fixture_names() -> None:
    """Rebind the estate names so the fixture run can never touch real resources."""
    global STORAGE_BUCKET, QUARANTINE_BUCKET, METADATA_TABLE, AUDIT_TABLE
    STORAGE_BUCKET = f"{STORAGE_BUCKET}-fixture"
    QUARANTINE_BUCKET = f"{QUARANTINE_BUCKET}-fixture"
    METADATA_TABLE = f"{METADATA_TABLE}-fixture"
    AUDIT_TABLE = f"{AUDIT_TABLE}-fixture"


def load_fixture_handler(endpoint: str):
    """Import the packaged handler pointed at the fixture estate.

    The fixture has no Lambda service, so the deployed artifact is executed
    in-process instead. ``RECHECK_DELAY_SECONDS=0`` disables the event-path
    write-order recheck: the fixture seeds metadata before the object, so there
    is no young-object race to wait out, and the wait is reported as unverified.
    """
    os.environ.update(
        {
            "AWS_ENDPOINT_URL": endpoint,
            "AWS_REGION": REGION,
            "AWS_ACCESS_KEY_ID": os.environ.get("AWS_ACCESS_KEY_ID", "fixture"),
            "AWS_SECRET_ACCESS_KEY": os.environ.get(
                "AWS_SECRET_ACCESS_KEY", "fixture-secret"
            ),
            "STORAGE_BUCKET": STORAGE_BUCKET,
            "QUARANTINE_BUCKET": QUARANTINE_BUCKET,
            "METADATA_TABLE": METADATA_TABLE,
            "AUDIT_TABLE": AUDIT_TABLE,
            "FILES_PREFIX": FILES_PREFIX,
            "QUARANTINE_PREFIX": QUARANTINE_PREFIX,
            "RECHECK_DELAY_SECONDS": "0",
        }
    )
    spec = importlib.util.spec_from_file_location("cron_cleanup_handler", HANDLER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load handler from {HANDLER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_clients(mode: str, endpoint: str) -> dict:
    if mode == "live":
        session = boto3.session.Session(region_name=REGION)
        lam = session.client("lambda")
        clients = {
            "mode": mode,
            "s3": session.client("s3"),
            "ddb": session.client("dynamodb"),
            "lambda": lam,
            "events": session.client("events"),
            "sqs": session.client("sqs"),
            "invoke_label": f"lambda:Invoke {FUNCTION_NAME}",
        }

        def invoke(payload: dict) -> tuple[str | None, dict]:
            response = lam.invoke(
                FunctionName=FUNCTION_NAME,
                InvocationType="RequestResponse",
                Payload=json.dumps(payload).encode(),
            )
            return (
                response.get("FunctionError"),
                json.loads(response["Payload"].read() or b"{}"),
            )

        clients["invoke"] = invoke
        return clients

    use_fixture_names()
    module = load_fixture_handler(endpoint)
    session = boto3.session.Session(
        region_name=REGION,
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    )
    clients = {
        "mode": mode,
        "s3": session.client("s3", endpoint_url=endpoint),
        "ddb": session.client("dynamodb", endpoint_url=endpoint),
        "lambda": None,
        "events": None,
        "sqs": None,
        "invoke_label": f"in-process {HANDLER_PATH.name} against {endpoint}",
    }

    def invoke_fixture(payload: dict) -> tuple[str | None, dict]:
        try:
            return None, module.handler(payload, None)
        # Lambda reports any handler exception as FunctionError; mirror that here
        # so a fixture failure lands in a check instead of aborting the run.
        except Exception as error:  # noqa: BLE001
            return type(error).__name__, {"errorMessage": str(error)}

    clients["invoke"] = invoke_fixture
    return clients


def deliver_synthetic_events(clients: dict, sizes: dict[str, int]) -> None:
    """Stand in for EventBridge delivery in fixture mode; a no-op when live."""
    if clients["mode"] != "fixture":
        return
    for key, size in sorted(sizes.items()):
        clients["invoke"](eventbridge_event(key, size, now()))


def prepare_fixture_estate(clients: dict, ns: str, exp: dict) -> None:
    """Create, reset and seed the fixture estate, then drive it through the handler."""
    if clients["mode"] != "fixture":
        raise RuntimeError("the fixture estate may only be prepared in fixture mode")
    if not STORAGE_BUCKET.endswith("-fixture"):
        raise RuntimeError("refusing to reset an estate that is not -fixture suffixed")

    s3, ddb = clients["s3"], clients["ddb"]
    for bucket in (STORAGE_BUCKET, QUARANTINE_BUCKET):
        try:
            s3.create_bucket(Bucket=bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in {
                "BucketAlreadyExists",
                "BucketAlreadyOwnedByYou",
            }:
                raise
    tables = {METADATA_TABLE: "id", AUDIT_TABLE: "object_key"}
    for table, hash_key in tables.items():
        try:
            ddb.create_table(
                TableName=table,
                KeySchema=[{"AttributeName": hash_key, "KeyType": "HASH"}],
                AttributeDefinitions=[
                    {"AttributeName": hash_key, "AttributeType": "S"}
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            ddb.get_waiter("table_exists").wait(TableName=table)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceInUseException":
                raise

    # Start from empty so every set comparison below means something.
    for bucket in (STORAGE_BUCKET, QUARANTINE_BUCKET):
        for key in list_objects(s3, bucket, ""):
            s3.delete_object(Bucket=bucket, Key=key)
    for table, hash_key in tables.items():
        for item in scan_table(ddb, table):
            ddb.delete_item(TableName=table, Key={hash_key: item[hash_key]})

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import seed_cron_cleanup_estate as seeder

    seeder.STORAGE_BUCKET = STORAGE_BUCKET
    seeder.METADATA_TABLE = METADATA_TABLE
    seeder.seed_estate(s3, ddb, ns, exp)

    deliver_synthetic_events(
        clients, list_objects(s3, STORAGE_BUCKET, f"{FILES_PREFIX}{ns}/")
    )


# --------------------------------------------------------------------------
# configuration read-back (CLN-06, CLN-08)
# --------------------------------------------------------------------------


DEPLOYED_ONLY_CHECKS = (
    ("CLN-06/storage_bucket_eventbridge_enabled", True),
    ("CLN-06/quarantine_lifecycle_expiry_configured", True),
    ("CLN-06/eventbridge_rule_has_no_schedule", ""),
    ("CLN-06/eventbridge_rule_enabled", "ENABLED"),
    ("CLN-06/eventbridge_rule_pattern", ["Object Created", ["aws.s3"]]),
    ("CLN-06/eventbridge_target_is_lambda_with_dlq", [True, True]),
    ("CLN-08/lambda_dlq_is_unit_queue", True),
    ("CLN-08/lambda_no_provisioned_concurrency", 0),
    ("CLN-08/lambda_tagged_project", "otterworks-tp"),
    ("CLN-08/dlq_tagged_project", "otterworks-tp"),
    ("CLN-08/unit_resource_names_prefixed", [True, True, True]),
)

DEPLOYED_ONLY_REASON = (
    "deployed-only configuration: the fixture estate has no bucket notification, "
    "lifecycle rule, EventBridge rule, Lambda function or SQS queue to read back"
)


def config_checks(checks: list[dict], clients: dict) -> None:
    if clients["mode"] == "fixture":
        for cid, expected in DEPLOYED_ONLY_CHECKS:
            skip(checks, cid, expected, DEPLOYED_ONLY_REASON)
        return

    s3, events, lam, sqs = (
        clients["s3"],
        clients["events"],
        clients["lambda"],
        clients["sqs"],
    )

    notification = s3.get_bucket_notification_configuration(Bucket=STORAGE_BUCKET)
    check(
        checks,
        "CLN-06/storage_bucket_eventbridge_enabled",
        True,
        "EventBridgeConfiguration" in notification,
        f"s3:GetBucketNotificationConfiguration {STORAGE_BUCKET}",
    )

    lifecycle = s3.get_bucket_lifecycle_configuration(Bucket=QUARANTINE_BUCKET)
    quarantine_rules = sorted(
        f"{rule.get('Filter', {}).get('Prefix', rule.get('Prefix', ''))}"
        f"|{rule.get('Status')}|{rule.get('Expiration', {}).get('Days')}"
        for rule in lifecycle.get("Rules", [])
    )
    check(
        checks,
        "CLN-06/quarantine_lifecycle_expiry_configured",
        True,
        any(
            rule.startswith(f"{QUARANTINE_PREFIX}/")
            and "|Enabled|" in rule
            and not rule.endswith("|None")
            for rule in quarantine_rules
        ),
        f"s3:GetBucketLifecycleConfiguration {QUARANTINE_BUCKET} -> {quarantine_rules}",
    )

    rule = events.describe_rule(Name=RULE_NAME)
    check(
        checks,
        "CLN-06/eventbridge_rule_has_no_schedule",
        "",
        rule.get("ScheduleExpression", ""),
        f"events:DescribeRule {RULE_NAME}",
    )
    check(
        checks,
        "CLN-06/eventbridge_rule_enabled",
        "ENABLED",
        rule.get("State"),
        f"events:DescribeRule {RULE_NAME}",
    )
    pattern = json.loads(rule.get("EventPattern") or "{}")
    check(
        checks,
        "CLN-06/eventbridge_rule_pattern",
        ["Object Created", ["aws.s3"]],
        [
            (pattern.get("detail-type") or [None])[0],
            pattern.get("source"),
        ],
        f"events:DescribeRule {RULE_NAME} EventPattern",
    )

    targets = events.list_targets_by_rule(Rule=RULE_NAME)["Targets"]
    check(
        checks,
        "CLN-06/eventbridge_target_is_lambda_with_dlq",
        [True, True],
        [
            any(FUNCTION_NAME in target["Arn"] for target in targets),
            all("DeadLetterConfig" in target for target in targets),
        ],
        f"events:ListTargetsByRule {RULE_NAME}",
    )

    function = lam.get_function(FunctionName=FUNCTION_NAME)
    dlq_arn = function["Configuration"].get("DeadLetterConfig", {}).get("TargetArn", "")
    check(
        checks,
        "CLN-08/lambda_dlq_is_unit_queue",
        True,
        dlq_arn.endswith(f":{DLQ_NAME}"),
        f"lambda:GetFunction {FUNCTION_NAME} DeadLetterConfig",
    )
    provisioned = lam.list_provisioned_concurrency_configs(FunctionName=FUNCTION_NAME)
    check(
        checks,
        "CLN-08/lambda_no_provisioned_concurrency",
        0,
        len(provisioned.get("ProvisionedConcurrencyConfigs", [])),
        f"lambda:ListProvisionedConcurrencyConfigs {FUNCTION_NAME}",
    )
    check(
        checks,
        "CLN-08/lambda_tagged_project",
        "otterworks-tp",
        function.get("Tags", {}).get("Project"),
        f"lambda:GetFunction {FUNCTION_NAME} Tags",
    )
    queue_url = sqs.get_queue_url(QueueName=DLQ_NAME)["QueueUrl"]
    queue_tags = sqs.list_queue_tags(QueueUrl=queue_url).get("Tags", {})
    check(
        checks,
        "CLN-08/dlq_tagged_project",
        "otterworks-tp",
        queue_tags.get("Project"),
        f"sqs:ListQueueTags {DLQ_NAME}",
    )
    check(
        checks,
        "CLN-08/unit_resource_names_prefixed",
        [True, True, True],
        [
            FUNCTION_NAME.startswith("ow-tp-"),
            RULE_NAME.startswith("ow-tp-"),
            DLQ_NAME.startswith("ow-tp-"),
        ],
        "deployed resource names read back above",
    )


# --------------------------------------------------------------------------
# estate sets (CLN-01, CLN-02, CLN-03, CLN-04)
# --------------------------------------------------------------------------


def estate_checks(
    checks: list[dict], clients: dict, ns: str, exp: dict, probe_prefix: str
) -> dict:
    s3, ddb = clients["s3"], clients["ddb"]
    storage = list_objects(s3, STORAGE_BUCKET, f"{FILES_PREFIX}{ns}/")
    quarantine = list_objects(s3, QUARANTINE_BUCKET, f"{QUARANTINE_PREFIX}/")

    surviving = sorted(key for key in storage if not key.startswith(probe_prefix))
    quarantined_sources = sorted(
        source
        for source in (source_key_of(key) for key in quarantine)
        if source
        and source.startswith(f"{FILES_PREFIX}{ns}/")
        and not source.startswith(probe_prefix)
    )

    check(
        checks,
        "CLN-01/orphan_key_set_quarantined",
        exp["orphan_keys"],
        quarantined_sources,
        f"s3:ListObjectsV2 {QUARANTINE_BUCKET}/{QUARANTINE_PREFIX}/ mapped back to source keys",
    )
    check(
        checks,
        "CLN-03/surviving_key_set",
        exp["referenced_keys"],
        surviving,
        f"s3:ListObjectsV2 {STORAGE_BUCKET}/{FILES_PREFIX}{ns}/",
    )
    check(
        checks,
        "CLN-01/orphans_absent_from_storage",
        [],
        sorted(key for key in exp["orphan_keys"] if key in storage),
        f"s3:ListObjectsV2 {STORAGE_BUCKET}",
    )

    freed = sum(
        size
        for key, size in quarantine.items()
        if (source_key_of(key) or "") in set(exp["orphan_keys"])
    )
    check(
        checks,
        "CLN-01/freed_bytes",
        exp["orphaned_bytes"],
        freed,
        f"s3:ListObjectsV2 {QUARANTINE_BUCKET} object sizes",
    )

    # CLN-04: dated quarantine prefix preserving the full source key path,
    # and copy-then-delete semantics (present in quarantine, gone from source).
    layout = sorted(
        key
        for key in quarantine
        if (source_key_of(key) or "") in set(exp["orphan_keys"])
    )
    check(
        checks,
        "CLN-04/quarantine_layout_matches_legacy_semantics",
        [len(exp["orphan_keys"]), True],
        [
            len(layout),
            all(QUARANTINE_KEY_RE.match(key) is not None for key in layout),
        ],
        f"s3:ListObjectsV2 {QUARANTINE_BUCKET} key shape quarantined/<YYYY-MM-DD>/<source key>",
    )
    bodies = sorted(
        {
            s3.get_object(Bucket=QUARANTINE_BUCKET, Key=key)["Body"].read()
            for key in layout
        }
    )
    check(
        checks,
        "CLN-04/quarantined_bodies_byte_identical",
        [b"orphan".decode()],
        [body.decode("utf-8", "backslashreplace") for body in bodies],
        f"s3:GetObject {QUARANTINE_BUCKET} object bodies",
    )

    # CLN-02: the reverse orphan is untouched in BOTH stores.
    metadata = scan_table(ddb, METADATA_TABLE)
    reverse = sorted(
        item["id"]["S"]
        for item in metadata
        if item.get("s3_key", {}).get("S") == exp["reverse_orphan_key"]
    )
    check(
        checks,
        "CLN-02/reverse_orphan_metadata_retained",
        exp["reverse_orphan_ids"],
        reverse,
        f"dynamodb:Scan {METADATA_TABLE}",
    )
    check(
        checks,
        "CLN-02/reverse_orphan_object_still_absent",
        False,
        object_exists(s3, STORAGE_BUCKET, exp["reverse_orphan_key"]),
        f"s3:HeadObject {STORAGE_BUCKET}/{exp['reverse_orphan_key']}",
    )
    check(
        checks,
        "CLN-02/reverse_orphan_not_quarantined",
        [],
        sorted(key for key in quarantine if key.endswith("missing-reverse.bin")),
        f"s3:ListObjectsV2 {QUARANTINE_BUCKET}",
    )
    check(
        checks,
        "CLN-02/metadata_item_count_unchanged",
        len(exp["referenced_keys"]) + len(exp["reverse_orphan_ids"]),
        len(metadata),
        f"dynamodb:Scan {METADATA_TABLE}",
    )

    audit_quarantined = sorted(
        item["object_key"]["S"]
        for item in scan_table(ddb, AUDIT_TABLE)
        if item.get("decision", {}).get("S") == "quarantined"
        and not item["object_key"]["S"].startswith(probe_prefix)
    )
    check(
        checks,
        "CLN-01/audit_quarantined_decision_set",
        exp["orphan_keys"],
        audit_quarantined,
        f"dynamodb:Scan {AUDIT_TABLE} decision=quarantined",
    )
    return {"quarantine": quarantine, "storage": storage}


# --------------------------------------------------------------------------
# live event path (CLN-05) and replay idempotency (CLN-07)
# --------------------------------------------------------------------------


def eventbridge_event(key: str, size: int, when: str) -> dict:
    return {
        "version": "0",
        "id": str(uuid.uuid4()),
        "detail-type": "Object Created",
        "source": "aws.s3",
        "time": when,
        "region": REGION,
        "detail": {
            "version": "0",
            "bucket": {"name": STORAGE_BUCKET},
            "object": {"key": key, "size": size},
            "reason": "PutObject",
        },
    }


def wait_for_quarantine(s3, ddb, key: str, deadline: float) -> dict | None:
    while time.time() < deadline:
        item = audit_item(ddb, key)
        if item and item.get("decision", {}).get("S") == "quarantined":
            quarantine_key = item["quarantine_key"]["S"]
            if object_exists(
                s3, QUARANTINE_BUCKET, quarantine_key
            ) and not object_exists(s3, STORAGE_BUCKET, key):
                return item
        time.sleep(5)
    return None


def wait_for_estate_settled(clients: dict, exp: dict, timeout: int) -> list[str]:
    """Bounded wait until every seeded orphan has been processed by the event path.

    The seed's own object-created events drive the same Lambda, which holds a
    young object for up to RECHECK_DELAY_SECONDS before quarantining it. Comparing
    the sets while the estate is still in flight would report drift that does not
    exist, so poll first and return whatever is still unprocessed at the deadline.
    """
    s3, ddb = clients["s3"], clients["ddb"]
    pending = list(exp["orphan_keys"])
    deadline = time.time() + timeout
    while True:
        remaining = []
        for key in pending:
            item = audit_item(ddb, key)
            settled = (
                item is not None
                and item.get("decision", {}).get("S") == "quarantined"
                and not object_exists(s3, STORAGE_BUCKET, key)
            )
            if not settled:
                remaining.append(key)
        pending = remaining
        if not pending or time.time() >= deadline:
            return pending
        time.sleep(5)


def live_event_checks(
    checks: list[dict], clients: dict, probe_prefix: str, timeout: int
) -> tuple[dict, list[str]]:
    """Exercise the event path with two probes: plain and multi-byte key."""
    s3, ddb = clients["s3"], clients["ddb"]
    probes = {
        "plain": f"{probe_prefix}probe.bin",
        "unicode": f"{probe_prefix}{UNICODE_PROBE_NAME}",
    }
    body = b"recon-probe-bytes\x00\xff"
    for key in probes.values():
        s3.put_object(Bucket=STORAGE_BUCKET, Key=key, Body=body)
    deliver_synthetic_events(clients, dict.fromkeys(probes.values(), len(body)))

    deadline = time.time() + timeout
    observed: dict[str, dict | None] = {
        name: wait_for_quarantine(s3, ddb, key, deadline)
        for name, key in probes.items()
    }

    check(
        checks,
        "CLN-05/event_driven_quarantine_without_schedule",
        [True, True],
        [observed["plain"] is not None, observed["unicode"] is not None],
        f"s3:PutObject probe under {probe_prefix} then dynamodb:GetItem {AUDIT_TABLE} + s3:HeadObject",
    )
    check(
        checks,
        "CLN-05/audit_records_trigger_source_event",
        ["event", "event"],
        [
            (observed[name] or {}).get("trigger_source", {}).get("S")
            for name in ("plain", "unicode")
        ],
        f"dynamodb:GetItem {AUDIT_TABLE}",
    )
    check(
        checks,
        "CLN-04/probe_destination_key_not_re_encoded",
        [True, True],
        [
            (observed[name] or {})
            .get("quarantine_key", {})
            .get("S", "")
            .endswith(probes[name])
            for name in ("plain", "unicode")
        ],
        f"dynamodb:GetItem {AUDIT_TABLE} quarantine_key vs source key",
    )
    if observed["unicode"]:
        moved = s3.get_object(
            Bucket=QUARANTINE_BUCKET, Key=observed["unicode"]["quarantine_key"]["S"]
        )["Body"].read()
        check(
            checks,
            "CLN-04/probe_body_byte_identical",
            body.hex(),
            moved.hex(),
            f"s3:GetObject {QUARANTINE_BUCKET} probe body bytes",
        )
    else:
        skip(
            checks,
            "CLN-04/probe_body_byte_identical",
            body.hex(),
            "unicode probe was not quarantined",
        )

    # CLN-07: replay the identical event for the plain probe.
    unverified: list[str] = []
    replay_evidence = "replay not performed"
    if observed["plain"]:
        before = observed["plain"]
        before_quarantine = list_objects(
            s3, QUARANTINE_BUCKET, before["quarantine_key"]["S"]
        )
        function_error, payload = clients["invoke"](
            eventbridge_event(probes["plain"], len(body), before["detected_at"]["S"])
        )
        after = audit_item(ddb, probes["plain"]) or {}
        after_quarantine = list_objects(
            s3, QUARANTINE_BUCKET, before["quarantine_key"]["S"]
        )
        replay_ok = all(
            [
                check(
                    checks,
                    "CLN-07/replay_no_function_error",
                    None,
                    function_error,
                    f"{clients['invoke_label']} replay of the identical event",
                ),
                check(
                    checks,
                    "CLN-07/replay_reports_already_quarantined",
                    "already_quarantined",
                    payload.get("status"),
                    f"{clients['invoke_label']} response payload",
                ),
                check(
                    checks,
                    "CLN-07/replay_audit_row_unchanged",
                    [before["detected_at"]["S"], before["quarantine_key"]["S"]],
                    [
                        after.get("detected_at", {}).get("S"),
                        after.get("quarantine_key", {}).get("S"),
                    ],
                    f"dynamodb:GetItem {AUDIT_TABLE} before/after replay",
                ),
                check(
                    checks,
                    "CLN-07/replay_no_duplicate_quarantine_object",
                    sorted(before_quarantine.items()),
                    sorted(after_quarantine.items()),
                    f"s3:ListObjectsV2 {QUARANTINE_BUCKET} before/after replay",
                ),
            ]
        )
        replay_evidence = (
            f"identical EventBridge event replayed through {clients['invoke_label']}; status="
            f"{payload.get('status')}, detected_at unchanged, quarantine object set unchanged"
        )
    else:
        for cid in (
            "CLN-07/replay_no_function_error",
            "CLN-07/replay_reports_already_quarantined",
            "CLN-07/replay_audit_row_unchanged",
            "CLN-07/replay_no_duplicate_quarantine_object",
        ):
            skip(
                checks,
                cid,
                "no-op replay",
                "probe was never quarantined, replay not attempted",
            )
        replay_ok = False
        unverified.append(
            "CLN-07 replay idempotency: the probe object never reached quarantine"
        )

    return {
        "probes": probes,
        "replay_ok": replay_ok,
        "evidence": replay_evidence,
    }, unverified


def cleanup_probes(clients: dict, probes: dict[str, str], probe_prefix: str) -> None:
    s3, ddb = clients["s3"], clients["ddb"]
    for key in list_objects(s3, STORAGE_BUCKET, probe_prefix):
        s3.delete_object(Bucket=STORAGE_BUCKET, Key=key)
    for key in list_objects(s3, QUARANTINE_BUCKET, f"{QUARANTINE_PREFIX}/"):
        source = source_key_of(key)
        if source and source.startswith(probe_prefix):
            s3.delete_object(Bucket=QUARANTINE_BUCKET, Key=key)
    for key in probes.values():
        ddb.delete_item(TableName=AUDIT_TABLE, Key={"object_key": {"S": key}})


# --------------------------------------------------------------------------
# on-demand reconciliation sweep: zero-orphan observability + idempotency
# --------------------------------------------------------------------------


def sweep_checks(checks: list[dict], clients: dict, exp: dict) -> str:
    ddb, s3 = clients["ddb"], clients["s3"]
    quarantine_before = list_objects(s3, QUARANTINE_BUCKET, f"{QUARANTINE_PREFIX}/")
    function_error, payload = clients["invoke"]({"mode": "reconcile"})
    check(
        checks,
        "CLN-05/sweep_no_function_error",
        None,
        function_error,
        f"{clients['invoke_label']} mode=reconcile (on demand, never scheduled)",
    )

    summary_keys = sorted(
        item["object_key"]["S"]
        for item in scan_table(ddb, AUDIT_TABLE)
        if item["object_key"]["S"].startswith("__sweep__/")
    )
    check(
        checks,
        "empty_input_semantics/sweep_summary_row_written",
        True,
        len(summary_keys) >= 1,
        f"dynamodb:Scan {AUDIT_TABLE} __sweep__/ summary items",
    )
    latest = audit_item(ddb, summary_keys[-1]) if summary_keys else None
    check(
        checks,
        "empty_input_semantics/zero_orphans_still_recorded",
        ["0", str(len(exp["reverse_orphan_ids"]))],
        [
            (latest or {}).get("orphans_quarantined", {}).get("N"),
            (latest or {}).get("reverse_orphans", {}).get("N"),
        ],
        f"dynamodb:GetItem {AUDIT_TABLE} latest sweep summary",
    )
    quarantine_after = list_objects(s3, QUARANTINE_BUCKET, f"{QUARANTINE_PREFIX}/")
    check(
        checks,
        "CLN-07/sweep_rerun_quarantine_set_unchanged",
        sorted(quarantine_before),
        sorted(quarantine_after),
        f"s3:ListObjectsV2 {QUARANTINE_BUCKET} before/after the reconciliation sweep",
    )
    check(
        checks,
        "CLN-02/sweep_leaves_reverse_orphan_alone",
        [len(exp["referenced_keys"]) + len(exp["reverse_orphan_ids"])],
        [len(scan_table(ddb, METADATA_TABLE))],
        f"dynamodb:Scan {METADATA_TABLE} after the sweep",
    )
    return (
        f"on-demand reconciliation sweep recomputed the orphan set from the deployed stores "
        f"({payload.get('orphans_found')} orphan(s) found, "
        f"{payload.get('orphans_quarantined')} quarantined) and changed nothing"
    )


# --------------------------------------------------------------------------
# report assembly
# --------------------------------------------------------------------------


def anomaly_sets(clients: dict, ns: str, exp: dict, probe_prefix: str) -> dict:
    s3, ddb = clients["s3"], clients["ddb"]
    storage = list_objects(s3, STORAGE_BUCKET, f"{FILES_PREFIX}{ns}/")
    quarantine = list_objects(s3, QUARANTINE_BUCKET, f"{QUARANTINE_PREFIX}/")
    metadata = scan_table(ddb, METADATA_TABLE)

    expected_set = [["s3_orphan_objects", key] for key in exp["orphan_keys"]]
    expected_set += [["reverse_metadata_orphan", i] for i in exp["reverse_orphan_ids"]]
    expected_set += [
        ["referenced_objects_untouched", key] for key in exp["referenced_keys"]
    ]

    actual_set = [
        ["s3_orphan_objects", source]
        for source in sorted(
            source
            for source in (source_key_of(key) for key in quarantine)
            if source
            and source.startswith(f"{FILES_PREFIX}{ns}/")
            and not source.startswith(probe_prefix)
        )
    ]
    actual_set += [
        ["reverse_metadata_orphan", item["id"]["S"]]
        for item in sorted(metadata, key=lambda i: i["id"]["S"])
        if item.get("s3_key", {}).get("S") == exp["reverse_orphan_key"]
        and not object_exists(s3, STORAGE_BUCKET, item["s3_key"]["S"])
    ]
    actual_set += [
        ["referenced_objects_untouched", key]
        for key in sorted(storage)
        if not key.startswith(probe_prefix) and key not in set(exp["orphan_keys"])
    ]

    expected_pairs = {tuple(pair) for pair in expected_set}
    actual_pairs = {tuple(pair) for pair in actual_set}
    return {
        "expected_set": expected_set,
        "actual_set": actual_set,
        "missing": [list(pair) for pair in sorted(expected_pairs - actual_pairs)],
        "unexpected": [list(pair) for pair in sorted(actual_pairs - expected_pairs)],
    }


COVERAGE_GAPS = [
    (
        "cost_savings_estimate (contract coverage_gap): the legacy report's hardcoded per-GB dollar "
        "figure is presentation-only and is not reproduced; freed bytes are compared instead."
    ),
    (
        "event_delivery_eventual_consistency (contract coverage_gap): per-object event delivery has no "
        "daily batch boundary, so no batch-instant equality is asserted; the event path itself is "
        "exercised live and the resulting orphan set compared as a set."
    ),
    (
        "Quarantine lifecycle expiry is verified as configuration only; the 30-day deletion cannot be "
        "observed inside a validation window."
    ),
    (
        "The legacy savings report artifact (s3://otterworks-data-lake/reports/storage-cleanup/) is "
        "intentionally not emitted by the config-first replacement, so no byte-parity claim is made; "
        "the observable zero-orphan outcome is the sweep summary item instead."
    ),
    (
        "DLQ delivery on repeated handler failure is not exercised: no failure was injected, only the "
        "Lambda and EventBridge target dead-letter configuration were read back."
    ),
    "IAM least privilege is asserted by policy shape, not by a negative denial test.",
    (
        "Post-teardown tag/prefix emptiness (CLN-08) belongs to the parent's teardown step and is "
        "outside this run."
    ),
]


FIXTURE_UNVERIFIED = [
    (
        "run_mode=fixture: every value is recomputed from the LocalStack fixture estate "
        "(-fixture suffixed stand-in buckets and tables), not from the deployed AWS "
        "target; only the parent's live run satisfies the contract's read-back requirement."
    ),
    (
        "run_mode=fixture: EventBridge delivery itself is not exercised. The rule, its "
        "target and the object-created pattern do not exist in the fixture, so the handler "
        "is invoked in-process with synthetic envelopes of the shape the rule would deliver."
    ),
    (
        "run_mode=fixture: the deployed-only configuration checks reported as skipped "
        "(bucket EventBridge notification, quarantine lifecycle expiry, rule "
        "schedule/state/pattern, EventBridge target and Lambda DLQ, provisioned "
        "concurrency, resource tags) can only be read back from AWS."
    ),
    (
        "run_mode=fixture: the event-path write-order recheck runs with "
        "RECHECK_DELAY_SECONDS=0, so the bounded young-object wait is not exercised; the "
        "fixture seeds each metadata item before its object, which is the ordering that "
        "recheck exists to tolerate."
    ),
]


def build_report(
    ns: str,
    checks: list[dict],
    unverified: list[str],
    anomalies: dict,
    evidence: str,
    run_mode: str,
) -> dict:
    """Assemble the schema-valid report; idempotency is the CLN-07 check set."""
    replayed = [c for c in checks if c["id"].startswith("CLN-07/")]
    return {
        "kind": "recon-report",
        "unit": "cron-cleanup",
        "namespace": ns,
        "generated_at": now(),
        "run_mode": run_mode,
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": {
            "performed": True,
            "result": "pass"
            if replayed and all(c["result"] == "pass" for c in replayed)
            else "fail",
            "evidence": evidence,
        },
        "planted_anomaly_detections": anomalies,
        "unverified_paths": unverified,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default="demo")
    parser.add_argument(
        "--mode",
        choices=["live", "fixture"],
        default="live",
        help="live reads the deployed AWS target; fixture is the child self-check",
    )
    parser.add_argument(
        "--out", default="docs/tech-partnerships/recon/cron-cleanup-demo.recon.json"
    )
    parser.add_argument("--endpoint", default=LOCALSTACK_ENDPOINT)
    parser.add_argument("--probe-timeout", type=int, default=300)
    parser.add_argument(
        "--skip-probe", action="store_true", help="skip the live event-path probe"
    )
    args = parser.parse_args()

    clients = build_clients(args.mode, args.endpoint)

    exp = expectations(args.ns)
    probe_prefix = f"{FILES_PREFIX}{args.ns}/recon-{uuid.uuid4().hex[:8]}/"
    checks: list[dict] = []
    unverified = list(COVERAGE_GAPS)
    if args.mode == "fixture":
        unverified.extend(FIXTURE_UNVERIFIED)
        prepare_fixture_estate(clients, args.ns, exp)

    config_checks(checks, clients)

    probes: dict[str, str] = {}
    replay_evidence = "not performed"
    if args.skip_probe:
        skip(
            checks,
            "CLN-05/event_driven_quarantine_without_schedule",
            True,
            "--skip-probe requested",
        )
        unverified.append(
            "CLN-05/CLN-07 live event path and replay: --skip-probe was requested"
        )
    else:
        live, probe_unverified = live_event_checks(
            checks, clients, probe_prefix, args.probe_timeout
        )
        probes = live["probes"]
        replay_evidence = live["evidence"]
        unverified.extend(probe_unverified)

    pending = wait_for_estate_settled(clients, exp, args.probe_timeout)
    check(
        checks,
        "CLN-01/seeded_orphans_settled_before_comparison",
        [],
        pending,
        (
            f"polled dynamodb:GetItem {AUDIT_TABLE} + s3:HeadObject {STORAGE_BUCKET} "
            f"for up to {args.probe_timeout}s"
        ),
    )
    if pending:
        unverified.append(
            "CLN-01/CLN-03: the seeded orphan set had not finished processing within "
            "--probe-timeout, so the set comparisons below may reflect an in-flight estate."
        )

    estate_checks(checks, clients, args.ns, exp, probe_prefix)
    sweep_evidence = sweep_checks(checks, clients, exp)

    if probes:
        cleanup_probes(clients, probes, probe_prefix)

    anomalies = anomaly_sets(clients, args.ns, exp, probe_prefix)
    report = build_report(
        args.ns,
        checks,
        unverified,
        anomalies,
        f"{replay_evidence}; {sweep_evidence}",
        args.mode,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    failed = [c["id"] for c in checks if c["result"] == "fail"]
    skipped = [c["id"] for c in checks if c["result"] == "skipped"]
    print(
        f"wrote {out} ({len(checks)} checks, {len(failed)} failed, {len(skipped)} skipped)"
    )
    if anomalies["missing"] or anomalies["unexpected"]:
        print(
            f"anomaly set drift: missing={anomalies['missing']} unexpected={anomalies['unexpected']}"
        )
    for cid in failed:
        print(f"FAIL {cid}")
    return 1 if failed or anomalies["missing"] or anomalies["unexpected"] else 0


if __name__ == "__main__":
    sys.exit(main())
