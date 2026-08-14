#!/usr/bin/env bash
#############################################################
# aws-tp-verify.sh — recon the serverless CUSTBILL pipeline
# against the legacy chain (etl/legacy-extra/).
#
# Usage: scripts/aws-tp-verify.sh <NS>   (or: make aws-tp-verify NS=<ns>)
#
# 1. Seeds deterministic sample input (gen_sample_data.pl).
# 2. Runs the legacy chain locally (ingest -> parse -> report).
# 3. Uploads the same .dat files to the ow-tp landing bucket.
# 4. Waits for the event-driven pipeline, then diffs parsed .psv
#    files + the finance report byte-for-byte and reconciles the
#    DynamoDB record count. Writes a recon report; exits non-zero
#    on any mismatch.
#
# Requires: the terraform-tp-aws stack applied, aws cli creds,
# perl; ksh optional (falls back to a plain copy for ingest).
#############################################################
# The setup steps (1-3) fail fast via `die`; `set -e` is not used globally
# because the wait/compare sections must keep going to emit full diagnostics
set -uo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

NS="${1:-${NS:-}}"
if [ -z "$NS" ] || ! echo "$NS" | grep -qE '^[A-Za-z0-9]+$'; then
    echo "usage: $0 <NS>   (alphanumeric namespace)" >&2
    exit 1
fi
NS_LOWER=$(echo "$NS" | tr '[:upper:]' '[:lower:]')

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TF_DIR="$REPO_ROOT/infrastructure/terraform-tp-aws"
LEGACY="$REPO_ROOT/etl/legacy-extra"

# Talk to the region the stack was actually applied in (provider region wins
# over env vars, so the operator's AWS_REGION may not match the stack)
STACK_REGION=$(terraform -chdir="$TF_DIR" output -raw aws_region 2>/dev/null)
export AWS_DEFAULT_REGION="${STACK_REGION:-us-east-1}"
export AWS_REGION="$AWS_DEFAULT_REGION"

BUCKET=$(terraform -chdir="$TF_DIR" output -raw ingest_bucket 2>/dev/null)
TABLE=$(terraform -chdir="$TF_DIR" output -raw billing_table 2>/dev/null)
if [ -z "${BUCKET:-}" ] || [ -z "${TABLE:-}" ]; then
    echo "ERROR: terraform outputs unavailable — apply infrastructure/terraform-tp-aws first" >&2
    exit 1
fi

ROOT="/tmp/ow-tp-recon-$NS_LOWER"
rm -rf "$ROOT"
export OTTERWORKS_LEGACY_ROOT="$ROOT"
STASH="$ROOT/stash"
mkdir -p "$STASH"
REPORT_FILE="$ROOT/recon_report_$NS_LOWER.txt"

echo "== 1. Generating deterministic sample input (NS=$NS) =="
perl "$LEGACY/tools/gen_sample_data.pl" "$NS" || die "sample data generation failed"
cp "$ROOT/sftp-drop/upload/"CUSTBILL*.dat "$STASH/" || die "no CUSTBILL files generated"
NFILES=$(ls "$STASH" | wc -l | tr -d ' ')
NRECORDS=$(cat "$STASH"/*.dat | grep -cv '^HDR\|^TRL')

echo "== 2. Running the legacy chain locally =="
if command -v ksh >/dev/null 2>&1; then
    "$LEGACY/jobs/sftp_ingest_poll.ksh" >/dev/null || die "legacy ingest failed"
else
    mkdir -p "$ROOT/incoming"
    cp "$STASH"/*.dat "$ROOT/incoming/" || die "legacy ingest copy failed"
fi
"$LEGACY/jobs/parse_custbill_fixedwidth.sh" >/dev/null || die "legacy parse failed"
perl "$LEGACY/jobs/finance_excel_report.pl" >/dev/null || die "legacy report failed"
LEGACY_REPORT=$(ls "$ROOT/reports/"finance_billing_*.csv | head -1)
[ -n "$LEGACY_REPORT" ] || die "legacy report not produced"

echo "== 3. Clearing remote namespace + uploading to s3://$BUCKET/landing/$NS_LOWER/ =="
for p in landing parsed reports archive; do
    aws s3 rm --recursive "s3://$BUCKET/$p/$NS_LOWER/" --quiet || true
done
for f in "$STASH"/*.dat; do
    aws s3 cp "$f" "s3://$BUCKET/landing/$NS_LOWER/$(basename "$f")" --quiet || die "upload failed: $f"
done

echo "== 4. Waiting for the serverless pipeline =="
DEADLINE=$(( $(date +%s) + 300 ))
while :; do
    NPARSED=$(aws s3 ls "s3://$BUCKET/parsed/$NS_LOWER/" 2>/dev/null | grep -c '\.psv$' || true)
    NREPORT=$(aws s3 ls "s3://$BUCKET/reports/$NS_LOWER/" 2>/dev/null | grep -c '\.csv$' || true)
    [ "$NPARSED" -ge "$NFILES" ] && [ "$NREPORT" -ge 1 ] && break
    if [ "$(date +%s)" -gt "$DEADLINE" ]; then
        echo "ERROR: pipeline timed out (parsed=$NPARSED/$NFILES report=$NREPORT)" >&2
        exit 1
    fi
    sleep 5
done

compare() {
    PASS=true
    CLOUD="$ROOT/cloud"
    rm -rf "$CLOUD" && mkdir -p "$CLOUD/parsed" "$CLOUD/reports"
    aws s3 cp --recursive "s3://$BUCKET/parsed/$NS_LOWER/" "$CLOUD/parsed/" --quiet
    aws s3 cp --recursive "s3://$BUCKET/reports/$NS_LOWER/" "$CLOUD/reports/" --quiet

    {
        echo "recon report — NS=$NS  $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        echo "bucket=$BUCKET table=$TABLE files=$NFILES records=$NRECORDS"
        echo
        for f in "$ROOT/parsed/"CUSTBILL*.psv; do
            b=$(basename "$f")
            if diff -q "$f" "$CLOUD/parsed/$b" >/dev/null 2>&1; then
                echo "PASS  parsed/$b byte-identical"
            else
                echo "FAIL  parsed/$b differs"
                PASS=false
            fi
        done
        CLOUD_REPORT=$(ls "$CLOUD/reports/"finance_billing_*.csv 2>/dev/null | head -1)
        if [ -n "$CLOUD_REPORT" ] && diff -q "$LEGACY_REPORT" "$CLOUD_REPORT" >/dev/null 2>&1; then
            echo "PASS  finance report byte-identical ($(basename "$LEGACY_REPORT"))"
        else
            echo "FAIL  finance report differs ($(basename "$LEGACY_REPORT") vs ${CLOUD_REPORT:-missing})"
            PASS=false
        fi
        DDB_COUNT=$(aws dynamodb query --table-name "$TABLE" --select COUNT \
            --key-condition-expression "#ns = :ns AND begins_with(rec, :pfx)" \
            --expression-attribute-names '{"#ns":"ns"}' \
            --expression-attribute-values "{\":ns\":{\"S\":\"$NS_LOWER\"},\":pfx\":{\"S\":\"CUSTBILL\"}}" \
            --query Count --output text 2>/dev/null)
        if [ "$DDB_COUNT" = "$NRECORDS" ]; then
            echo "PASS  DynamoDB record count matches ($DDB_COUNT)"
        else
            echo "FAIL  DynamoDB record count $DDB_COUNT != $NRECORDS"
            PASS=false
        fi
    } > "$REPORT_FILE"
    cat "$REPORT_FILE"
    $PASS
}

echo "== 5. Reconciling outputs =="
# the last report regeneration can lag the last parse; retry the diff briefly
for attempt in 1 2 3 4 5 6; do
    if compare; then
        echo "aws-tp-verify: GREEN (report: $REPORT_FILE)"
        exit 0
    fi
    [ "$attempt" -lt 6 ] && echo "-- mismatch, retrying in 10s (attempt $attempt/6)" && sleep 10
done
echo "aws-tp-verify: FAILED (report: $REPORT_FILE)" >&2
exit 1
