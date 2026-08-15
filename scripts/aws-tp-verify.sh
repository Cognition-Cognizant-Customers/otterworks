#!/usr/bin/env bash
#
# Recon the serverless CUSTBILL pipeline against the legacy chain.
#
# Baselines are DERIVED, never hard-coded: the legacy outputs under
# $OTTERWORKS_LEGACY_ROOT (produced by `make legacy-etl-run NS=<ns>`) are the
# source of truth. Every parsed .psv and the finance report CSV must be
# byte-identical, the DynamoDB row count must match the legacy record count, the
# DLQ must be empty, and no Step Functions execution may have failed.
#
# Usage: scripts/aws-tp-verify.sh [NS] [--wait <seconds>]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_DIR="$REPO_ROOT/infrastructure/terraform-tp-aws"
LEGACY_ROOT="${OTTERWORKS_LEGACY_ROOT:-/tmp/otterworks-legacy}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-us-east-1}}"
# The stack's provider region wins over the ambient one, so the deployed region
# is authoritative for every CLI call below.
if deployed_region="$(terraform -chdir="$STACK_DIR" output -raw aws_region 2>/dev/null)" && [ -n "$deployed_region" ]; then
  export AWS_DEFAULT_REGION="$deployed_region"
fi

NS="${NS:-demo}"
WAIT=0
SINCE=""
while [ $# -gt 0 ]; do
  case "$1" in
  --wait)
    WAIT="$2"
    shift 2
    ;;
  --wait=*)
    WAIT="${1#*=}"
    shift
    ;;
  --since)
    SINCE="$2"
    shift 2
    ;;
  --since=*)
    SINCE="${1#*=}"
    shift
    ;;
  *)
    NS="$1"
    shift
    ;;
  esac
done
NS_UPPER="$(printf '%s' "$NS" | tr '[:lower:]' '[:upper:]')"

tf() { terraform -chdir="$STACK_DIR" output -raw "$1"; }
BUCKET="$(tf ingest_bucket)"
TABLE="$(tf billing_table)"
DLQ_URL="$(tf ingest_dlq_url)"
SM_ARN="$(tf state_machine_arn)"

# --- legacy baseline (derived) ---
shopt -s nullglob
legacy_psv=("$LEGACY_ROOT"/parsed/CUSTBILL_"${NS_UPPER}"_*.psv)
legacy_csv=("$LEGACY_ROOT"/reports/finance_billing_*.csv)
if [ ${#legacy_psv[@]} -eq 0 ] || [ ${#legacy_csv[@]} -eq 0 ]; then
  echo "no legacy baseline under $LEGACY_ROOT — run: make legacy-etl-gen-data NS=$NS && make legacy-etl-run NS=$NS" >&2
  exit 2
fi
# The legacy Perl job aggregates EVERY CUSTBILL*.psv in $LEGACY_ROOT/parsed
# regardless of namespace, while the serverless report only aggregates
# parsed/<ns>/. Comparing the two is only meaningful when the legacy parsed dir
# holds this namespace alone — refuse rather than emit a bogus mismatch.
foreign=()
for f in "$LEGACY_ROOT"/parsed/CUSTBILL_*.psv; do
  case "$(basename "$f")" in
  CUSTBILL_"${NS_UPPER}"_*) ;;
  *) foreign+=("$(basename "$f")") ;;
  esac
done
if [ ${#foreign[@]} -gt 0 ]; then
  echo "refusing to compare: $LEGACY_ROOT/parsed also holds other namespaces' files (${foreign[*]})." >&2
  echo "the legacy finance report is not namespace-scoped, so its baseline would mix namespaces." >&2
  echo "clear it and re-run the legacy chain for NS=$NS only: rm -rf $LEGACY_ROOT && make legacy-etl-gen-data NS=$NS && make legacy-etl-run NS=$NS" >&2
  exit 2
fi

# newest report wins (the legacy job stamps by date)
legacy_report="$(ls -t "${legacy_csv[@]}" | head -1)"
legacy_records=0
for f in "${legacy_psv[@]}"; do
  legacy_records=$((legacy_records + $(wc -l <"$f")))
done

echo "namespace       : $NS"
echo "bucket          : $BUCKET"
echo "legacy baseline : ${#legacy_psv[@]} parsed file(s), $legacy_records record(s), $(basename "$legacy_report")"
echo

# --- wait for the event-driven chain to catch up ---
s3_parsed() { aws s3api list-objects-v2 --bucket "$BUCKET" --prefix "parsed/$NS/" --query 'Contents[].Key' --output text 2>/dev/null || true; }
s3_report() { aws s3api list-objects-v2 --bucket "$BUCKET" --prefix "reports/$NS/finance_billing_" --query 'sort_by(Contents,&LastModified)[-1].Key' --output text 2>/dev/null || true; }

deadline=$((SECONDS + WAIT))
while :; do
  parsed_keys="$(s3_parsed)"
  report_key="$(s3_report)"
  parsed_n=0
  [ -n "$parsed_keys" ] && [ "$parsed_keys" != "None" ] && parsed_n=$(wc -w <<<"$parsed_keys")
  if [ "$parsed_n" -ge "${#legacy_psv[@]}" ] && [ -n "$report_key" ] && [ "$report_key" != "None" ]; then
    break
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "pipeline output incomplete after ${WAIT}s: parsed=$parsed_n/${#legacy_psv[@]} report=${report_key:-none}" >&2
    break
  fi
  sleep 5
done

# --- compare bytes ---
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
fails=0
printf '%-42s %-8s %s\n' FILE RESULT DETAIL
check() { # name expected_sha actual_sha
  if [ "$2" = "$3" ]; then
    printf '%-42s %-8s %s\n' "$1" PASS "sha256 ${2:0:12}…"
  else
    printf '%-42s %-8s %s\n' "$1" FAIL "expected ${2:0:12}… got ${3:0:12}…"
    fails=$((fails + 1))
  fi
}

for f in "${legacy_psv[@]}"; do
  base="$(basename "$f")"
  if aws s3 cp "s3://$BUCKET/parsed/$NS/$base" "$work/$base" --only-show-errors 2>/dev/null; then
    check "parsed/$NS/$base" "$(sha256sum <"$f" | cut -d' ' -f1)" "$(sha256sum <"$work/$base" | cut -d' ' -f1)"
  else
    printf '%-42s %-8s %s\n' "parsed/$NS/$base" FAIL "missing in s3"
    fails=$((fails + 1))
  fi
done

report_key="$(s3_report)"
if [ -n "$report_key" ] && [ "$report_key" != "None" ]; then
  aws s3 cp "s3://$BUCKET/$report_key" "$work/report.csv" --only-show-errors
  check "$report_key" "$(sha256sum <"$legacy_report" | cut -d' ' -f1)" "$(sha256sum <"$work/report.csv" | cut -d' ' -f1)"
else
  printf '%-42s %-8s %s\n' "reports/$NS/finance_billing_*.csv" FAIL "no report written"
  fails=$((fails + 1))
fi

# --- DynamoDB row count ---
ddb_count="$(aws dynamodb query --table-name "$TABLE" \
  --key-condition-expression 'ns = :ns' \
  --expression-attribute-values "{\":ns\":{\"S\":\"$NS\"}}" \
  --select COUNT --query Count --output text)"
if [ "$ddb_count" = "$legacy_records" ]; then
  printf '%-42s %-8s %s\n' "dynamodb $TABLE (ns=$NS)" PASS "$ddb_count items"
else
  printf '%-42s %-8s %s\n' "dynamodb $TABLE (ns=$NS)" FAIL "$ddb_count items, legacy has $legacy_records"
  fails=$((fails + 1))
fi

# --- DLQ must be empty ---
dlq="$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
  --query 'Attributes.[ApproximateNumberOfMessages,ApproximateNumberOfMessagesNotVisible]' --output text)"
dlq_total=$(awk '{print $1+$2}' <<<"$dlq")
if [ "$dlq_total" -eq 0 ]; then
  printf '%-42s %-8s %s\n' "dlq" PASS "empty"
else
  printf '%-42s %-8s %s\n' "dlq" FAIL "$dlq_total message(s) — see ow-tp-ingest-dlq"
  fails=$((fails + 1))
fi

# --- no failed executions for THIS run ---
# list-executions returns the full 90-day history across namespaces, so an old
# failure (e.g. the deliberate malformed-file beat) must not redden every later
# run: scope to this namespace's executions started at/after the cutoff.
if [ -n "$SINCE" ]; then
  cutoff="$(date -d "$SINCE" +%s)"
else
  # oldest landing object for the namespace = when this run's feed arrived
  oldest="$(aws s3api list-objects-v2 --bucket "$BUCKET" --prefix "landing/$NS/" \
    --query 'sort_by(Contents,&LastModified)[0].LastModified' --output text 2>/dev/null || true)"
  if [ -n "$oldest" ] && [ "$oldest" != "None" ]; then
    cutoff="$(date -d "$oldest" +%s)"
  else
    cutoff=0
  fi
fi

bad=""
for status in FAILED TIMED_OUT ABORTED; do
  # a query that could not run must never read as "none failed"
  if rows="$(aws stepfunctions list-executions --state-machine-arn "$SM_ARN" --status-filter "$status" \
    --query "executions[?starts_with(name,'$NS-')].[name,startDate]" --output text 2>&1)"; then
    while read -r name started; do
      [ -z "$name" ] && continue
      [ "$(date -d "$started" +%s 2>/dev/null || echo 0)" -ge "$cutoff" ] && bad="$bad $status:$name"
    done <<<"$rows"
  else
    bad="$bad $status:QUERY-FAILED($rows)"
  fi
done
if [ -z "$bad" ]; then
  printf '%-42s %-8s %s\n' "step functions executions" PASS "none failed"
else
  printf '%-42s %-8s %s\n' "step functions executions" FAIL "$bad"
  fails=$((fails + 1))
fi

echo
if [ "$fails" -eq 0 ]; then
  echo "VERIFY PASS: serverless pipeline output is byte-identical to the legacy chain for NS=$NS"
else
  echo "VERIFY FAIL: $fails check(s) failed for NS=$NS" >&2
  exit 1
fi
