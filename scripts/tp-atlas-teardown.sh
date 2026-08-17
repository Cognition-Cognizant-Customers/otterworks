#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS="${1:-}"
if [[ -z "${NS}" || ! "${NS}" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "NS must contain only letters, digits, and underscores" >&2
  exit 2
fi
if [[ "${NS}" == "demo" && "${FORCE:-0}" != "1" ]]; then
  echo "refusing to tear down NS=demo without FORCE=1" >&2
  exit 2
fi

TF_DIR="${REPO_ROOT}/infrastructure/terraform/tp-mongodb"
export TF_VAR_project_id="${MONGODB_ATLAS_PROJECT_ID:?MONGODB_ATLAS_PROJECT_ID is required}"
export TF_VAR_public_key="${MONGODB_ATLAS_PUBLIC_KEY:?MONGODB_ATLAS_PUBLIC_KEY is required}"
export TF_VAR_private_key="${MONGODB_ATLAS_PRIVATE_KEY:?MONGODB_ATLAS_PRIVATE_KEY is required}"
: "${MONGODB_ATLAS_URI:?MONGODB_ATLAS_URI is required}"
terraform -chdir="${TF_DIR}" init -input=false >/dev/null
terraform -chdir="${TF_DIR}" workspace select "${NS}" >/dev/null 2>&1 ||
  terraform -chdir="${TF_DIR}" workspace new "${NS}" >/dev/null
workspace="$(terraform -chdir="${TF_DIR}" workspace show)"
if [[ "${workspace}" == "default" || "${workspace}" != "${NS}" ]]; then
  echo "refusing to operate in Terraform workspace ${workspace}; expected ${NS}" >&2
  exit 1
fi
if state="$(terraform -chdir="${TF_DIR}" state list 2>/dev/null)" && [[ -n "${state}" ]]; then
  details="$(terraform -chdir="${TF_DIR}" state show -no-color mongodbatlas_database_user.namespace 2>/dev/null || true)"
  acl_details="$(terraform -chdir="${TF_DIR}" state show -no-color mongodbatlas_project_ip_access_list.caller 2>/dev/null || true)"
  expected_user="ow-tp-${NS,,}"
  expected_comment="otterworks-tp track=mongodb namespace=${NS}"
  if [[ "${details}" != *"username"* || "${details}" != *"${expected_user}"* ||
        "${details}" != *"ow_tp_${NS,,}"* ||
        "${acl_details}" != *"${expected_comment}"* ]]; then
    echo "refusing to destroy Terraform state that is not owned by NS=${NS}" >&2
    exit 1
  fi
fi

uv run --no-project --with pymongo==4.10.1 \
  python3 "${REPO_ROOT}/scripts/tp_atlas_teardown.py" --ns "${NS}"

terraform -chdir="${TF_DIR}" destroy -auto-approve -input=false \
  -var="ns=${NS}"

if state="$(terraform -chdir="${TF_DIR}" state list 2>/dev/null)" && [[ -n "${state}" ]]; then
  echo "negative verification FAILED: Terraform state still contains ${state}" >&2
  exit 1
fi
echo "negative verification: no namespace-scoped Terraform objects remain"
