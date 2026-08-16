#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import uuid

from common import Manifest, require_env

require_env("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
m = Manifest("aws")
region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
name_prefix = os.environ.get("TP_AWS_NAME_PREFIX", "ow-tp-")
tag_key = os.environ.get("TP_AWS_PROJECT_TAG_KEY", "Project")
tag_value = os.environ.get("TP_AWS_PROJECT_TAG_VALUE", "otterworks-tp")
env = {**os.environ, "AWS_DEFAULT_REGION": region, "AWS_REGION": region}


def aws(pid, description, args, required=True):
    try:
        p = subprocess.run(["aws", *args, "--output", "json"], capture_output=True, text=True, timeout=45, env=env)
        detail = (p.stdout or p.stderr).strip().replace("\n", " ")[:1000]
        m.add(pid, description, "aws " + " ".join(args), "verified" if p.returncode == 0 else ("denied" if required else "skipped"), detail or "ok")
        return p.returncode == 0, detail
    except Exception as exc:
        m.add(pid, description, "aws " + " ".join(args), "denied", str(exc))
        return False, str(exc)


ok, identity = aws("identity", "Identify the AWS caller", ["sts", "get-caller-identity"])
if ok:
    try:
        m.data["credential_identity"] = json.loads(identity).get("Arn", "available")
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
            aws(f"{service}-create-permission", f"Simulate permission for {action}", [
                "iam", "simulate-principal-policy", "--policy-source-arn", caller_arn,
                "--action-names", action,
            ])
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
created, detail = aws("iam-role-create", "Create a temporary IAM role to prove role creation permission", ["iam", "create-role", "--role-name", role, "--assume-role-policy-document", trust])
if created:
    aws("iam-role-delete", "Delete the temporary IAM role", ["iam", "delete-role", "--role-name", role])
else:
    m.add("iam-role-delete", "Delete the temporary IAM role", "iam:DeleteRole", "skipped", "role was not created")

tagged, _ = aws("leftover-tag-scan", f"Scan for {tag_key}={tag_value} resources", ["resourcegroupstaggingapi", "get-resources", "--tag-filters", f"Key={tag_key},Values={tag_value}"])
for label, args in [
    ("leftover-lambda-scan", ["lambda", "list-functions", "--query", f"Functions[?starts_with(FunctionName,'{name_prefix}')].FunctionName"]),
    ("leftover-sfn-scan", ["stepfunctions", "list-state-machines", "--query", f"stateMachines[?starts_with(name,'{name_prefix}')].name"]),
    ("leftover-eventbridge-scan", ["events", "list-rules", "--name-prefix", name_prefix]),
    ("leftover-sqs-scan", ["sqs", "list-queues", "--queue-name-prefix", name_prefix]),
    ("leftover-dynamodb-scan", ["dynamodb", "list-tables", "--query", f"TableNames[?starts_with(@,'{name_prefix}')]"]),
    ("leftover-s3-scan", ["s3api", "list-buckets", "--query", f"Buckets[?starts_with(Name,'{name_prefix}')].Name"]),
    ("leftover-iam-scan", ["iam", "list-roles", "--query", f"Roles[?starts_with(RoleName,'{name_prefix}')].RoleName"]),
]:
    aws(label, f"Scan for leftover {name_prefix} resources", args)
raise SystemExit(m.write("aws"))
