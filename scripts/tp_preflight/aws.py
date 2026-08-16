#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import uuid

from common import Manifest, exception_detail

m = Manifest("aws")
cleanup_registry = {}


def handle_uncaught(exc_type, exc, traceback):
    try:
        m.add("internal-error", "Unhandled preflight failure", "preflight runtime",
              "denied", exception_detail(exc))
        m.write("aws")
    finally:
        sys.__excepthook__(exc_type, exc, traceback)


sys.excepthook = handle_uncaught


def cleanup_all():
    for name, callback in list(cleanup_registry.items()):
        try:
            callback()
        except Exception as exc:
            m.add(f"{name}-cleanup", f"Cleanup temporary {name}", "AWS cleanup",
                  "denied", exception_detail(exc))
        cleanup_registry.pop(name, None)


def handle_signal(signum, _frame):
    cleanup_all()
    m.write("aws")
    raise SystemExit(128 + signum)


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)
configured_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
name_prefix = os.environ.get("TP_AWS_NAME_PREFIX", "ow-tp-")
tag_key = os.environ.get("TP_AWS_PROJECT_TAG_KEY", "Project")
tag_value = os.environ.get("TP_AWS_PROJECT_TAG_VALUE", "otterworks-tp")
if configured_region and not re.fullmatch(r"[A-Za-z0-9_-]+", configured_region):
    raise SystemExit("AWS region must contain only letters, digits, '-' or '_'")
if not re.fullmatch(r"[A-Za-z0-9_-]+", name_prefix):
    raise SystemExit("TP_AWS_NAME_PREFIX must contain only letters, digits, '-' or '_'")
tag_filter_pattern = r"[A-Za-z0-9 +\-._:/@]+"
if not re.fullmatch(tag_filter_pattern, tag_key) or not re.fullmatch(tag_filter_pattern, tag_value):
    raise SystemExit(
        "AWS tag key/value may contain only letters, digits, spaces, '+', '-', '.', '_', ':', '/', and '@'"
    )
env = dict(os.environ)
if configured_region:
    env["AWS_DEFAULT_REGION"] = configured_region
    env["AWS_REGION"] = configured_region

if configured_region:
    region = configured_region
else:
    region_probe = subprocess.run(
        ["aws", "configure", "get", "region"],
        capture_output=True,
        text=True,
        timeout=45,
        env=env,
    )
    region = (region_probe.stdout or "").strip() if region_probe.returncode == 0 else ""
    if region and not re.fullmatch(r"[A-Za-z0-9_-]+", region):
        region = ""
if region:
    m.add("region", "Resolve the AWS region used by the preflight",
          "aws configure get region" if not configured_region else "AWS environment",
          "verified", region)
else:
    m.add("region", "Resolve the AWS region used by the preflight",
          "aws configure get region", "denied",
          "no AWS region was provided or resolved from the active profile")


def aws(pid, description, args, required=True, record=True):
    try:
        p = subprocess.run(["aws", *args, "--output", "json"], capture_output=True, text=True, timeout=45, env=env)
        raw = (p.stdout or p.stderr).strip()
        if p.returncode == 0:
            detail = "command succeeded"
        else:
            try:
                error = json.loads(raw)
                detail = error.get("message") or error.get("Message") or error.get("Code") or "command failed"
            except json.JSONDecodeError:
                detail = next((line.strip() for line in raw.splitlines() if line.strip()), "command failed")
        if record:
            m.add(pid, description, "aws " + " ".join(args), "verified" if p.returncode == 0 else ("denied" if required else "skipped"), detail or "ok")
        return p.returncode == 0, raw
    except Exception as exc:
        if record:
            m.add(pid, description, "aws " + " ".join(args), "denied", exception_detail(exc))
        return False, exception_detail(exc)


def leftover_scan(pid, description, args, extractor):
    ok, raw = aws(pid, description, args, required=True, record=False)
    if not ok:
        m.add(pid, description, "aws " + " ".join(args), "denied", "scan command failed")
        return False
    try:
        body = {} if not raw.strip() else json.loads(raw)
        matches = extractor(body)
    except (json.JSONDecodeError, TypeError, AttributeError, KeyError) as exc:
        detail = f"unable to parse leftover scan output: {exc}; output={(raw or '<empty>')[:300]}"
        m.add(pid, description, "aws " + " ".join(args), "denied", detail)
        return False
    detail = json.dumps(matches) if matches else "none found"
    preflight_matches = [
        match for match in matches
        if re.search(rf"{re.escape(name_prefix)}preflight-", str(match))
    ]
    if preflight_matches:
        result = "denied"
        detail = f"preflight debris ({len(preflight_matches)}): {json.dumps(preflight_matches)}"
    elif matches and os.environ.get("TP_AWS_REQUIRE_CLEAN_ESTATE") == "1":
        result = "denied"
    elif matches:
        result = "informational"
        detail = f"{len(matches)} existing resource(s): {detail}"
    else:
        result = "verified"
    m.add(pid, description, "aws " + " ".join(args), result, detail)
    return result != "denied"


def policy_source_arn(caller_arn):
    match = re.fullmatch(r"arn:aws:sts::(\d+):assumed-role/(.+)/[^/]+", caller_arn)
    if not match:
        return caller_arn
    account, role = match.groups()
    return f"arn:aws:iam::{account}:role/{role}"


def simulate_permission(pid, description, caller_arn, action):
    source_arn = policy_source_arn(caller_arn)
    args = [
        "iam", "simulate-principal-policy", "--policy-source-arn", source_arn,
        "--action-names", action,
    ]
    try:
        p = subprocess.run(["aws", *args, "--output", "json"], capture_output=True, text=True, timeout=45, env=env)
        decision = None
        if p.stdout:
            try:
                payload = json.loads(p.stdout)
                results = payload.get("EvaluationResults", []) if isinstance(payload, dict) else []
                decision = results[0].get("EvalDecision") if results else None
            except json.JSONDecodeError:
                pass
        result = "verified" if decision == "allowed" else "denied"
        stderr = (p.stderr or "").strip()
        detail = f"decision={decision or 'unavailable'}"
        if p.returncode != 0 and stderr:
            detail += f", error={stderr.splitlines()[0][:300]}"
        m.add(pid, description, "aws iam simulate-principal-policy --action-names " + action, result,
              detail)
        return result == "verified"
    except Exception as exc:
        m.add(pid, description, "aws " + " ".join(args), "denied", exception_detail(exc))
        return False


ok, identity = aws("identity", "Identify the AWS caller", ["sts", "get-caller-identity"])
if ok:
    try:
        payload = json.loads(identity)
        m.set_identity(payload.get("Arn", "available") if isinstance(payload, dict) else "available")
    except json.JSONDecodeError:
        pass
if ok:
    try:
        caller_arn = json.loads(identity)["Arn"]
        for service, action in [
            ("lambda", "lambda:CreateFunction"), ("stepfunctions", "states:CreateStateMachine"),
            ("eventbridge", "events:PutRule"), ("sqs", "sqs:CreateQueue"),
            ("dynamodb", "dynamodb:CreateTable"), ("s3", "s3:CreateBucket"),
            ("iam", "iam:CreateRole"),
        ]:
            simulate_permission(f"{service}-create-permission", f"Simulate permission for {action}", caller_arn, action)
    except (KeyError, json.JSONDecodeError):
        pass
for service, args, desc in [
    ("lambda", ["lambda", "list-functions"], "List Lambda functions"),
    ("stepfunctions", ["stepfunctions", "list-state-machines"], "List Step Functions state machines"),
    ("eventbridge", ["events", "list-rules"], "List EventBridge rules"),
    ("sqs", ["sqs", "list-queues"], "List SQS queues"),
    ("dynamodb", ["dynamodb", "list-tables"], "List DynamoDB tables"),
    ("s3", ["s3api", "list-buckets"], "List S3 buckets"),
    ("iam", ["iam", "list-roles"], "List IAM roles"),
]:
    aws(f"{service}-permissions", desc, args)

role = f"{name_prefix}preflight-{uuid.uuid4().hex[:12]}"
trust = json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
create_succeeded = False
def reconcile_role():
    if create_succeeded:
        deleted, delete_detail = aws(
            "iam-role-delete",
            "Delete the temporary IAM role",
            ["iam", "delete-role", "--role-name", role],
            required=False,
            record=False,
        )
        if deleted:
            m.add("iam-role-delete", "Delete the temporary IAM role",
                  "iam:DeleteRole", "verified", "command succeeded")
        elif "NoSuchEntity" in delete_detail or "not found" in delete_detail.lower():
            m.add("iam-role-delete", "Delete the temporary IAM role",
                  "iam:DeleteRole", "verified", "role was already absent")
        else:
            m.add("iam-role-delete", "Delete the temporary IAM role",
                  "iam:DeleteRole", "denied", "role may still exist; manual IAM cleanup may be required")
        return
    found, lookup_detail = aws(
        "iam-role-reconcile",
        "Reconcile the temporary IAM role",
        ["iam", "get-role", "--role-name", role],
        required=False,
        record=False,
    )
    if found:
        m.add("iam-role-reconcile", "Reconcile the temporary IAM role",
              "iam:GetRole", "verified", f"role {role} exists; deleting it")
        aws("iam-role-delete", "Delete the temporary IAM role", ["iam", "delete-role", "--role-name", role])
    elif "NoSuchEntity" in lookup_detail or "not found" in lookup_detail.lower():
        m.add("iam-role-reconcile", "Reconcile the temporary IAM role",
              "iam:GetRole", "verified", f"role {role} was absent")
    else:
        m.add("iam-role-reconcile", "Reconcile the temporary IAM role",
              "iam:GetRole", "denied",
              f"role {role} may exist; manual IAM cleanup may be required")


cleanup_registry["iam-role"] = reconcile_role
try:
    create_succeeded, _ = aws(
        "iam-role-create", "Create a temporary IAM role to prove role creation permission",
        ["iam", "create-role", "--role-name", role, "--assume-role-policy-document", trust],
    )
finally:
    cleanup_all()

leftover_scan(
    "leftover-tag-scan",
    f"Scan for {tag_key}={tag_value} resources",
    ["resourcegroupstaggingapi", "get-resources", "--tag-filters", f"Key={tag_key},Values={tag_value}"],
    lambda body: [item.get("ResourceARN") for item in body.get("ResourceTagMappingList", [])],
)
def list_output(body):
    if not isinstance(body, list):
        raise TypeError("expected a JSON list")
    return body


def field_list(body, field):
    if not isinstance(body, dict):
        raise TypeError(f"expected JSON object with list field {field}")
    if field not in body:
        return []
    if not isinstance(body[field], list):
        raise TypeError(f"expected JSON object with list field {field}")
    return body[field]


for label, args, extractor in [
    ("leftover-lambda-scan", ["lambda", "list-functions", "--query", f"Functions[?starts_with(FunctionName,'{name_prefix}')].FunctionName"], list_output),
    ("leftover-sfn-scan", ["stepfunctions", "list-state-machines", "--query", f"stateMachines[?starts_with(name,'{name_prefix}')].name"], list_output),
    ("leftover-eventbridge-scan", ["events", "list-rules", "--name-prefix", name_prefix], lambda body: [item.get("Name") for item in field_list(body, "Rules")]),
    ("leftover-sqs-scan", ["sqs", "list-queues", "--queue-name-prefix", name_prefix], lambda body: field_list(body, "QueueUrls")),
    ("leftover-dynamodb-scan", ["dynamodb", "list-tables", "--query", f"TableNames[?starts_with(@,'{name_prefix}')]"], list_output),
    ("leftover-s3-scan", ["s3api", "list-buckets", "--query", f"Buckets[?starts_with(Name,'{name_prefix}')].Name"], list_output),
    ("leftover-iam-scan", ["iam", "list-roles", "--query", f"Roles[?starts_with(RoleName,'{name_prefix}')].RoleName"], list_output),
]:
    leftover_scan(label, f"Scan for leftover {name_prefix} resources", args, extractor)
raise SystemExit(m.write("aws"))
