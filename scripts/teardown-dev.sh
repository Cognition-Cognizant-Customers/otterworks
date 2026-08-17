#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# OtterWorks - Dev Teardown (cost saver)
#
# Default mode ("keep data"): destroys ONLY the expensive EKS compute
#   (control plane ~$73/mo + nodes) via a targeted destroy of the EKS module.
#   The VPC, ECR repos (and their images), RDS, Redis, S3, DynamoDB, Cognito,
#   SQS/SNS and all data are PRESERVED so a later spin-up is fast and lossless.
#
# Full mode (--all): destroys EVERYTHING (application infra layer first, then
#   the platform layer). This deletes RDS/Redis/S3 data. Use with care.
#
# Usage:
#   ./scripts/teardown-dev.sh                 # keep data, drop EKS compute
#   ./scripts/teardown-dev.sh --all           # full nuke (destroys data)
#   ./scripts/teardown-dev.sh --yes           # skip the interactive confirmation
#
# Requires: terraform, aws. AWS creds must be exported in the environment.
# Optional guard: set EXPECTED_ACCOUNT_ID to abort if the active account differs.
# ------------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
AWS_REGION="${AWS_REGION:-us-east-1}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[teardown]${NC} $*"; }
warn() { echo -e "${YELLOW}[teardown]${NC} $*"; }
err()  { echo -e "${RED}[teardown]${NC} $*" >&2; }

MODE="eks-only"
ASSUME_YES=false
for arg in "$@"; do
  case "$arg" in
    --all) MODE="all" ;;
    --yes|-y) ASSUME_YES=true ;;
    *) err "Unknown argument: $arg"; exit 1 ;;
  esac
done

command -v terraform >/dev/null 2>&1 || { err "terraform not found"; exit 1; }
command -v aws >/dev/null 2>&1       || { err "aws CLI not found"; exit 1; }

# --- Account safety check ---
ACTIVE_ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)"
[[ -n "${ACTIVE_ACCOUNT}" ]] || { err "Unable to resolve AWS account (are creds exported?)"; exit 1; }
log "Active AWS account: ${ACTIVE_ACCOUNT} (region ${AWS_REGION})"
if [[ -n "${EXPECTED_ACCOUNT_ID:-}" ]] && [[ "${EXPECTED_ACCOUNT_ID}" != "${ACTIVE_ACCOUNT}" ]]; then
  err "Active account ${ACTIVE_ACCOUNT} != EXPECTED_ACCOUNT_ID ${EXPECTED_ACCOUNT_ID}. Aborting."
  exit 1
fi

# --- Confirmation ---
if [[ "${MODE}" = "all" ]]; then
  warn "FULL teardown selected: this DESTROYS RDS/Redis/S3 data and all infra."
  CONFIRM_WORD="destroy-all"
else
  log  "Keep-data teardown: drops the EKS cluster + nodes only (data preserved)."
  CONFIRM_WORD="destroy-eks"
fi
if [[ "${ASSUME_YES}" = false ]]; then
  read -r -p "Type '${CONFIRM_WORD}' to proceed: " reply
  [[ "${reply}" = "${CONFIRM_WORD}" ]] || { err "Confirmation mismatch, aborting."; exit 1; }
fi

if [[ "${MODE}" = "all" ]]; then
  # Destroy the application infra layer FIRST (it depends on the platform VPC/OIDC),
  # then the platform layer. Note: S3 buckets that still contain objects will block
  # destroy unless emptied first.
  log "Destroying application infrastructure layer..."
  terraform -chdir="${REPO_ROOT}/infrastructure/terraform" init -input=false
  terraform -chdir="${REPO_ROOT}/infrastructure/terraform" destroy \
    -var="db_password=unused-during-destroy" -auto-approve -input=false

  log "Destroying platform layer (VPC, EKS, ECR)..."
  terraform -chdir="${REPO_ROOT}/platform/terraform" init -input=false
  terraform -chdir="${REPO_ROOT}/platform/terraform" destroy \
    -var-file=environments/dev.tfvars -auto-approve -input=false

  log "Full teardown complete. Everything destroyed."
else
  # Targeted destroy of just the EKS module: removes the control plane, node group
  # and add-ons while leaving the VPC + ECR (and the whole app-infra layer) intact.
  log "Destroying EKS compute only (module.eks); VPC, ECR and app data are kept..."
  terraform -chdir="${REPO_ROOT}/platform/terraform" init -input=false
  terraform -chdir="${REPO_ROOT}/platform/terraform" destroy \
    -target=module.eks -var-file=environments/dev.tfvars -auto-approve -input=false

  log "EKS torn down. Residual cost is just RDS/Redis/S3/DynamoDB (~\$25/mo)."
  log "Spin back up with: AWS_ACCOUNT_ID=<id> DB_PASSWORD=<pw> ./scripts/spinup-dev.sh"
fi
