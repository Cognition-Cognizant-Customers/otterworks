#!/usr/bin/env python3
from __future__ import annotations

import json
import os
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
region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
name_prefix = os.environ.get("TP_AWS_NAME_PREFIX", "ow-tp-")
tag_key = os.environ.get("TP_AWS_PROJECT_TAG_KEY", "Project")
tag_value = os.environ.get("TP_AWS_PROJECT_TAG_VALUE", "otterworks-tp")
env = {**os.environ, "AWS_DEFAULT_REGION": region, "AWS_REGION": region}


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
    m.add(pid, description, "aws " + " ".join(args), "denied" if matches else "verified", detail)
    return not matches


def simulate_permission(pid, description, caller_arn, action):
    args = [
        "iam", "simulate-principal-policy", "--policy-source-arn", caller_arn,
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
        m.add(pid, description, "aws iam simulate-principal-policy --action-names " + action, result,
              f"decision={decision or 'unavailable'}")
        return result == "verified"
    except Exception as exc:
        m.add(pid, description, "aws " + " ".join(args), "denied", exception_detail(exc))
        return False


ok, identity = aws("identity", "Identify the AWS caller", ["sts", "get-caller-identity"])
if ok:
    try:
        payload = json.loads(identity)
        m.data["credential_identity"] = payload.get("Arn", "available") if isinstance(payload, dict) else "available"
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
def reconcile_role():
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
    aws("iam-role-create", "Create a temporary IAM role to prove role creation permission",
        ["iam", "create-role", "--role-name", role, "--assume-role-policy-document", trust])
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
