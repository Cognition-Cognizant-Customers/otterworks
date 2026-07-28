#!/bin/bash
# Re-hydrate seeded S3 objects on LocalStack startup.
#
# LocalStack's community image does not capture S3 state via PERSISTENCE, so a
# seed dump is baked into the localstack_data volume at $DUMP_DIR (one subdir per
# bucket). This ready.d hook runs on every container start, AFTER
# localstack-init.sh (which creates the buckets), and syncs each bucket back from
# its dump when the live bucket is empty.
#
# No-op for a normal dev environment where no dump is present.
set -uo pipefail

DUMP_DIR="${SEED_S3_DUMP_DIR:-/var/lib/localstack/seed-s3}"

[ -d "$DUMP_DIR" ] || exit 0

for bucket_path in "$DUMP_DIR"/*/; do
  [ -d "$bucket_path" ] || continue
  bucket="$(basename "$bucket_path")"

  # Ensure the bucket exists (localstack-init.sh normally created it already).
  awslocal s3api head-bucket --bucket "$bucket" >/dev/null 2>&1 \
    || awslocal s3 mb "s3://$bucket" >/dev/null 2>&1 || true

  # Only restore into an empty bucket so we never clobber live changes.
  existing="$(awslocal s3api list-objects-v2 --bucket "$bucket" --max-items 1 \
    --query 'Contents[0].Key' --output text 2>/dev/null || echo None)"
  if [ -z "$existing" ] || [ "$existing" = "None" ]; then
    echo "[seed-restore] restoring s3://$bucket from $bucket_path"
    awslocal s3 sync "$bucket_path" "s3://$bucket" >/dev/null 2>&1 || true
  fi
done

echo "[seed-restore] done"
