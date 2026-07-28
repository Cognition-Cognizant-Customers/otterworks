#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# OtterWorks - Per-Tenant Ephemeral Demo Deploy
#
# Stands up an isolated copy of the golden app for one attendee/demo run in the
# namespace  otterworks-<ATTENDEE_ID>  on the SHARED otterworks-dev EKS cluster.
# Wraps the config/secret wiring from scripts/deploy-dev.sh (via
# scripts/lib/tenant-common.sh) and layers on tenant isolation + cost controls.
#
# Per tenant this creates:
#   - namespace otterworks-<ID> (TTL-labeled for the reaper)
#   - ResourceQuota + LimitRange + a namespace NetworkPolicy
#   - per-tenant in-cluster Redis + MeiliSearch (chaos/session/search isolation)
#   - a per-tenant RDS database otterworks_<ID> (Postgres data isolation)
#   - all 11 backends + 2 frontends via Helm (replicas=1), frontends on the
#     SHARED ingress (ClusterIP + one Ingress), NOT one LoadBalancer per tenant
#
# Usage:
#   ./scripts/deploy-tenant.sh <ATTENDEE_ID> [--tier A|B] [--image-tag TAG] \
#       [--ttl 8h] [--host-suffix demo.example.com] [--skip-db] \
#       [--profile core|full]
#
# Required env: AWS creds (exported), DB_PASSWORD. Stable JWT_SECRET /
#   SECRET_KEY_BASE recommended across redeploys (auto-generated if unset).
# ------------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/tenant-common.sh
source "${SCRIPT_DIR}/lib/tenant-common.sh"

# ---------- Args ----------
ATTENDEE_ID=""
TIER="A"
IMAGE_TAG_OVERRIDE=""
TENANT_BRANCH_ARG=""
TTL="8h"
HOST_SUFFIX="${HOST_SUFFIX:-}"
SKIP_DB=false
# Deploy only the services the lab needs. At 100 tenants the difference between
# "core" and "full" is roughly 100 vCPU of requests. Defaults to "full" so no
# existing lab loses a service; see profile_services in lib/tenant-common.sh.
PROFILE="${TENANT_PROFILE:-full}"
while [ $# -gt 0 ]; do
  case "$1" in
    --tier)        TIER="$2"; shift 2 ;;
    --image-tag)   IMAGE_TAG_OVERRIDE="$2"; shift 2 ;;
    --branch)      TENANT_BRANCH_ARG="$2"; shift 2 ;;
    --ttl)         TTL="$2"; shift 2 ;;
    --host-suffix) HOST_SUFFIX="$2"; shift 2 ;;
    --profile)     PROFILE="$2"; shift 2 ;;
    --skip-db)     SKIP_DB=true; shift ;;
    -*)            err "Unknown flag: $1"; exit 1 ;;
    *)             if [ -z "${ATTENDEE_ID}" ]; then ATTENDEE_ID="$1"; else err "Unexpected arg: $1"; exit 1; fi; shift ;;
  esac
done

[ -n "${ATTENDEE_ID}" ] || { err "Usage: $0 <ATTENDEE_ID> [--tier A|B] [--image-tag TAG] [--branch BRANCH] [--ttl 8h|never] [--profile core|full]"; exit 1; }
case "${TIER}" in A|B) ;; *) err "--tier must be A or B"; exit 1 ;; esac
case "${PROFILE}" in core|full) ;; *) err "--profile must be core or full"; exit 1 ;; esac
mapfile -t TENANT_SERVICES < <(profile_services "${PROFILE}")

require_bins aws kubectl helm terraform jq
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text 2>/dev/null)}"
[ -n "${AWS_ACCOUNT_ID}" ] || { err "Unable to resolve AWS account (are creds exported?)"; exit 1; }
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
DB_PASSWORD="${DB_PASSWORD:?ERROR: DB_PASSWORD must be set}"
JWT_SECRET="${JWT_SECRET:-$(openssl rand -hex 32)}"
SECRET_KEY_BASE="${SECRET_KEY_BASE:-$(openssl rand -hex 64)}"

NS="$(tenant_namespace "${ATTENDEE_ID}")"
T_DB_NAME="$(tenant_db_name "${ATTENDEE_ID}")"
T_REDIS_HOST="redis"
T_MEILI_URL="http://meilisearch:7700"
# Tier A shares SNS/SQS eventing off by default to avoid cross-tenant queue
# consumption; Tier B (data-isolated) can opt in later. Kept off for both here.
T_WIRE_EVENTING="false"
# Convert a compact TTL (e.g. 8h, 30m, 2d) into an absolute UTC expiry, working
# with both GNU date (-d "8 hours") and BSD/macOS date (-v+8H).
# `never` marks a perpetual tenant: the reaper skips it on the control table's
# `persistent` flag, and the ten-year stamp is the backstop for the namespace
# label if that check ever regresses.
ttl_to_expiry() {
  local ttl="$1" num unit gnu bsd
  if [ "${ttl}" = "never" ]; then
    date -u -d "+3650 days" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v+3650d +%Y-%m-%dT%H:%M:%SZ
    return 0
  fi
  num="${ttl%%[!0-9]*}"; unit="${ttl##*[0-9]}"
  [ -n "${num}" ] || { err "Invalid --ttl '${ttl}' (use e.g. 8h, 30m, 2d)"; exit 1; }
  case "${unit}" in
    h|H|"") gnu="${num} hours";   bsd="+${num}H" ;;
    m|M)    gnu="${num} minutes"; bsd="+${num}M" ;;
    d|D)    gnu="${num} days";    bsd="+${num}d" ;;
    *)      err "Invalid --ttl unit in '${ttl}' (use h, m, or d)"; exit 1 ;;
  esac
  date -u -d "+${gnu}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v"${bsd}" +%Y-%m-%dT%H:%M:%SZ
}
EXPIRES_AT="$(ttl_to_expiry "${TTL}")"
# Epoch form for the reaper: it compares integers only (no ISO parsing), so the
# reaper image needs nothing more than `date +%s`.
EXPIRES_EPOCH="$(date -u -d "${EXPIRES_AT}" +%s 2>/dev/null || date -u -jf %Y-%m-%dT%H:%M:%SZ "${EXPIRES_AT}" +%s)"

log "Tenant '${ATTENDEE_ID}' -> namespace ${NS} (tier ${TIER}, ttl ${TTL} -> expires ${EXPIRES_AT})"

# ---------- kubectl + shared infra outputs ----------
# In-cluster (runner Job) the pod's ServiceAccount already has cluster access via
# RBAC; writing a kubeconfig would instead auth as the IRSA IAM role, which is
# not mapped in aws-auth. Only build a kubeconfig when running outside the cluster.
if [ -z "${KUBERNETES_SERVICE_HOST:-}" ]; then
  aws eks update-kubeconfig --name "${EKS_CLUSTER}" --region "${AWS_REGION}" --alias "${EKS_CLUSTER}" >/dev/null
fi
log "Loading shared application-infra Terraform outputs..."
load_infra_outputs

# ---------- Namespace + isolation guardrails ----------
log "Creating namespace ${NS} with quota / limits / network policy..."
kubectl apply -f - <<YAML
apiVersion: v1
kind: Namespace
metadata:
  name: ${NS}
  labels:
    app.kubernetes.io/managed-by: otterworks-tenant
    platform/environment: dev
    platform/team: otterworks
    demo/tenant: "$(sanitize_id "${ATTENDEE_ID}")"
    demo/tier: "${TIER}"
    demo/profile: "${PROFILE}"
    kubernetes.io/metadata.name: ${NS}
  annotations:
    demo/expires-at: "${EXPIRES_AT}"
    demo/expires-at-epoch: "${EXPIRES_EPOCH}"
    demo/attendee-id: "${ATTENDEE_ID}"
---
apiVersion: v1
kind: ResourceQuota
metadata:
  name: tenant-quota
  namespace: ${NS}
spec:
  hard:
    # Requests are what actually reserve node capacity, so they stay tight --
    # this is the number that decides how many tenants fit on the cluster.
    requests.cpu: "4"
    requests.memory: 8Gi
    # Limits only cap bursting, but the quota counts them, and the full service
    # set declares ~9.25 CPU of limits. At 8 the last two Deployments to be
    # created were rejected by the quota and simply never appeared -- the
    # namespace looked healthy because the failure lands on the ReplicaSet, not
    # on a pod. Sized above the profile's total rather than by trimming limits,
    # which would only make services throttle under load.
    limits.cpu: "12"
    limits.memory: 20Gi
    pods: "40"
---
apiVersion: v1
kind: LimitRange
metadata:
  name: tenant-limits
  namespace: ${NS}
spec:
  limits:
    - type: Container
      default:
        cpu: 500m
        memory: 256Mi
      defaultRequest:
        cpu: 100m
        memory: 128Mi
---
# Tenant isolation: allow traffic only from within this namespace, the shared
# ingress controller, and monitoring. Cross-tenant pod-to-pod traffic is denied.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: tenant-isolation
  namespace: ${NS}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ${NS}
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ${INGRESS_NAMESPACE}
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: monitoring
YAML

# ---------- IRSA trust: allow this tenant namespace to assume the shared roles ----------
ensure_irsa_trust() {
  local d="${REPO_ROOT}/infrastructure/terraform"
  local oidc_url; oidc_url="$(terraform -chdir="${REPO_ROOT}/platform/terraform" output -raw oidc_provider_url 2>/dev/null || echo "")"
  # In-cluster the platform/terraform state isn't initialized; fall back to the
  # cluster's OIDC issuer via the EKS API (the runner IRSA role has
  # eks:DescribeCluster). Without this the per-namespace trust is skipped and
  # tenant pods can't assume the shared roles (AWS ops fail).
  if [ -z "${oidc_url}" ]; then
    oidc_url="$(aws eks describe-cluster --name "${EKS_CLUSTER}" --region "${AWS_REGION}" \
      --query 'cluster.identity.oidc.issuer' --output text 2>/dev/null || echo "")"
  fi
  oidc_url="${oidc_url#https://}"
  [ -n "${oidc_url}" ] || { warn "OIDC provider URL unavailable; skipping IRSA trust update (IRSA may fail for ${NS})"; return 0; }
  local svc role sub
  for svc in $(echo "${IRSA_JSON}" | jq -r 'keys[]'); do
    role="otterworks-${svc}-dev"
    sub="system:serviceaccount:${NS}:${svc}"
    local doc; doc="$(aws iam get-role --role-name "${role}" --query 'Role.AssumeRolePolicyDocument' --output json 2>/dev/null || echo "")"
    [ -n "${doc}" ] || { warn "role ${role} not found; skipping"; continue; }
    # Skip if the sub is already trusted — either an exact StringEquals entry or
    # a StringLike wildcard (e.g. the Terraform-managed "otterworks-*" pattern)
    # that already matches this namespace. Checking only StringEquals would make
    # us append a redundant statement on every deploy and bloat the trust policy
    # (IAM trust docs cap at 2048/4096 chars) until deploys start failing.
    local trusted already=false pat
    trusted="$(echo "${doc}" | jq -r --arg url "${oidc_url}" '
      [ .Statement[]?.Condition
        | (.StringEquals[$url+":sub"], .StringLike[$url+":sub"])
        | select(. != null)
        | if type=="array" then .[] else . end ] | .[]' 2>/dev/null)"
    while IFS= read -r pat; do
      [ -n "${pat}" ] || continue
      # shellcheck disable=SC2254  # glob-match the exact sub against trust patterns
      case "${sub}" in ${pat}) already=true; break ;; esac
    done <<EOF
${trusted}
EOF
    [ "${already}" = true ] && continue
    # Append an AssumeRoleWithWebIdentity statement scoped to this namespace SA.
    local new; new="$(echo "${doc}" | jq --arg sub "${sub}" --arg url "${oidc_url}" '
      .Statement += [{
        Effect: "Allow",
        Action: "sts:AssumeRoleWithWebIdentity",
        Principal: (.Statement[0].Principal),
        Condition: { StringEquals: { ($url+":sub"): $sub, ($url+":aud"): "sts.amazonaws.com" } }
      }]')"
    aws iam update-assume-role-policy --role-name "${role}" --policy-document "${new}" >/dev/null \
      && log "  IRSA trust: ${role} now trusts ${sub}" \
      || warn "  failed to update trust for ${role}"
  done
}
log "Ensuring shared IRSA roles trust the tenant namespace service accounts..."
ensure_irsa_trust

# ---------- Per-tenant RDS database (Postgres data isolation) ----------
create_tenant_database() {
  [ -n "${RDS_HOST}" ] || { warn "RDS endpoint unknown; skipping per-tenant DB (services will share the default DB)"; return 0; }
  log "Ensuring per-tenant database ${T_DB_NAME} exists on shared RDS (in-cluster job)..."
  kubectl -n "${NS}" delete job tenant-db-init --ignore-not-found >/dev/null 2>&1 || true
  apply_db_admin_secret "${NS}"
  kubectl apply -n "${NS}" -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: tenant-db-init
spec:
  backoffLimit: 2
  ttlSecondsAfterFinished: 120
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: psql
          image: postgres:16-alpine
          env:
            - name: PGPASSWORD
              valueFrom: { secretKeyRef: { name: tenant-db-admin, key: PGPASSWORD } }
          command: ["/bin/sh","-c"]
          args:
            - |
              set -e
              CONN="host=${RDS_HOST} port=${RDS_PORT} dbname=otterworks user=${DB_USER} sslmode=prefer connect_timeout=10"
              if psql "\$CONN" -tAc "SELECT 1 FROM pg_database WHERE datname='${T_DB_NAME}'" | grep -q 1; then
                echo "database ${T_DB_NAME} already exists"
              else
                psql "\$CONN" -c "CREATE DATABASE \"${T_DB_NAME}\""
                echo "created database ${T_DB_NAME}"
              fi
              # analytics-service keeps its tables in an \`analytics\` schema and
              # asks for it with the JDBC \`currentSchema\` option, which the
              # driver sends as a search_path startup parameter. PgBouncer never
              # forwards that parameter to the server, so through the pooler the
              # service would query \`public\` and find none of its own tables.
              # A database-level default is applied by the server itself and so
              # survives pooling. public stays first: everything else in this
              # app, including the other services' migrations, lives there.
              psql "\$CONN" -c 'ALTER DATABASE "${T_DB_NAME}" SET search_path = public, analytics'
          resources:
            requests: { cpu: 50m, memory: 64Mi }
            limits: { cpu: 200m, memory: 128Mi }
YAML
  if kubectl -n "${NS}" wait --for=condition=complete job/tenant-db-init --timeout=120s >/dev/null 2>&1; then
    log "  per-tenant database ready."
  else
    warn "  per-tenant DB init did not complete; check: kubectl -n ${NS} logs job/tenant-db-init"
    kubectl -n "${NS}" logs job/tenant-db-init 2>/dev/null | tail -5 || true
  fi
  kubectl -n "${NS}" delete secret tenant-db-admin --ignore-not-found >/dev/null 2>&1 || true
}
if [ "${SKIP_DB}" = true ]; then
  warn "--skip-db set: using the shared default database (no Postgres data isolation)."
  T_DB_NAME="otterworks"
else
  create_tenant_database
fi

# ---------- Per-tenant Redis + MeiliSearch ----------
log "Deploying per-tenant Redis + MeiliSearch..."
kubectl apply -n "${NS}" -f - <<'YAML'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
  labels: { app: redis }
spec:
  replicas: 1
  selector: { matchLabels: { app: redis } }
  template:
    metadata:
      labels: { app: redis }
    spec:
      containers:
        - name: redis
          image: redis:7-alpine
          args: ["--save","","--appendonly","no"]
          ports: [{ containerPort: 6379 }]
          readinessProbe:
            tcpSocket: { port: 6379 }
            initialDelaySeconds: 3
            periodSeconds: 10
          resources:
            requests: { cpu: 50m, memory: 64Mi }
            limits: { cpu: 250m, memory: 256Mi }
---
apiVersion: v1
kind: Service
metadata:
  name: redis
  labels: { app: redis }
spec:
  selector: { app: redis }
  ports: [{ port: 6379, targetPort: 6379 }]
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: meilisearch
  labels: { app: meilisearch }
spec:
  replicas: 1
  selector: { matchLabels: { app: meilisearch } }
  template:
    metadata:
      labels: { app: meilisearch }
    spec:
      containers:
        - name: meilisearch
          image: getmeili/meilisearch:v1.8
          env:
            - { name: MEILI_ENV, value: "development" }
            - { name: MEILI_NO_ANALYTICS, value: "true" }
          ports: [{ containerPort: 7700 }]
          readinessProbe:
            httpGet: { path: /health, port: 7700 }
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests: { cpu: 100m, memory: 256Mi }
            limits: { cpu: 500m, memory: 512Mi }
---
apiVersion: v1
kind: Service
metadata:
  name: meilisearch
  labels: { app: meilisearch }
spec:
  selector: { app: meilisearch }
  ports: [{ port: 7700, targetPort: 7700 }]
YAML
kubectl -n "${NS}" rollout status deployment/redis --timeout=120s || warn "redis not ready"
kubectl -n "${NS}" rollout status deployment/meilisearch --timeout=180s || warn "meilisearch not ready"

# ---------- Resolve image tags ----------
log "Logging into ECR to resolve image tags..."
aws ecr get-login-password --region "${AWS_REGION}" | \
  docker login --username AWS --password-stdin "${ECR_REGISTRY}" >/dev/null 2>&1 || true
latest_tag() {
  aws ecr describe-images --repository-name "${ECR_PREFIX}$1" --region "${AWS_REGION}" \
    --query 'sort_by(imageDetails,&imagePushedAt)[-1].imageTags[0]' --output text 2>/dev/null
}

tag_exists() {
  aws ecr describe-images --repository-name "${ECR_PREFIX}$1" --image-ids "imageTag=$2" \
    --region "${AWS_REGION}" >/dev/null 2>&1
}

# CI publishes each service a branch changed as `tenant-<id>` (and `main` for
# the golden app), so a tenant runs its branch's build of the services that
# branch touched and the golden build of everything else. Without this the
# fallback is "newest image pushed to the repo", which is whichever branch built
# last -- i.e. another tenant's code.
TENANT_TAG=""
[ -n "${TENANT_BRANCH_ARG}" ] &&
  TENANT_TAG="$(tenant_image_tag "${ATTENDEE_ID}")"

resolve_tag() {
  local service="$1"
  if [ -n "${TENANT_TAG}" ] && tag_exists "${service}" "${TENANT_TAG}"; then
    echo "${TENANT_TAG}"; return 0
  fi
  if tag_exists "${service}" main; then
    echo "main"; return 0
  fi
  latest_tag "${service}"
}

# ---------- Deploy services via Helm ----------
deploy_service() {
  local service=$1
  local chart_dir="${REPO_ROOT}/infrastructure/helm/${service}"
  [ -d "${chart_dir}" ] || { warn "No chart for ${service}, skipping"; return 0; }

  local tag="${IMAGE_TAG_OVERRIDE}"
  # Per-service image tag override: BUG_IMAGE_TAG_<service_with_underscores>
  local var="BUG_IMAGE_TAG_${service//-/_}"
  [ -n "${!var:-}" ] && tag="${!var}"
  [ -z "${tag}" ] && tag="$(resolve_tag "${service}")"
  if [ -z "${tag}" ] || [ "${tag}" = "None" ]; then
    warn "No image in ECR for ${service}; skipping."
    return 0
  fi

  build_helm_args "${service}"
  local secret_file="" secret_args=()
  if [ "${#SECRET_KV[@]}" -gt 0 ]; then
    secret_file="$(mktemp)"; chmod 600 "${secret_file}"
    jq -n --args '{secrets: (reduce range(0; ($ARGS.positional | length); 2) as $i
      ({}; . + {($ARGS.positional[$i]): $ARGS.positional[$i + 1]}))}' \
      "${SECRET_KV[@]}" > "${secret_file}"
    secret_args=(-f "${secret_file}")
  fi

  log "Deploying ${service} (tag ${tag})..."
  helm upgrade --install "${service}" "${chart_dir}" \
    --namespace "${NS}" \
    --set image.repository="${ECR_REGISTRY}/${ECR_PREFIX}${service}" \
    --set image.tag="${tag}" \
    "${EXTRA_ARGS[@]}" \
    "${secret_args[@]}" \
    --timeout 4m \
    && local rc=0 || local rc=1
  [ -n "${secret_file}" ] && rm -f "${secret_file}"
  if [ "${rc}" -ne 0 ]; then
    warn "Helm deploy failed for ${service}"
    return 1
  fi
  return 0
}

log "Deploying services into ${NS} (profile=${PROFILE}, ${#TENANT_SERVICES[@]} services)..."
FAILED=()
for service in "${TENANT_SERVICES[@]}"; do
  deploy_service "${service}" || FAILED+=("${service}")
done

# ---------- Shared ingress (host/path routing, ONE shared ALB/NLB) ----------
apply_ingress() {
  local sid; sid="$(sanitize_id "${ATTENDEE_ID}")"
  if [ -n "${HOST_SUFFIX}" ]; then
    # Preferred: host-based routing. One shared ingress controller / ELB fronts
    # every tenant; the web host serves the SPA, the api host the gateway.
    local web_host="t-${sid}.${HOST_SUFFIX}"
    local api_host="api-t-${sid}.${HOST_SUFFIX}"
    log "Applying shared ingress for ${NS} (hosts ${web_host}, ${api_host})..."
    kubectl apply -n "${NS}" -f - <<YAML
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: tenant-ingress
spec:
  ingressClassName: nginx
  rules:
    - host: ${web_host}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service: { name: web-app, port: { number: 80 } }
    - host: ${api_host}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service: { name: api-gateway, port: { number: 8080 } }
YAML
  else
    # Fallback: path-based routing on the shared ingress IP when no wildcard DNS
    # is available. The SPA is best reached with a base path; the gateway is
    # rewritten so /<id>/api/v1/... -> /api/v1/... on the backend.
    log "Applying shared ingress for ${NS} (path /${sid} , no host suffix)..."
    kubectl apply -n "${NS}" -f - <<YAML
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: tenant-ingress-api
  annotations:
    nginx.ingress.kubernetes.io/use-regex: "true"
    nginx.ingress.kubernetes.io/rewrite-target: /api/\$2
spec:
  ingressClassName: nginx
  rules:
    - http:
        paths:
          - path: /${sid}/api(/|\$)(.*)
            pathType: ImplementationSpecific
            backend:
              service: { name: api-gateway, port: { number: 8080 } }
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: tenant-ingress-web
  annotations:
    nginx.ingress.kubernetes.io/use-regex: "true"
    nginx.ingress.kubernetes.io/rewrite-target: /\$2
spec:
  ingressClassName: nginx
  rules:
    - http:
        paths:
          - path: /${sid}(/|\$)(.*)
            pathType: ImplementationSpecific
            backend:
              service: { name: web-app, port: { number: 80 } }
YAML
  fi
}
if kubectl get ns "${INGRESS_NAMESPACE}" >/dev/null 2>&1; then
  apply_ingress
else
  warn "No '${INGRESS_NAMESPACE}' namespace — shared ingress controller not installed."
  warn "Run scripts/tenant-platform-baseline.sh once to install it. Frontends are ClusterIP-only for now."
fi

# ---------- Summary ----------
echo ""
log "Tenant ${ATTENDEE_ID} deployed to namespace ${NS}."
kubectl get pods -n "${NS}" -o wide || true
if [ ${#FAILED[@]} -gt 0 ]; then
  warn "Services with deploy issues: ${FAILED[*]}"
fi
echo ""
log "Inspect:   kubectl get all -n ${NS}"
log "Reach API: kubectl -n ${NS} port-forward svc/api-gateway 8080:8080"
log "Inject bug: ./scripts/inject-bug.sh ${ATTENDEE_ID} <scenario>"
log "Teardown:  ./scripts/teardown-tenant.sh ${ATTENDEE_ID}"
