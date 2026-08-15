#!/usr/bin/env bash
#
# Destroy the AWS tech-partnerships serverless stack and PROVE it is gone.
#
# `terraform destroy` on infrastructure/terraform-tp-aws, then an independent
# scan (tag scan + per-service name scan for the ow-tp- prefix) so teardown does
# not rely on Terraform state being accurate.
#
# Usage: scripts/aws-tp-teardown.sh [--scan-only]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_DIR="$REPO_ROOT/infrastructure/terraform-tp-aws"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-us-east-1}}"
# The provider region in the stack wins over the ambient one, so scanning must
# follow the DEPLOYED region — otherwise teardown could be "proven" against a
# region the stack was never in.
if deployed_region="$(terraform -chdir="$STACK_DIR" output -raw aws_region 2>/dev/null)" && [ -n "$deployed_region" ]; then
  export AWS_DEFAULT_REGION="$deployed_region"
fi
PREFIX="${TP_NAME_PREFIX:-ow-tp-}"

if [ "${1:-}" != "--scan-only" ]; then
  echo "=== terraform destroy ($STACK_DIR) ==="
  if [ -f "$STACK_DIR/terraform.tfstate" ] || [ -d "$STACK_DIR/.terraform" ]; then
    terraform -chdir="$STACK_DIR" init -input=false >/dev/null
    terraform -chdir="$STACK_DIR" destroy -auto-approve -input=false
  else
    echo "no local state in $STACK_DIR — nothing to destroy, scanning anyway"
  fi
fi

echo
echo "=== teardown verification: tag scan Project=otterworks-tp ==="
tagged=$(aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Project,Values=otterworks-tp \
  --query 'ResourceTagMappingList[].ResourceARN' --output text)
if [ -n "$tagged" ]; then
  echo "LEFTOVER tagged resources:"; printf '%s\n' $tagged
else
  echo "clean: no resources tagged Project=otterworks-tp"
fi

echo
echo "=== teardown verification: name scan '$PREFIX' per service ==="
leftovers=""
# A scan that ERRORS must never read as "clean" — an unauthorized or throttled
# call would otherwise let teardown be declared verified while resources bill on.
scan() {
  local label="$1"
  shift
  local out status
  out="$("$@" 2>&1)" && status=0 || status=$?
  if [ "$status" -ne 0 ]; then
    leftovers="$leftovers\n$label: SCAN FAILED ($out)"
    echo "  $label: SCAN FAILED"
  elif [ -n "$out" ] && [ "$out" != "None" ]; then
    leftovers="$leftovers\n$label: $out"
    echo "  $label: $out"
  else
    echo "  $label: clean"
  fi
}

scan s3 aws s3api list-buckets --query "Buckets[?starts_with(Name,'$PREFIX')].Name" --output text
scan lambda aws lambda list-functions --query "Functions[?starts_with(FunctionName,'$PREFIX')].FunctionName" --output text
scan sqs aws sqs list-queues --queue-name-prefix "$PREFIX" --query 'QueueUrls[]' --output text
scan dynamodb aws dynamodb list-tables --query "TableNames[?starts_with(@,'$PREFIX')]" --output text
scan stepfunctions aws stepfunctions list-state-machines --query "stateMachines[?starts_with(name,'$PREFIX')].name" --output text
scan eventbridge aws events list-rules --name-prefix "$PREFIX" --query 'Rules[].Name' --output text
scan iam-roles aws iam list-roles --query "Roles[?starts_with(RoleName,'$PREFIX')].RoleName" --output text
scan log-groups aws logs describe-log-groups --query "logGroups[?contains(logGroupName,'$PREFIX')].logGroupName" --output text

echo
if [ -n "$tagged" ] || [ -n "$leftovers" ]; then
  echo "TEARDOWN INCOMPLETE:"
  [ -n "$leftovers" ] && printf '%b\n' "$leftovers"
  exit 1
fi
echo "TEARDOWN VERIFIED: zero Project=otterworks-tp resources, zero '$PREFIX' names in $AWS_DEFAULT_REGION"
