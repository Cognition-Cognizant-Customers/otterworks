#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# OtterWorks - Full Standalone Deploy
# 1. Provisions platform infrastructure (VPC, EKS, ECR) via Terraform
# 2. Provisions application infrastructure (RDS, DynamoDB, S3, etc.) via Terraform
# 3. Builds Docker images, pushes to ECR, and deploys via Helm
#
# Usage:
#   ./scripts/deploy-dev.sh                    # Full deploy (platform + app + helm)
#   ./scripts/deploy-dev.sh --skip-platform    # Skip platform provisioning
#   ./scripts/deploy-dev.sh --skip-terraform   # Skip all Terraform, just build+deploy
#   ./scripts/deploy-dev.sh --skip-build       # Reuse existing ECR images (set IMAGE_TAG)
# ------------------------------------------------------------------------------
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:?ERROR: AWS_ACCOUNT_ID must be set}"
EKS_CLUSTER="${EKS_CLUSTER:-otterworks-dev}"
NAMESPACE="${NAMESPACE:-otterworks}"
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
ECR_PREFIX="otterworks/"
IMAGE_TAG="${IMAGE_TAG:-$(git -C "$(dirname "$0")/.." rev-parse --short HEAD)-$(date +%s)}"
DB_PASSWORD="${DB_PASSWORD:-}"
# Host suffix for the golden app's ingress records. The frontends and the API
# are published through the SHARED ingress controller (one NLB for the whole
# cluster) rather than a LoadBalancer Service each -- see build_helm_args.
GOLDEN_HOST_SUFFIX="${GOLDEN_HOST_SUFFIX:-otterworks.app}"
# Shared JWT signing secret. MUST be identical across the gateway and every
# service that validates tokens. Generated once if not supplied; pass a stable
# value (JWT_SECRET=...) across redeploys so previously issued tokens stay valid.
JWT_SECRET="${JWT_SECRET:-$(openssl rand -hex 32)}"
# Rails (admin-service) session key. Stable value recommended across redeploys.
SECRET_KEY_BASE="${SECRET_KEY_BASE:-$(openssl rand -hex 64)}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SKIP_PLATFORM=false
SKIP_TERRAFORM=false
SKIP_BUILD=false
for arg in "$@"; do
  case "$arg" in
    --skip-platform)  SKIP_PLATFORM=true ;;
    --skip-terraform) SKIP_TERRAFORM=true ;;
    --skip-build)     SKIP_BUILD=true ;;
  esac
done

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[deploy]${NC} $*"; }
err()  { echo -e "${RED}[deploy]${NC} $*" >&2; }

# Service list
BACKEND_SERVICES=(
  api-gateway
  auth-service
  file-service
  document-service
  collab-service
  notification-service
  search-service
  analytics-service
  admin-service
  audit-service
  report-service
)

FRONTEND_SERVICES=(
  web-app
  admin-dashboard
)

ALL_SERVICES=("${BACKEND_SERVICES[@]}" "${FRONTEND_SERVICES[@]}")

# ---------- Pre-flight checks ----------

log "Checking prerequisites..."

command -v aws >/dev/null 2>&1       || { err "aws CLI not found"; exit 1; }
command -v docker >/dev/null 2>&1    || { err "docker not found"; exit 1; }
command -v helm >/dev/null 2>&1      || { err "helm not found"; exit 1; }
command -v kubectl >/dev/null 2>&1   || { err "kubectl not found"; exit 1; }
command -v terraform >/dev/null 2>&1 || { err "terraform not found"; exit 1; }
command -v jq >/dev/null 2>&1        || { err "jq not found (required to read IRSA role ARNs from Terraform outputs)"; exit 1; }

# ---------- Step 1: Provision Platform (VPC, EKS, ECR) ----------

if [ "${SKIP_TERRAFORM}" = false ] && [ "${SKIP_PLATFORM}" = false ]; then
  log "Provisioning platform infrastructure (VPC, EKS, ECR)..."
  cd "${REPO_ROOT}/platform/terraform"
  terraform init -input=false
  terraform apply -var-file=environments/dev.tfvars -auto-approve -input=false
  cd "${REPO_ROOT}"
  log "Platform infrastructure provisioned."
else
  log "Skipping platform provisioning."
fi

# ---------- Step 2: Provision Application Infrastructure ----------

if [ "${SKIP_TERRAFORM}" = false ]; then
  DB_PASSWORD="${DB_PASSWORD:?ERROR: DB_PASSWORD must be set when running Terraform}"
  log "Provisioning application infrastructure (RDS, DynamoDB, S3, SQS, etc.)..."
  cd "${REPO_ROOT}/infrastructure/terraform"
  terraform init -input=false
  # Pass the DB password via TF_VAR_ env, not -var on the command line, so it is
  # not visible in the process argument list (ps / /proc/*/cmdline). Consistent
  # with the Helm secret handling below.
  TF_VAR_db_password="${DB_PASSWORD}" terraform apply -auto-approve -input=false
  cd "${REPO_ROOT}"
  log "Application infrastructure provisioned."
else
  log "Skipping Terraform provisioning."
fi

# ---------- Step 3: Configure kubectl ----------

log "Configuring kubectl for EKS cluster ${EKS_CLUSTER}..."
aws eks update-kubeconfig --name "${EKS_CLUSTER}" --region "${AWS_REGION}" --alias "${EKS_CLUSTER}"

# ---------- Step 4: Create namespace ----------

log "Ensuring namespace ${NAMESPACE} exists..."
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

# ---------- Step 4b: Shared ingress controller ----------

# The frontends and the gateway are published as Ingresses (see build_helm_args),
# which do nothing at all without a controller to act on them. Installing it here
# rather than assuming it means a deploy onto a freshly built cluster is reachable
# on the URLs this script prints, instead of silently having no entry point.
# shellcheck source=lib/ingress-nginx.sh
source "${SCRIPT_DIR}/lib/ingress-nginx.sh"
ensure_ingress_nginx

# ---------- Step 5: ECR Login ----------

log "Logging into ECR..."
aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

# ---------- Step 6: Build and push Docker images ----------

build_and_push() {
  local service=$1
  local service_dir

  if [[ " ${FRONTEND_SERVICES[*]} " == *" ${service} "* ]]; then
    # The web-app service's source lives in frontend/client-app; other frontend
    # service names match their directory names.
    if [ "${service}" = "web-app" ]; then
      service_dir="${REPO_ROOT}/frontend/client-app"
    else
      service_dir="${REPO_ROOT}/frontend/${service}"
    fi
  else
    service_dir="${REPO_ROOT}/services/${service}"
  fi

  if [ ! -f "${service_dir}/Dockerfile" ]; then
    warn "No Dockerfile for ${service}, skipping..."
    return 0
  fi

  local image="${ECR_REGISTRY}/${ECR_PREFIX}${service}:${IMAGE_TAG}"
  log "Building ${service}..."
  docker build -t "${image}" "${service_dir}" --platform linux/amd64
  log "Pushing ${service}..."
  docker push "${image}"
}

if [ "${SKIP_BUILD}" = false ]; then
  log "Building and pushing Docker images..."
  for service in "${ALL_SERVICES[@]}"; do
    build_and_push "${service}" || warn "Failed to build ${service}, continuing..."
  done
else
  log "Skipping image build; reusing existing ECR images at tag ${IMAGE_TAG}."
fi

# ---------- Step 7: Deploy via Helm ----------

# The gateway proxies each route to http://<service>:<containerPort>, so every
# backend Service must be exposed on its container port (not the chart default 80).
declare -A CONTAINER_PORT=(
  [api-gateway]=8080 [auth-service]=8081 [file-service]=8082 [document-service]=8083
  [collab-service]=8084 [notification-service]=8086 [search-service]=8087
  [analytics-service]=8088 [admin-service]=8089 [audit-service]=8090 [report-service]=8091
)
# JVM services need more memory than the namespace default (256Mi) to start.
JVM_SERVICES=" auth-service report-service notification-service analytics-service "

# Populate the config/secret wiring from the application-infra Terraform outputs
# (RDS, Redis, S3, DynamoDB, SNS/SQS, IRSA roles). This closes the documented gap
# where the charts reference a ConfigMap/Secret that nothing ever creates.
load_infra_outputs() {
  local d="${REPO_ROOT}/infrastructure/terraform"
  terraform -chdir="$d" init -input=false >/dev/null 2>&1 || true
  local rds; rds="$(terraform -chdir="$d" output -raw rds_endpoint 2>/dev/null || echo "")"
  RDS_HOST="${rds%%:*}"; RDS_PORT="${rds##*:}"
  [ "$RDS_PORT" = "$rds" ] && RDS_PORT=5432 || true
  REDIS_HOST="$(terraform -chdir="$d" output -raw redis_endpoint 2>/dev/null || echo "")"
  S3_FILE_BUCKET="$(terraform -chdir="$d" output -raw s3_file_bucket 2>/dev/null || echo "")"
  S3_AUDIT_BUCKET="$(terraform -chdir="$d" output -raw s3_audit_archive_bucket 2>/dev/null || echo "")"
  DDB_FILE_META="$(terraform -chdir="$d" output -raw dynamodb_file_metadata_table 2>/dev/null || echo "")"
  DDB_AUDIT="$(terraform -chdir="$d" output -raw dynamodb_audit_events_table 2>/dev/null || echo "")"
  DDB_NOTIF="$(terraform -chdir="$d" output -raw dynamodb_notifications_table 2>/dev/null || echo "")"
  DDB_FOLDERS="$(terraform -chdir="$d" output -raw dynamodb_folders_table 2>/dev/null || echo "")"
  DDB_VERSIONS="$(terraform -chdir="$d" output -raw dynamodb_file_versions_table 2>/dev/null || echo "")"
  DDB_SHARES="$(terraform -chdir="$d" output -raw dynamodb_file_shares_table 2>/dev/null || echo "")"
  SNS_TOPIC="$(terraform -chdir="$d" output -raw sns_events_topic_arn 2>/dev/null || echo "")"
  SQS_NOTIF="$(terraform -chdir="$d" output -raw sqs_notification_queue_url 2>/dev/null || echo "")"
  IRSA_JSON="$(terraform -chdir="$d" output -json irsa_role_arns 2>/dev/null || echo "{}")"
  DB_NAME="${DB_NAME:-otterworks}"; DB_USER="${DB_USER:-otterworks_admin}"
  # MeiliSearch runs in-cluster (see deploy_meilisearch); search-service reaches it by Service DNS.
  MEILISEARCH_URL="${MEILISEARCH_URL:-http://meilisearch:7700}"
  if [ -z "${RDS_HOST}" ]; then
    warn "Terraform outputs unavailable; services will deploy without wired config."
  fi
  resolve_db_endpoint
}

# Where the services send their SQL, as opposed to RDS_HOST, which stays pointed
# at the instance for administrative work. The golden app shares the one RDS
# instance with every tenant, so it goes through the same pooler rather than
# holding raw connections against a ~112-connection ceiling. Falls back to the
# instance when the pooler is not installed; DB_VIA_PGBOUNCER=false forces it.
# Mirrors resolve_db_endpoint in scripts/lib/tenant-common.sh.
resolve_db_endpoint() {
  DB_ENDPOINT_HOST="${RDS_HOST}"
  DB_ENDPOINT_PORT="${RDS_PORT}"
  DB_SESSION_PORT="${RDS_PORT}"

  [ "${DB_VIA_PGBOUNCER:-true}" = "true" ] || return 0
  kubectl -n "${PGBOUNCER_NAMESPACE:-otterworks-platform}" get svc pgbouncer >/dev/null 2>&1 || {
    warn "pgbouncer not found; wiring services straight to RDS (install with demo-platform/scripts/install-pgbouncer.sh)"
    return 0
  }

  DB_ENDPOINT_HOST="pgbouncer.${PGBOUNCER_NAMESPACE:-otterworks-platform}.svc.cluster.local"
  DB_ENDPOINT_PORT=6432
  # Migrations hold session-level advisory locks and cannot go through a
  # transaction pooler; 6433 is the same pooler in session mode.
  DB_SESSION_PORT=6433
}

irsa_arn() { echo "${IRSA_JSON:-{}}" | jq -r --arg s "$1" '.[$s] // empty' 2>/dev/null; }

# Collect secret key/value pairs for a service. These are written to a temp
# values file and passed to helm via -f (see deploy_service) rather than
# --set-string, so secret values never appear in the process argument list
# (visible via ps / /proc/*/cmdline).
add_secret() { SECRET_KV+=("$1" "$2"); }

# URL-encode a string for safe use inside a URI (e.g. a DB password that may
# contain @ : / # % ? in a connection string). Uses jq's @uri filter.
urlencode() { jq -rn --arg s "$1" '$s|@uri'; }

# Public hostname for a golden-app service on the shared ingress.
golden_host() {
  case "$1" in
    web-app)         echo "app.${GOLDEN_HOST_SUFFIX}" ;;
    admin-dashboard) echo "admin.${GOLDEN_HOST_SUFFIX}" ;;
    api-gateway)     echo "api.${GOLDEN_HOST_SUFFIX}" ;;
    *)               echo "$1.${GOLDEN_HOST_SUFFIX}" ;;
  esac
}

# Fail the deploy if anything in this namespace owns a LoadBalancer Service.
# Those create ELBs outside Terraform's knowledge, which is how load balancers
# get stranded when a Service or cluster goes away. The single shared
# ingress-nginx controller (in its own namespace) is the only allowed exception.
assert_no_loadbalancer_services() {
  local found
  found="$(kubectl -n "${NAMESPACE}" get svc -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.name}{"\n"}{end}' 2>/dev/null)"
  if [ -n "${found}" ]; then
    err "LoadBalancer Services found in ${NAMESPACE}; these create untracked ELBs:"
    err "${found}"
    err "Route them through the shared ingress controller instead, then delete them:"
    err "  kubectl -n ${NAMESPACE} delete svc ${found//$'\n'/ }"
    return 1
  fi
}

# Build the per-service Helm --set flags into the global EXTRA_ARGS array, and
# secret values into the global SECRET_KV array.
build_helm_args() {
  local service=$1
  EXTRA_ARGS=()
  SECRET_KV=()
  local role; role="$(irsa_arn "$service")"
  if [ -n "$role" ]; then EXTRA_ARGS+=(--set "serviceAccount.roleArn=${role}"); fi
  if [[ " ${JVM_SERVICES} " == *" ${service} "* ]]; then
    EXTRA_ARGS+=(--set resources.requests.memory=512Mi --set resources.limits.memory=1024Mi --set resources.limits.cpu=1000m)
  fi

  # Frontends are reached through the shared ingress controller. They must never
  # be LoadBalancer Services: each one would mint a Classic ELB that Terraform
  # does not track and that outlives the cluster if the Service is removed while
  # the cloud-controller is down (this stranded 3 ELBs + 1 NLB in the past).
  case "$service" in
    web-app|admin-dashboard)
      EXTRA_ARGS+=(--set service.type=ClusterIP)
      EXTRA_ARGS+=(--set ingress.enabled=true --set ingress.className=nginx)
      EXTRA_ARGS+=(--set-string "ingress.hosts[0].host=$(golden_host "${service}")")
      EXTRA_ARGS+=(--set-string 'ingress.hosts[0].paths[0].path=/')
      EXTRA_ARGS+=(--set-string 'ingress.hosts[0].paths[0].pathType=Prefix')
      EXTRA_ARGS+=(--set 'ingress.tls=null')
      EXTRA_ARGS+=(--set-string config.API_GATEWAY_URL=http://api-gateway:8080)
      return 0 ;;
    api-gateway)
      EXTRA_ARGS+=(--set service.type=ClusterIP)
      EXTRA_ARGS+=(--set ingress.enabled=true --set ingress.className=nginx)
      EXTRA_ARGS+=(--set-string "ingress.hosts[0].host=$(golden_host "${service}")")
      EXTRA_ARGS+=(--set-string 'ingress.hosts[0].paths[0].path=/')
      EXTRA_ARGS+=(--set-string 'ingress.hosts[0].paths[0].pathType=Prefix')
      EXTRA_ARGS+=(--set 'ingress.tls=null') ;;
  esac

  local port="${CONTAINER_PORT[$service]:-}"
  if [ -n "$port" ]; then EXTRA_ARGS+=(--set "service.port=${port}" --set "service.targetPort=${port}"); fi
  # Backends are cluster-internal. api-gateway falls through the case above to
  # pick up its JWT secret but keeps the Ingress that block gave it; Helm honors
  # the last --set, so disabling here unconditionally would silently strip it.
  if [ "$service" != "api-gateway" ]; then
    EXTRA_ARGS+=(--set ingress.enabled=false)
  fi

  if [ -n "${JWT_SECRET}" ]; then
    case "$service" in
      api-gateway|auth-service|document-service|collab-service|admin-service)
        add_secret JWT_SECRET "${JWT_SECRET}" ;;
    esac
  fi

  case "$service" in
    api-gateway) : ;; # backend service URLs default to the correct in-cluster DNS
    auth-service)
      EXTRA_ARGS+=(--set-string "config.SPRING_PROFILES_ACTIVE=prod")
      EXTRA_ARGS+=(--set-string "config.SPRING_DATASOURCE_URL=jdbc:postgresql://${DB_ENDPOINT_HOST}:${DB_ENDPOINT_PORT}/${DB_NAME}")
      EXTRA_ARGS+=(--set-string "config.SPRING_DATASOURCE_USERNAME=${DB_USER}")
      # Flyway's migration lock is session-level, so it needs the session-mode
      # pooler port; the application's own queries stay on the transaction one.
      EXTRA_ARGS+=(--set-string "config.SPRING_FLYWAY_URL=jdbc:postgresql://${DB_ENDPOINT_HOST}:${DB_SESSION_PORT}/${DB_NAME}")
      EXTRA_ARGS+=(--set-string "config.SPRING_FLYWAY_USER=${DB_USER}")
      add_secret SPRING_FLYWAY_PASSWORD "${DB_PASSWORD}"
      add_secret SPRING_DATASOURCE_PASSWORD "${DB_PASSWORD}" ;;
    file-service)
      EXTRA_ARGS+=(--set-string "config.AWS_REGION=${AWS_REGION}")
      EXTRA_ARGS+=(--set-string "config.S3_BUCKET=${S3_FILE_BUCKET}")
      EXTRA_ARGS+=(--set-string "config.DYNAMODB_TABLE=${DDB_FILE_META}")
      EXTRA_ARGS+=(--set-string "config.DYNAMODB_FOLDERS_TABLE=${DDB_FOLDERS}")
      EXTRA_ARGS+=(--set-string "config.DYNAMODB_VERSIONS_TABLE=${DDB_VERSIONS}")
      EXTRA_ARGS+=(--set-string "config.DYNAMODB_SHARES_TABLE=${DDB_SHARES}")
      EXTRA_ARGS+=(--set-string "config.REDIS_HOST=${REDIS_HOST}" --set-string "config.REDIS_PORT=6379")
      EXTRA_ARGS+=(--set-string "config.SNS_TOPIC_ARN=${SNS_TOPIC}") ;;
    document-service)
      EXTRA_ARGS+=(--set-string "config.REDIS_HOST=${REDIS_HOST}" --set-string "config.REDIS_PORT=6379")
      EXTRA_ARGS+=(--set-string "config.DOC_SVC_AWS_REGION=${AWS_REGION}")
      EXTRA_ARGS+=(--set-string "config.DOC_SVC_SNS_TOPIC_ARN=${SNS_TOPIC}")
      add_secret DOC_SVC_DATABASE_URL "postgresql+asyncpg://$(urlencode "${DB_USER}"):$(urlencode "${DB_PASSWORD}")@${DB_ENDPOINT_HOST}:${DB_ENDPOINT_PORT}/${DB_NAME}" ;;
    collab-service)
      EXTRA_ARGS+=(--set-string "config.HTTP_PORT=8084" --set-string "config.NODE_ENV=production")
      EXTRA_ARGS+=(--set-string "config.REDIS_HOST=${REDIS_HOST}" --set-string "config.REDIS_PORT=6379") ;;
    notification-service)
      EXTRA_ARGS+=(--set-string "config.AWS_REGION=${AWS_REGION}")
      EXTRA_ARGS+=(--set-string "config.REDIS_HOST=${REDIS_HOST}" --set-string "config.REDIS_PORT=6379")
      EXTRA_ARGS+=(--set-string "config.DYNAMODB_TABLE_NOTIFICATIONS=${DDB_NOTIF}")
      EXTRA_ARGS+=(--set-string "config.SNS_TOPIC_ARN=${SNS_TOPIC}")
      EXTRA_ARGS+=(--set-string "config.SQS_QUEUE_URL=${SQS_NOTIF}") ;;
    search-service)
      EXTRA_ARGS+=(--set-string "config.AWS_REGION=${AWS_REGION}")
      EXTRA_ARGS+=(--set-string "config.REDIS_HOST=${REDIS_HOST}" --set-string "config.REDIS_PORT=6379")
      EXTRA_ARGS+=(--set-string "config.HOST=0.0.0.0" --set-string "config.PORT=8087")
      EXTRA_ARGS+=(--set-string "config.MEILISEARCH_URL=${MEILISEARCH_URL}")
      EXTRA_ARGS+=(--set-string "config.REQUIRE_AUTH=false" --set-string "config.SQS_ENABLED=false") ;;
    analytics-service)
      EXTRA_ARGS+=(--set-string "config.AWS_REGION=${AWS_REGION}")
      EXTRA_ARGS+=(--set-string "config.ANALYTICS_HOST=0.0.0.0" --set-string "config.PORT=8088")
      # Flyway again, from the same URL as the Slick pool (AnalyticsDb.migrate),
      # and a failed migration silently falls back to the in-memory store --
      # so the session port for the whole service, as with Rails.
      EXTRA_ARGS+=(--set-string "config.DATABASE_URL=jdbc:postgresql://${DB_ENDPOINT_HOST}:${DB_SESSION_PORT}/${DB_NAME}")
      EXTRA_ARGS+=(--set-string "config.DATABASE_USER=${DB_USER}")
      add_secret DATABASE_PASSWORD "${DB_PASSWORD}" ;;
    admin-service)
      # `rails db:migrate` runs from the image's CMD and takes a session-level
      # advisory lock, and Rails has one connection URL for both, so the whole
      # service uses the session-mode pooler port.
      EXTRA_ARGS+=(--set-string "config.DATABASE_HOST=${DB_ENDPOINT_HOST}" --set-string "config.DATABASE_PORT=${DB_SESSION_PORT}")
      EXTRA_ARGS+=(--set-string "config.DATABASE_USER=${DB_USER}")
      EXTRA_ARGS+=(--set-string "config.RAILS_ENV=production" --set-string "config.RAILS_LOG_TO_STDOUT=true")
      add_secret DATABASE_PASSWORD "${DB_PASSWORD}"
      add_secret SECRET_KEY_BASE "${SECRET_KEY_BASE}" ;;
    audit-service)
      EXTRA_ARGS+=(--set-string "config.Aws__Region=${AWS_REGION}")
      EXTRA_ARGS+=(--set-string "config.Aws__DynamoDbTable=${DDB_AUDIT}")
      EXTRA_ARGS+=(--set-string "config.Aws__S3ArchiveBucket=${S3_AUDIT_BUCKET}") ;;
    report-service)
      EXTRA_ARGS+=(--set-string "config.DB_HOST=${DB_ENDPOINT_HOST}" --set-string "config.DB_PORT=${DB_ENDPOINT_PORT}")
      EXTRA_ARGS+=(--set-string "config.DB_NAME=${DB_NAME}" --set-string "config.DB_USER=${DB_USER}")
      add_secret DB_PASSWORD "${DB_PASSWORD}" ;;
  esac
}

deploy_service() {
  local service=$1
  local chart_dir="${REPO_ROOT}/infrastructure/helm/${service}"

  if [ ! -d "${chart_dir}" ]; then
    warn "No Helm chart for ${service}, skipping..."
    return 0
  fi

  local image="${ECR_REGISTRY}/${ECR_PREFIX}${service}"
  build_helm_args "${service}"

  # Write secrets to a locked-down temp values file passed via -f, so the values
  # are never exposed in the process argument list (ps / /proc/*/cmdline).
  local secret_file="" secret_args=()
  if [ "${#SECRET_KV[@]}" -gt 0 ]; then
    secret_file="$(mktemp)"
    chmod 600 "${secret_file}"
    jq -n --args '{secrets: (reduce range(0; ($ARGS.positional | length); 2) as $i
      ({}; . + {($ARGS.positional[$i]): $ARGS.positional[$i + 1]}))}' \
      "${SECRET_KV[@]}" > "${secret_file}"
    secret_args=(-f "${secret_file}")
  fi

  log "Deploying ${service} via Helm..."
  helm upgrade --install "${service}" "${chart_dir}" \
    --namespace "${NAMESPACE}" \
    --set image.repository="${image}" \
    --set image.tag="${IMAGE_TAG}" \
    "${EXTRA_ARGS[@]}" \
    "${secret_args[@]}" \
    --wait \
    --timeout 3m \
    && local rc=0 || local rc=1
  [ -n "${secret_file}" ] && rm -f "${secret_file}"
  if [ "${rc}" -ne 0 ]; then warn "Helm deploy failed for ${service}"; return 1; fi
}

# MeiliSearch is the search-service backend. The IaC provisions it on ECS, but the
# golden app runs everything in-cluster, so deploy a lightweight single-replica
# MeiliSearch Deployment + Service that search-service reaches at http://meilisearch:7700.
# Dev mode (MEILI_ENV=development) disables the master-key requirement.
deploy_meilisearch() {
  log "Deploying in-cluster MeiliSearch (search-service backend)..."
  kubectl apply -n "${NAMESPACE}" -f - <<'YAML'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: meilisearch
  labels: { app: meilisearch }
spec:
  replicas: 1
  selector:
    matchLabels: { app: meilisearch }
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
          ports:
            - containerPort: 7700
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
  ports:
    - port: 7700
      targetPort: 7700
YAML
  kubectl rollout status -n "${NAMESPACE}" deployment/meilisearch --timeout=180s || \
    warn "MeiliSearch did not become ready in time; search-service may report meilisearch_unavailable."
}

log "Loading application-infra Terraform outputs for config wiring..."
load_infra_outputs

# Every DB-backed service (auth/document/analytics/admin/report) gets DB_PASSWORD
# injected below, so require it here too — not only inside the Terraform block,
# which is skipped on the --skip-terraform redeploy path.
DB_PASSWORD="${DB_PASSWORD:?ERROR: DB_PASSWORD must be set (exported or via Terraform run) before deploying services}"

deploy_meilisearch

log "Deploying services to EKS..."
FAILED=()
for service in "${ALL_SERVICES[@]}"; do
  deploy_service "${service}" || FAILED+=("${service}")
done

# ---------- Step 8: Verify ----------

log "Checking pod status in namespace ${NAMESPACE}..."
kubectl get pods -n "${NAMESPACE}" -o wide

if [ ${#FAILED[@]} -gt 0 ]; then
  warn "The following services failed to deploy: ${FAILED[*]}"
  exit 1
fi

assert_no_loadbalancer_services || exit 1

log "Deployment complete! All services deployed to ${EKS_CLUSTER}/${NAMESPACE}"
echo ""
log "Golden app URLs (shared ingress):"
echo "  web-app:         https://$(golden_host web-app)"
echo "  admin-dashboard: https://$(golden_host admin-dashboard)"
echo "  api-gateway:     https://$(golden_host api-gateway)"
echo ""
log "Useful commands:"
echo "  kubectl get pods -n ${NAMESPACE}"
echo "  kubectl get svc -n ${NAMESPACE}"
echo "  kubectl logs -n ${NAMESPACE} -l app=api-gateway"
