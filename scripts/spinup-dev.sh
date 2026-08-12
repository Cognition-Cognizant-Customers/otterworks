#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# OtterWorks - Dev Spin-up (companion to teardown-dev.sh)
#
# Brings the environment back after a teardown:
#   1. Re-applies the platform layer (VPC/EKS/ECR) -> recreates the EKS cluster
#      (SPOT, single node per environments/dev.tfvars).
#   2. Re-applies the application infra layer (refreshes IRSA trust for the new
#      OIDC provider). Requires DB_PASSWORD; resets the RDS master password in
#      place to the value you pass.
#   3. Configures kubectl and deploys the services via Helm.
#
# By default it REUSES the Docker images already in ECR (fast) and does NOT
# rebuild. Pass --build to rebuild + push all images from source.
#
# Usage:
#   AWS_ACCOUNT_ID=<id> DB_PASSWORD=<pw> ./scripts/spinup-dev.sh
#   AWS_ACCOUNT_ID=<id> DB_PASSWORD=<pw> ./scripts/spinup-dev.sh --build
#   AWS_ACCOUNT_ID=<id> DB_PASSWORD=<pw> ./scripts/spinup-dev.sh --skip-deploy
#
# Env: AWS_ACCOUNT_ID (required), DB_PASSWORD (required unless --skip-infra),
#      AWS_REGION (default us-east-1), NAMESPACE (default otterworks).
# ------------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:?ERROR: AWS_ACCOUNT_ID must be set}"
EKS_CLUSTER="${EKS_CLUSTER:-otterworks-dev}"
NAMESPACE="${NAMESPACE:-otterworks}"
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
ECR_PREFIX="otterworks/"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[spinup]${NC} $*"; }
warn() { echo -e "${YELLOW}[spinup]${NC} $*"; }
err()  { echo -e "${RED}[spinup]${NC} $*" >&2; }

DO_BUILD=false
SKIP_INFRA=false
SKIP_DEPLOY=false
for arg in "$@"; do
  case "$arg" in
    --build) DO_BUILD=true ;;
    --skip-infra) SKIP_INFRA=true ;;
    --skip-deploy) SKIP_DEPLOY=true ;;
    *) err "Unknown argument: $arg"; exit 1 ;;
  esac
done

for bin in aws terraform kubectl helm; do
  command -v "$bin" >/dev/null 2>&1 || { err "$bin not found"; exit 1; }
done

BACKEND_SERVICES=(api-gateway auth-service file-service document-service collab-service \
  notification-service search-service analytics-service admin-service audit-service report-service \
  legacy-portal)
FRONTEND_SERVICES=(web-app admin-dashboard)
ALL_SERVICES=("${BACKEND_SERVICES[@]}" "${FRONTEND_SERVICES[@]}")

# ---------- Step 1: Platform (recreate EKS) ----------
log "Applying platform layer (VPC/EKS/ECR)..."
terraform -chdir="${REPO_ROOT}/platform/terraform" init -input=false
terraform -chdir="${REPO_ROOT}/platform/terraform" apply \
  -var-file=environments/dev.tfvars -auto-approve -input=false

# ---------- Step 2: App infra (refresh IRSA for new OIDC) ----------
if [ "${SKIP_INFRA}" = false ]; then
  DB_PASSWORD="${DB_PASSWORD:?ERROR: DB_PASSWORD must be set (or pass --skip-infra)}"
  log "Applying application infra layer (refreshes IRSA trust; resets RDS password)..."
  terraform -chdir="${REPO_ROOT}/infrastructure/terraform" init -input=false
  terraform -chdir="${REPO_ROOT}/infrastructure/terraform" apply \
    -var="db_password=${DB_PASSWORD}" -auto-approve -input=false
else
  warn "Skipping app infra apply (--skip-infra); IRSA may be stale if OIDC changed."
fi

# ---------- Step 3: kubectl + namespace ----------
log "Configuring kubectl for ${EKS_CLUSTER}..."
aws eks update-kubeconfig --name "${EKS_CLUSTER}" --region "${AWS_REGION}" --alias "${EKS_CLUSTER}"
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

if [ "${SKIP_DEPLOY}" = true ]; then
  log "Infra is up. Skipping app deploy (--skip-deploy). Cluster ready:"
  kubectl get nodes
  exit 0
fi

# ---------- Step 4: Deploy ----------
if [ "${DO_BUILD}" = true ]; then
  log "Rebuilding + pushing all images, then deploying (delegating to deploy-dev.sh)..."
  AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID}" DB_PASSWORD="${DB_PASSWORD:-}" \
    "${REPO_ROOT}/scripts/deploy-dev.sh" --skip-terraform
  exit 0
fi

# Fast path: reuse the newest image tag already in ECR for each service.
log "Logging into ECR (${ECR_REGISTRY})..."
aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "${ECR_REGISTRY}" 2>/dev/null || \
  warn "docker login failed (not required for helm, continuing)"

latest_tag() {
  local repo="${ECR_PREFIX}$1"
  aws ecr describe-images --repository-name "${repo}" --region "${AWS_REGION}" \
    --query 'sort_by(imageDetails,&imagePushedAt)[-1].imageTags[0]' --output text 2>/dev/null
}

log "Deploying services via Helm using existing ECR images..."
FAILED=(); SKIPPED=()
for service in "${ALL_SERVICES[@]}"; do
  chart_dir="${REPO_ROOT}/infrastructure/helm/${service}"
  [ -d "${chart_dir}" ] || { warn "No chart for ${service}, skipping"; continue; }
  tag="$(latest_tag "${service}")"
  if [ -z "${tag}" ] || [ "${tag}" = "None" ]; then
    warn "No image in ECR for ${service}; run with --build to create it. Skipping."
    SKIPPED+=("${service}"); continue
  fi
  log "Deploying ${service} (tag ${tag})..."
  helm upgrade --install "${service}" "${chart_dir}" \
    --namespace "${NAMESPACE}" \
    --set image.repository="${ECR_REGISTRY}/${ECR_PREFIX}${service}" \
    --set image.tag="${tag}" \
    --wait --timeout 5m || { warn "Helm deploy failed for ${service}"; FAILED+=("${service}"); }
done

log "Pod status:"
kubectl get pods -n "${NAMESPACE}" -o wide || true
[ ${#SKIPPED[@]} -gt 0 ] && warn "No ECR image (skipped): ${SKIPPED[*]}"
[ ${#FAILED[@]} -gt 0 ] && { warn "Failed to deploy: ${FAILED[*]}"; exit 1; }
log "Spin-up complete on ${EKS_CLUSTER}/${NAMESPACE}."
