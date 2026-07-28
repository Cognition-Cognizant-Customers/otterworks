#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# OtterWorks - Shared Platform Baseline for Multi-Tenant Demos (run ONCE)
#
# These are SHARED, cluster-wide pieces consumed by every tenant — conceptually
# they belong in platform-engineering-shared-services and must NOT be duplicated
# per tenant (see docs/MULTI-TENANT-DEMO-PLAN.md §2/§8). This script installs:
#   1. ingress-nginx (one shared controller + one NLB) -> host/path tenant routing
#   2. the namespace TTL reaper CronJob (+ RBAC) in otterworks-system, which
#      deletes tenant namespaces past their demo/expires-at annotation
#
# Idempotent: safe to re-run. Usage:
#   ./scripts/tenant-platform-baseline.sh            # install everything
#   ./scripts/tenant-platform-baseline.sh --no-ingress
# ------------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/tenant-common.sh
source "${SCRIPT_DIR}/lib/tenant-common.sh"
# shellcheck source=lib/ingress-nginx.sh
source "${SCRIPT_DIR}/lib/ingress-nginx.sh"

INSTALL_INGRESS=true
for arg in "$@"; do
  case "$arg" in
    --no-ingress) INSTALL_INGRESS=false ;;
    *) err "Unknown argument: $arg"; exit 1 ;;
  esac
done

require_bins aws kubectl helm
aws eks update-kubeconfig --name "${EKS_CLUSTER}" --region "${AWS_REGION}" --alias "${EKS_CLUSTER}" >/dev/null

# ---------- 1. Shared ingress-nginx (one controller, one NLB) ----------
if [ "${INSTALL_INGRESS}" = true ]; then
  ensure_ingress_nginx
fi

# ---------- 2. Namespace TTL reaper (otterworks-system) ----------
log "Deploying namespace TTL reaper CronJob into ${SYSTEM_NAMESPACE}..."
kubectl create namespace "${SYSTEM_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply -f - <<YAML
apiVersion: v1
kind: ServiceAccount
metadata:
  name: tenant-reaper
  namespace: ${SYSTEM_NAMESPACE}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: tenant-reaper
rules:
  - apiGroups: [""]
    resources: ["namespaces"]
    verbs: ["get", "list", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: tenant-reaper
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: tenant-reaper
subjects:
  - kind: ServiceAccount
    name: tenant-reaper
    namespace: ${SYSTEM_NAMESPACE}
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: tenant-reaper
  namespace: ${SYSTEM_NAMESPACE}
spec:
  schedule: "*/15 * * * *"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      ttlSecondsAfterFinished: 600
      template:
        spec:
          serviceAccountName: tenant-reaper
          restartPolicy: Never
          containers:
            - name: reaper
              image: alpine/k8s:1.30.3
              command: ["/bin/sh","-c"]
              args:
                - |
                  set -eu
                  now=\$(date -u +%s)
                  echo "[reaper] scanning tenant namespaces at \$(date -u)"
                  for ns in \$(kubectl get ns -l app.kubernetes.io/managed-by=otterworks-tenant -o jsonpath='{.items[*].metadata.name}'); do
                    exp_epoch=\$(kubectl get ns "\$ns" -o jsonpath='{.metadata.annotations.demo/expires-at-epoch}' 2>/dev/null || true)
                    if [ -z "\$exp_epoch" ]; then
                      echo "[reaper] \$ns has no demo/expires-at-epoch; skipping"
                      continue
                    fi
                    if [ "\$exp_epoch" -lt "\$now" ]; then
                      echo "[reaper] \$ns expired (\$exp_epoch < \$now) -> deleting"
                      kubectl delete ns "\$ns" --wait=false || true
                    else
                      echo "[reaper] \$ns expires at epoch \$exp_epoch -> keeping"
                    fi
                  done
              resources:
                requests: { cpu: 50m, memory: 64Mi }
                limits: { cpu: 200m, memory: 128Mi }
YAML

log "Platform baseline ready."
log "  - ingress controller namespace: ${INGRESS_NAMESPACE}"
log "  - reaper CronJob: ${SYSTEM_NAMESPACE}/tenant-reaper (every 15m)"
log "Note: the reaper drops namespaces only. Per-tenant RDS databases are dropped"
log "      by teardown-tenant.sh; expired tenants leave the DB until torn down."
