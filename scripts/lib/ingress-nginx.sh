#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# The shared ingress controller: one nginx controller, one NLB, for the whole
# cluster.
#
# Nothing else in this repo may be a LoadBalancer Service. Per-service load
# balancers are what stranded three Classic ELBs and an NLB when a cluster was
# replaced, so the golden app and every tenant reach the outside world through
# this one controller instead.
#
# That makes the controller a prerequisite of any deploy that creates an
# Ingress, not an optional extra: an Ingress with no controller is inert, and
# the deploy would report success while nothing was reachable. Both the golden
# deploy (deploy-dev.sh) and the tenant baseline (tenant-platform-baseline.sh)
# install it from here so the two cannot drift -- in particular over
# controller.metrics, which is off by default in the upstream chart and which
# the reaper's idle scan depends on to see per-namespace traffic.
#
# Idempotent; safe to call on every deploy.
# ------------------------------------------------------------------------------

INGRESS_NAMESPACE="${INGRESS_NAMESPACE:-ingress-nginx}"

# Reuse the caller's logging when it has some, so output stays consistent with
# the script that sourced this one.
ing_log()  { if declare -F log  >/dev/null; then log  "$@"; else echo "[ingress] $*"; fi; }
ing_warn() { if declare -F warn >/dev/null; then warn "$@"; else echo "[ingress] WARN: $*" >&2; fi; }

ensure_ingress_nginx() {
  # --reuse-values on an existing release, because settings are applied to this
  # controller out of band: enable-dns-tls.sh points it at the wildcard
  # certificate with controller.extraArgs.default-ssl-certificate. A plain
  # upgrade resets everything not named below, which drops that argument and
  # serves the built-in self-signed certificate for every tenant host -- the
  # install returns success and TLS is broken cluster-wide.
  local reuse=()
  if helm status ingress-nginx -n "${INGRESS_NAMESPACE}" >/dev/null 2>&1; then
    ing_log "Shared ingress-nginx already installed in ${INGRESS_NAMESPACE}."
    reuse=(--reuse-values)
  else
    ing_log "Installing shared ingress-nginx (one controller, one NLB)..."
  fi

  helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx >/dev/null 2>&1 || true
  helm repo update ingress-nginx >/dev/null 2>&1 || true

  # controller.metrics.enabled is off in the chart by default, but the reaper
  # decides which tenants are idle from this controller's per-namespace request
  # counter -- with no metrics endpoint the idle scan fails closed and nothing
  # is ever scaled to zero. It costs one port and one ClusterIP Service.
  helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
    --namespace "${INGRESS_NAMESPACE}" --create-namespace \
    "${reuse[@]}" \
    --set controller.service.type=LoadBalancer \
    --set controller.service.annotations."service\.beta\.kubernetes\.io/aws-load-balancer-type"=nlb \
    --set controller.replicaCount=1 \
    --set controller.resources.requests.cpu=100m \
    --set controller.resources.requests.memory=128Mi \
    --set controller.metrics.enabled=true \
    --wait --timeout 5m || ing_warn "ingress-nginx install reported an issue; continuing."

  kubectl label namespace "${INGRESS_NAMESPACE}" \
    kubernetes.io/metadata.name="${INGRESS_NAMESPACE}" --overwrite >/dev/null 2>&1 || true

  ing_log "Shared ingress address: $(kubectl get svc -n "${INGRESS_NAMESPACE}" ingress-nginx-controller \
    -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo 'pending')"
}
