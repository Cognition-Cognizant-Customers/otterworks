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
ARTIFACT_KEY="$(terraform -chdir="${TF_DIR}" output -raw artifact_key)"
INSTANCE_ID="$(terraform -chdir="${TF_DIR}" output -raw instance_id)"
APP_URL="$(terraform -chdir="${TF_DIR}" output -raw app_url)"

echo "[rehost-deploy] Uploading JAR to s3://${ARTIFACT_BUCKET}/${ARTIFACT_KEY}..."
aws s3 cp "${JAR}" "s3://${ARTIFACT_BUCKET}/${ARTIFACT_KEY}"

echo "[rehost-deploy] Restarting legacy-portal on ${INSTANCE_ID} via SSM..."
COMMAND_ID="$(aws ssm send-command \
  --instance-ids "${INSTANCE_ID}" \
  --document-name "AWS-RunShellScript" \
  --comment "rehost-deploy: refresh legacy-portal.jar and restart" \
  --parameters 'commands=["set -e","/opt/legacy-portal/fetch-jar.sh","systemctl restart legacy-portal"]' \
  --query 'Command.CommandId' --output text)"

aws ssm wait command-executed --command-id "${COMMAND_ID}" --instance-id "${INSTANCE_ID}"

# Health-check on the instance itself via SSM: ingress on 8095 is opt-in
# (app_ingress_cidr_blocks defaults to []), so the public URL may be unreachable
# even when the deploy succeeded.
echo "[rehost-deploy] Health-checking on-instance via SSM..."
HEALTH_COMMAND_ID="$(aws ssm send-command \
  --instance-ids "${INSTANCE_ID}" \
  --document-name "AWS-RunShellScript" \
  --comment "rehost-deploy: on-instance health check" \
  --parameters 'commands=["for i in $(seq 1 30); do curl -fsS http://localhost:8095/health >/dev/null 2>&1 && exit 0; sleep 5; done; exit 1"]' \
  --query 'Command.CommandId' --output text)"

# Poll get-command-invocation explicitly: the default `aws ssm wait` budget
# (~100s) is shorter than the health command's own retry window (~150s).
for _ in $(seq 1 40); do
  STATUS="$(aws ssm get-command-invocation \
    --command-id "${HEALTH_COMMAND_ID}" --instance-id "${INSTANCE_ID}" \
    --query Status --output text 2>/dev/null || echo Pending)"
  case "${STATUS}" in
    Success)
      echo "[rehost-deploy] legacy-portal is UP (localhost:8095/health on ${INSTANCE_ID})"
      echo "[rehost-deploy] External URL (requires app_ingress_cidr_blocks): ${APP_URL}"
      exit 0
      ;;
    Failed | Cancelled | TimedOut)
      break
      ;;
  esac
  sleep 5
done

echo "[rehost-deploy] ERROR: on-instance health check did not pass" >&2
exit 1
