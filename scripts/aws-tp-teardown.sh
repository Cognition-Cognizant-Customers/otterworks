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
export AWS_REGION="$AWS_DEFAULT_REGION"
# The provider region in the stack wins over the ambient one, so scanning must
# follow the DEPLOYED region — otherwise teardown could be "proven" against a
# region the stack was never in.
# an empty/destroyed state makes terraform print a "No outputs found" warning
# instead of a value, so the value is only trusted when it looks like a region
if deployed_region="$(terraform -chdir="$STACK_DIR" output -raw aws_region 2>/dev/null)" &&
  [[ "$deployed_region" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]$ ]]; then
  # AWS_REGION outranks AWS_DEFAULT_REGION in the CLI, so both must be pinned
  export AWS_DEFAULT_REGION="$deployed_region"
  export AWS_REGION="$deployed_region"
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
# The tagging index is eventually consistent and keeps returning just-deleted
# ARNs for a few minutes, so every hit is confirmed against its owning service
# API before it counts, and the scan is retried while only unconfirmed hits remain.
# Only an explicit not-found clears an ARN: a probe that fails for any other
# reason (denied, throttled, expired credentials) must read as present, never as
# "clean", exactly like the name scan below.
still_exists() {
  local err
  case "$1" in
  arn:aws:lambda:*:event-source-mapping:*)
    err="$(aws lambda get-event-source-mapping --uuid "${1##*:}" 2>&1 >/dev/null)" && return 0
    case "$err" in
    *ResourceNotFoundException*) return 1 ;;
    *)
      echo "  probe of $1 failed, treating it as present: $err" >&2
      return 0
      ;;
    esac
    ;;
  *) return 0 ;; # anything we cannot probe is treated as present
  esac
}

tagged=""
for attempt in 1 2 3 4 5 6; do
  index="$(aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=Project,Values=otterworks-tp \
    --query 'ResourceTagMappingList[].ResourceARN' --output text)"
  tagged=""
  stale=""
  for arn in $index; do
    if still_exists "$arn"; then
      tagged="$tagged $arn"
    else
      stale="$stale $arn"
    fi
  done
  [ -n "$tagged" ] && break
  [ -z "$stale" ] && break
  echo "  tag index still lists deleted resource(s), waiting (attempt $attempt):$stale"
  sleep 60
done
tagged="${tagged# }"
if [ -n "$tagged" ]; then
  echo "LEFTOVER tagged resources:"
  printf '%s\n' $tagged
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
