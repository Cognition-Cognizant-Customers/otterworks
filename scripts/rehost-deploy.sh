#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# Lift-and-shift deploy for legacy-portal: build the fat JAR, upload it to the
# rehost artifact bucket, and (re)start the app on the EC2 instance via SSM.
#
# Prereqs:
#   - infrastructure/terraform/rehost has been applied (creates the EC2 instance,
#     RDS PostgreSQL, and the artifact bucket)
#   - AWS CLI credentials with s3:PutObject on the artifact bucket and
#     ssm:SendCommand on the instance
#
# Usage:
#   ./scripts/rehost-deploy.sh            # build + upload + restart
#   SKIP_BUILD=1 ./scripts/rehost-deploy.sh
# ------------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${REPO_ROOT}/services/legacy-portal"
TF_DIR="${REPO_ROOT}/infrastructure/terraform/rehost"
JAR="${APP_DIR}/target/legacy-portal.jar"

if [[ "${SKIP_BUILD:-0}" != "1" || ! -f "${JAR}" ]]; then
  echo "[rehost-deploy] Building legacy-portal fat JAR..."
  (cd "${APP_DIR}" && ./mvnw -B -DskipTests package)
fi

echo "[rehost-deploy] Reading Terraform outputs..."
ARTIFACT_BUCKET="$(terraform -chdir="${TF_DIR}" output -raw artifact_bucket)"
INSTANCE_ID="$(terraform -chdir="${TF_DIR}" output -raw instance_id)"
APP_URL="$(terraform -chdir="${TF_DIR}" output -raw app_url)"

echo "[rehost-deploy] Uploading JAR to s3://${ARTIFACT_BUCKET}/legacy-portal.jar..."
aws s3 cp "${JAR}" "s3://${ARTIFACT_BUCKET}/legacy-portal.jar"

echo "[rehost-deploy] Restarting legacy-portal on ${INSTANCE_ID} via SSM..."
COMMAND_ID="$(aws ssm send-command \
  --instance-ids "${INSTANCE_ID}" \
  --document-name "AWS-RunShellScript" \
  --comment "rehost-deploy: refresh legacy-portal.jar and restart" \
  --parameters 'commands=["/opt/legacy-portal/fetch-jar.sh","chown legacyportal:legacyportal /opt/legacy-portal/legacy-portal.jar","systemctl restart legacy-portal"]' \
  --query 'Command.CommandId' --output text)"

aws ssm wait command-executed --command-id "${COMMAND_ID}" --instance-id "${INSTANCE_ID}"

echo "[rehost-deploy] Waiting for health check at ${APP_URL}/health..."
for _ in $(seq 1 30); do
  if curl -fsS "${APP_URL}/health" >/dev/null 2>&1; then
    echo "[rehost-deploy] legacy-portal is UP: ${APP_URL}/health"
    exit 0
  fi
  sleep 5
done

echo "[rehost-deploy] ERROR: health check did not pass at ${APP_URL}/health" >&2
exit 1
