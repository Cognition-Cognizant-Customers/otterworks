#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# Deploy PgBouncer in otterworks-platform so total RDS connections stay bounded
# as tenants scale. See docs/scaling.md §3 and k8s/pgbouncer.yaml.
#
# The credential comes from Secrets Manager (otterworks/dev/rds/master) by
# default, or from DB_PASSWORD if it is already in the environment. It is never
# passed on a process argv and never echoed: the userlist Secret is applied as
# YAML on stdin.
#
# Idempotent: safe to re-run, including after the password is rotated.
#
# Usage:
#   demo-platform/scripts/install-pgbouncer.sh
#   RDS_SECRET_ID=... AWS_REGION=... install-pgbouncer.sh
# ------------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS=otterworks-platform
AWS_REGION="${AWS_REGION:-us-east-1}"
RDS_SECRET_ID="${RDS_SECRET_ID:-otterworks/dev/rds/master}"

log()  { echo "[pgbouncer] $*"; }
fail() { echo "[pgbouncer] ERROR: $*" >&2; exit 1; }

for bin in kubectl jq; do
  command -v "${bin}" >/dev/null 2>&1 || fail "${bin} is required"
done

# Everything the pooler needs is in the vaulted RDS secret, so the normal path
# takes no arguments at all. Read it once into a variable rather than piping the
# fields separately: each read is an audit-logged retrieval of the master
# credential.
if [ -z "${DB_PASSWORD:-}" ] || [ -z "${RDS_HOST:-}" ]; then
  command -v aws >/dev/null 2>&1 || fail "aws CLI is required to read ${RDS_SECRET_ID}"
  log "reading ${RDS_SECRET_ID} from Secrets Manager..."
  secret="$(aws secretsmanager get-secret-value --region "${AWS_REGION}" \
              --secret-id "${RDS_SECRET_ID}" --query SecretString --output text 2>/dev/null)" \
    || fail "could not read ${RDS_SECRET_ID}; set RDS_HOST/DB_USER/DB_PASSWORD instead"
  RDS_HOST="${RDS_HOST:-$(printf '%s' "${secret}" | jq -r '.host // empty')}"
  RDS_PORT="${RDS_PORT:-$(printf '%s' "${secret}" | jq -r '.port // 5432')}"
  DB_USER="${DB_USER:-$(printf '%s' "${secret}" | jq -r '.username // empty')}"
  DB_PASSWORD="$(printf '%s' "${secret}" | jq -r '.password // empty')"
  unset secret
fi

RDS_PORT="${RDS_PORT:-5432}"
DB_USER="${DB_USER:-otterworks_admin}"
[ -n "${RDS_HOST:-}" ]     || fail "RDS_HOST is empty"
[ -n "${DB_PASSWORD:-}" ]  || fail "DB_PASSWORD is empty"

# The endpoint may be stored with the port attached.
RDS_HOST="${RDS_HOST%%:*}"

kubectl create namespace "${NS}" --dry-run=client -o yaml | kubectl apply -f -

# PgBouncer's auth_file, in its own format: "user" "password", one per line.
# Applied as YAML on stdin so the password never lands in a file, an argv, or
# the shell history. stringData, so no base64 step handles it either.
log "writing userlist Secret (credential stays on stdin)..."
kubectl apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: pgbouncer-auth
  namespace: ${NS}
  labels: { app: pgbouncer }
type: Opaque
stringData:
  userlist.txt: |
    "${DB_USER}" "${DB_PASSWORD}"
EOF

# PgBouncer reads its config and userlist only at startup, and a ConfigMap or
# Secret changing underneath a running pod restarts nothing. Without this the
# pods keep serving the previous pool sizes, or authenticating with a rotated
# password, until something unrelated happens to restart them. Checksum the
# rendered config as well as the credential: pool_mode and the connection caps
# are exactly the kind of edit that looks applied and is not.
rendered="$(sed -e "s#__RDS_HOST__#${RDS_HOST}#g" \
                -e "s#__RDS_PORT__#${RDS_PORT}#g" \
                -e "s#__DB_USER__#${DB_USER}#g" \
                "${HERE}/../k8s/pgbouncer.yaml")"
checksum="$(printf '%s|%s' "${rendered}" "${DB_PASSWORD}" | sha256sum | cut -c1-16)"

log "applying pooler (host=${RDS_HOST} port=${RDS_PORT} user=${DB_USER})..."
printf '%s' "${rendered}" | sed -e "s#__CONFIG_CHECKSUM__#${checksum}#g" | kubectl apply -f -

kubectl -n "${NS}" rollout status deploy/pgbouncer --timeout=180s
log "ready at pgbouncer.${NS}:6432 (transaction pooling, wildcard database routing)"
log "           pgbouncer.${NS}:6433 (session pooling, for migrations -- see docs/scaling.md)"
# The image ships no psql, so pool state is read from a throwaway client.
log "pool state: kubectl -n ${NS} run pgb-pools --rm -it --restart=Never --image=postgres:15-alpine \\"
log "              --env=PGPASSWORD=... -- psql -h pgbouncer -p 6432 -U ${DB_USER} pgbouncer -c 'SHOW POOLS'"
