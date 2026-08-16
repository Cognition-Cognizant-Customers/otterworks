#!/usr/bin/env bash
# Negative teardown verification for the AWS serverless track: scan by tag
# (Project=otterworks-tp) and by name prefix (ow-tp-) and fail if anything
# remains. Run after `make aws-tp-destroy`.
set -uo pipefail

REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="$REGION" AWS_REGION="$REGION"
PREFIX="ow-tp-"
FOUND=0

report() { # <label> <newline-separated items>
  local label="$1" items="$2"
  if [ -n "$items" ]; then
    FOUND=1
    echo "[LEFTOVER] $label:"
    echo "$items" | sed 's/^/  /'
  else
    echo "[CLEAN   ] $label"
  fi
}

report "tagged (Project=otterworks-tp)" "$(aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Project,Values=otterworks-tp \
  --query 'ResourceTagMappingList[].ResourceARN' --output text | tr '\t' '\n' | grep -v '^None$' || true)"

report "lambda ($PREFIX*)" "$(aws lambda list-functions \
  --query "Functions[?starts_with(FunctionName,'$PREFIX')].FunctionName" --output text | tr '\t' '\n' || true)"

report "stepfunctions ($PREFIX*)" "$(aws stepfunctions list-state-machines \
  --query "stateMachines[?starts_with(name,'$PREFIX')].name" --output text | tr '\t' '\n' || true)"

report "eventbridge rules ($PREFIX*)" "$(aws events list-rules --name-prefix "$PREFIX" \
  --query 'Rules[].Name' --output text | tr '\t' '\n' || true)"

report "sqs ($PREFIX*)" "$(aws sqs list-queues --queue-name-prefix "$PREFIX" \
  --query 'QueueUrls' --output text 2>/dev/null | tr '\t' '\n' | grep -v '^None$' || true)"

report "dynamodb ($PREFIX*)" "$(aws dynamodb list-tables \
  --query "TableNames[?starts_with(@,'$PREFIX')]" --output text | tr '\t' '\n' || true)"

report "s3 buckets ($PREFIX*)" "$(aws s3api list-buckets \
  --query "Buckets[?starts_with(Name,'$PREFIX')].Name" --output text | tr '\t' '\n' || true)"

report "iam roles ($PREFIX*)" "$(aws iam list-roles \
  --query "Roles[?starts_with(RoleName,'$PREFIX')].RoleName" --output text | tr '\t' '\n' || true)"

report "cloudwatch log groups (/aws/lambda/$PREFIX*)" "$(aws logs describe-log-groups \
  --log-group-name-prefix "/aws/lambda/$PREFIX" \
  --query 'logGroups[].logGroupName' --output text | tr '\t' '\n' || true)"

if [ "$FOUND" -ne 0 ]; then
  echo "aws-tp-scan: FAIL — leftovers found"
  exit 1
fi
echo "aws-tp-scan: clean (no ow-tp-/Project=otterworks-tp resources remain)"
