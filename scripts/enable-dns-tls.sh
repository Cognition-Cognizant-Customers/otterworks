#!/usr/bin/env bash
# enable-dns-tls.sh — turn on the AWS-native DNS + wildcard-TLS stack for the
# demo platform. Run ONCE, after otterworks.app is registered in Route53 and
# `terraform apply` has created the DNS automation IRSA role (enable_dns, on by default).
#
# It replaces the temporary nip.io IP-based hostnames with real, self-contained
# AWS records:
#   - external-dns  -> auto-manages t-<id>.demo.<root> / api-t-<id>.demo.<root>
#   - cert-manager  -> issues ONE wildcard cert via ACME DNS-01 over Route53
#   - ingress-nginx -> serves that wildcard as its default TLS certificate
#
# Idempotent: safe to re-run. No secrets are passed on argv.
#
# Usage:
#   DOMAIN_ROOT=otterworks.app ACME_EMAIL=platform@example.com \
#     [ISSUER=letsencrypt-staging] scripts/enable-dns-tls.sh
set -euo pipefail

DOMAIN_ROOT="${DOMAIN_ROOT:-otterworks.app}"
DEMO_SUFFIX="${DEMO_SUFFIX:-demo.${DOMAIN_ROOT}}"
OPS_HOST="${OPS_HOST:-ops.${DOMAIN_ROOT}}"
# The perpetual tenant answers at the apex (t-main.<root>), not under the
# per-attendee subdomain, so external-dns needs it named explicitly.
PERPETUAL_ID="${PERPETUAL_ID:-main}"
PERPETUAL_WEB_HOST="t-${PERPETUAL_ID}.${DOMAIN_ROOT}"
PERPETUAL_API_HOST="api-t-${PERPETUAL_ID}.${DOMAIN_ROOT}"
ISSUER="${ISSUER:-letsencrypt-staging}"   # start on staging, then re-run ISSUER=letsencrypt-prod
TXT_OWNER="${TXT_OWNER:-otterworks-demo}"
TF_DIR="${TF_DIR:-$(cd "$(dirname "$0")/.." && pwd)/demo-platform/infra/terraform}"
MANIFEST_DIR="$(cd "$(dirname "$0")/.." && pwd)/demo-platform/k8s/dns-tls"
INGRESS_NS="${INGRESS_NS:-ingress-nginx}"
INGRESS_RELEASE="${INGRESS_RELEASE:-ingress-nginx}"
WILDCARD_SECRET="ingress-nginx/otterworks-wildcard-tls"

if [ -z "${ACME_EMAIL:-}" ]; then
  echo "ACME_EMAIL is required (contact email for the Let's Encrypt account)" >&2
  exit 1
fi

echo "==> Reading Terraform outputs from ${TF_DIR}"
DNS_ROLE_ARN="$(terraform -chdir="${TF_DIR}" output -raw dns_role_arn)"
ZONE_ID="$(terraform -chdir="${TF_DIR}" output -raw dns_zone_id)"
if [ -z "${DNS_ROLE_ARN}" ] || [ "${DNS_ROLE_ARN}" = "null" ] || [ -z "${ZONE_ID}" ] || [ "${ZONE_ID}" = "null" ]; then
  echo "dns_role_arn / dns_zone_id are empty — run 'terraform apply' in demo-platform/infra/terraform first." >&2
  exit 1
fi
echo "    dns_role_arn=${DNS_ROLE_ARN}"
echo "    dns_zone_id=${ZONE_ID}"

render() {  # substitute the __PLACEHOLDER__ tokens in <file> with the resolved values
  sed -e "s#__DNS_ROLE_ARN__#${DNS_ROLE_ARN}#g" \
      -e "s#__DOMAIN_FILTER__#${DEMO_SUFFIX}#g" \
      -e "s#__OPS_HOST__#${OPS_HOST}#g" \
      -e "s#__PERPETUAL_WEB_HOST__#${PERPETUAL_WEB_HOST}#g" \
      -e "s#__PERPETUAL_API_HOST__#${PERPETUAL_API_HOST}#g" \
      -e "s#__TXT_OWNER__#${TXT_OWNER}#g" \
      -e "s#__ACME_EMAIL__#${ACME_EMAIL}#g" \
      -e "s#__ZONE_ID__#${ZONE_ID}#g" \
      -e "s#__DOMAIN_ROOT__#${DOMAIN_ROOT}#g" \
      -e "s#__ISSUER__#${ISSUER}#g" \
      "$1"
}

echo "==> Installing external-dns"
render "${MANIFEST_DIR}/external-dns.yaml" | kubectl apply -f -

# cert-manager is a platform prerequisite rather than something this script owns,
# but assuming it is present makes a blank-slate cluster fail here, halfway
# through, with external-dns already applied. Install it when missing so the
# rebuild is one command; an existing release is left at its current version.
if ! kubectl get namespace cert-manager >/dev/null 2>&1; then
  echo "==> Installing cert-manager (not present)"
  helm repo add jetstack https://charts.jetstack.io >/dev/null 2>&1 || true
  helm repo update jetstack >/dev/null 2>&1 || true
  helm upgrade --install cert-manager jetstack/cert-manager \
    --namespace cert-manager --create-namespace \
    --set crds.enabled=true --wait --timeout 5m
fi

echo "==> Annotating cert-manager ServiceAccount for Route53 IRSA + restarting"
kubectl -n cert-manager annotate serviceaccount cert-manager \
  "eks.amazonaws.com/role-arn=${DNS_ROLE_ARN}" --overwrite
kubectl -n cert-manager rollout restart deploy/cert-manager
kubectl -n cert-manager rollout status deploy/cert-manager --timeout=120s

echo "==> Creating ClusterIssuers"
render "${MANIFEST_DIR}/cluster-issuer.yaml" | kubectl apply -f -

echo "==> Requesting wildcard certificate (issuer=${ISSUER})"
render "${MANIFEST_DIR}/wildcard-certificate.yaml" | kubectl apply -f -

echo "==> Wiring the wildcard cert as ingress-nginx default TLS certificate"
if helm status "${INGRESS_RELEASE}" -n "${INGRESS_NS}" >/dev/null 2>&1; then
  # Pin to the version already deployed so a routine "turn on TLS" run only adds
  # the default-ssl-certificate flag and never silently upgrades the shared
  # controller that fronts every tenant + the dashboard.
  cur_ver="$(helm list -n "${INGRESS_NS}" -o json \
    | jq -r --arg n "${INGRESS_RELEASE}" '.[] | select(.name==$n) | .chart' \
    | sed 's/.*-//')"
  helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx >/dev/null 2>&1 || true
  helm repo update >/dev/null 2>&1 || true
  up_args=(-n "${INGRESS_NS}" --reuse-values
           --set-string "controller.extraArgs.default-ssl-certificate=${WILDCARD_SECRET}")
  if [ -n "${cur_ver}" ]; then
    up_args+=(--version "${cur_ver}")
  else
    echo "    WARNING: could not detect the installed ingress-nginx chart version;" >&2
    echo "    refusing to helm-upgrade an unpinned shared controller. Set it via" >&2
    echo "    controller.extraArgs.default-ssl-certificate=${WILDCARD_SECRET} manually." >&2
    exit 1
  fi
  helm upgrade "${INGRESS_RELEASE}" ingress-nginx/ingress-nginx "${up_args[@]}"
else
  echo "    ingress-nginx helm release not found; patching the controller Deployment directly."
  # Idempotent: only append the flag if it isn't already present, so re-runs
  # don't stack duplicate --default-ssl-certificate args on the controller.
  if kubectl -n "${INGRESS_NS}" get deploy ingress-nginx-controller \
      -o jsonpath='{.spec.template.spec.containers[0].args}' 2>/dev/null \
      | grep -q -- '--default-ssl-certificate='; then
    echo "    --default-ssl-certificate already set on the controller; nothing to do."
  else
    kubectl -n "${INGRESS_NS}" patch deploy ingress-nginx-controller --type=json -p "$(cat <<JSON
[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--default-ssl-certificate=${WILDCARD_SECRET}"}]
JSON
)"
  fi
fi

cat <<DONE

==> Done. Verify with:
    kubectl -n external-dns logs deploy/external-dns --tail=20
    kubectl -n ingress-nginx get certificate otterworks-wildcard
    kubectl -n ingress-nginx describe certificate otterworks-wildcard | tail -20

Once the cert is Ready on staging, re-run with ISSUER=letsencrypt-prod for a
browser-trusted certificate, then redeploy tenants (they already default to
--host-suffix ${DEMO_SUFFIX}) and browse https://ops.${DOMAIN_ROOT}.
DONE
