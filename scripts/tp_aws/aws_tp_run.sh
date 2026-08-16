#!/usr/bin/env bash
# Drive one batch through the deployed pipeline (parent-run only):
# land the deterministic sample inputs in landing/ and start the
# ow-tp-<ns>-chain state machine for the batch report.
#
# Usage: NS=demo [REPORT_DATE=20260115] [INPUTS=<dir>] scripts/tp_aws/aws_tp_run.sh
set -euo pipefail

NS="${NS:-demo}"
REPORT_DATE="${REPORT_DATE:-20260115}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_DEFAULT_REGION="$REGION" AWS_REGION="$REGION"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_DIR="$REPO_ROOT/infrastructure/terraform-tp-aws"

BUCKET="$(terraform -chdir="$TF_DIR" output -raw pipeline_bucket)"
INPUTS="${INPUTS:?INPUTS=<dir with CUSTBILL_*.dat golden inputs> is required}"

echo "landing inputs from $INPUTS into s3://$BUCKET/landing/"
for f in "$INPUTS"/CUSTBILL_*.dat; do
  aws s3 cp "$f" "s3://$BUCKET/landing/$(basename "$f")"
done

# give ingest + parser time to drain the batch before the report runs
echo "waiting for parsed/ objects to appear..."
expected=$(ls "$INPUTS"/CUSTBILL_*.dat | wc -l)
for i in $(seq 1 30); do
  n=$(aws s3api list-objects-v2 --bucket "$BUCKET" --prefix parsed/ \
      --query 'length(Contents || `[]`)' --output text)
  [ "$n" -ge "$expected" ] && break
  sleep 5
done
echo "parsed/ objects: $n / $expected"

SM_ARN="$(aws stepfunctions list-state-machines \
  --query "stateMachines[?name=='ow-tp-$NS-chain'].stateMachineArn" --output text)"
if [ -z "$SM_ARN" ] || [ "$SM_ARN" = "None" ]; then
  echo "state machine ow-tp-$NS-chain not found (report unit not applied yet?)" >&2
  exit 1
fi

EXEC_ARN="$(aws stepfunctions start-execution --state-machine-arn "$SM_ARN" \
  --input "{\"ns\":\"$NS\",\"report_date\":\"$REPORT_DATE\"}" \
  --query executionArn --output text)"
echo "started execution $EXEC_ARN"

for i in $(seq 1 30); do
  STATUS="$(aws stepfunctions describe-execution --execution-arn "$EXEC_ARN" \
    --query status --output text)"
  [ "$STATUS" != "RUNNING" ] && break
  sleep 5
done
echo "execution status: $STATUS"
[ "$STATUS" = "SUCCEEDED" ]
