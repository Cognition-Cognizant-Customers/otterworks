#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# Safety tests for the infrastructure orphan sweep.
#
# Every deletion this sweep makes is justified by "the owning cluster no longer
# exists", so the live-cluster lookup is load-bearing: if a failed lookup were
# read as "no clusters exist", the whole live estate would look orphaned and an
# armed run would delete the shared ingress. These tests pin that invariant.
#
# The aws CLI is stubbed; nothing here touches a real account.
# ------------------------------------------------------------------------------
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  ok   - $1"; }
nope() { FAIL=$((FAIL+1)); echo "  FAIL - $1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else nope "$1 (expected '$3', got '$2')"; fi; }

AWS_LIST_CLUSTERS_RC=0
AWS_LIST_CLUSTERS_OUT="otterworks-dev"
ELB_TAG_CLUSTER="otterworks-dev"
INSTANCE_TAG_CLUSTER="otterworks-dev"
DELETED=""

# Stub the CLI. The account is modelled as holding exactly one Classic ELB: the
# shared ingress, tagged to the live cluster. It must survive every scenario
# below -- if a lookup failure were read as "no clusters", this is the resource
# that would be deleted out from under every tenant.
aws() {
  local args="$*"
  case "${args}" in
    *"eks list-clusters"*)
      printf '%s' "${AWS_LIST_CLUSTERS_OUT}"; return "${AWS_LIST_CLUSTERS_RC}" ;;
    *"elb describe-load-balancers"*"--load-balancer-names"*)
      printf '3'; return 0 ;;                     # backend count
    *"elb describe-load-balancers"*)
      printf 'shared-ingress'; return 0 ;;
    *"elb describe-tags"*)
      printf '[{"Key":"kubernetes.io/cluster/%s","Value":"owned"},' "${ELB_TAG_CLUSTER}"
      printf '{"Key":"kubernetes.io/service-name","Value":"ingress-nginx/ingress-nginx-controller"}]'
      return 0 ;;
    *"ec2 describe-instances"*"--instance-ids"*)
      printf '[{"Key":"karpenter.sh/nodepool","Value":"tenants"},'
      printf '{"Key":"kubernetes.io/cluster/%s","Value":"owned"}]' "${INSTANCE_TAG_CLUSTER}"
      return 0 ;;
    *"ec2 describe-instances"*)
      printf 'i-000karpenter'; return 0 ;;
    *"route53 list-resource-record-sets"*"?Name=="*)
      printf '[{"Name":"%s.","Type":"A","TTL":300,"ResourceRecords":[{"Value":"10.0.0.1"}]}]' "${R53_RECORDS}"
      return 0 ;;
    *"route53 list-resource-record-sets"*)
      printf '%s' "${R53_RECORDS}"; return 0 ;;
    *"change-resource-record-sets"*)
      DELETED="${DELETED} route53:${R53_RECORDS}"; return 0 ;;
    *delete*|*release-address*|*terminate-instances*)
      DELETED="${DELETED} ${args}"; return 0 ;;
    *) return 0 ;;
  esac
}

# `kubectl get svc` answering "gone" is the interesting case: it is what the
# service-name branch acts on, and a Service in someone else's cluster is always
# absent from ours. A stub that always succeeds hides that branch entirely.
KUBECTL_RC=0
kubectl() {
  case "$*" in
    version) return 0 ;;
    *) return "${KUBECTL_RC}" ;;
  esac
}
R53_RECORDS=""

# Armed, so that any deletion the sweep decides on is actually recorded by the
# stub. A prefix assignment would be reverted once `source` returns.
export DRY_RUN=false
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/infra-sweep.sh"

echo "infra-sweep safety"

# The dangerous case: a throttled or credential-less lookup must not read as
# "nothing is live".
AWS_LIST_CLUSTERS_RC=255
AWS_LIST_CLUSTERS_OUT="An error occurred (ThrottlingException): Rate exceeded"
DELETED=""
infra_sweep >/dev/null 2>&1
check "deletes nothing when the cluster lookup fails" "${DELETED# }" ""

AWS_LIST_CLUSTERS_RC=255
infra_sweep >/dev/null 2>&1
check "reports failure to its caller" "$?" "1"

# A genuinely empty account is a real answer and must still be swept.
AWS_LIST_CLUSTERS_RC=0
AWS_LIST_CLUSTERS_OUT=""
infra_sweep >/dev/null 2>&1
check "still runs when the account genuinely has no clusters" "$?" "0"

AWS_LIST_CLUSTERS_OUT="otterworks-dev"
DELETED=""
infra_sweep >/dev/null 2>&1
check "runs normally when the lookup succeeds" "$?" "0"
check "  and spares the live cluster's shared ingress" "${DELETED# }" ""

# `aws --output text` emits a flat list TAB-separated, and a single-cluster
# account is the one case where that is indistinguishable from space-separated.
# With a second cluster present, a word-boundary match on the raw string fails
# for *every* name, so the live shared ingress reads as an orphan.
AWS_LIST_CLUSTERS_OUT="$(printf 'otterworks-dev\totterworks-blue')"
DELETED=""
infra_sweep >/dev/null 2>&1
check "spares the shared ingress when the account holds several clusters" "${DELETED# }" ""

# A live cluster must never be treated as an orphan.
# shellcheck disable=SC2034  # read by cluster_is_live from the sourced sweep
LIVE_CLUSTERS="otterworks-dev"
cluster_is_live "otterworks-dev" && r=live || r=dead
check "recognises a live cluster" "${r}" "live"
cluster_is_live "otterworks-deleted" && r=live || r=dead
check "recognises a cluster that is gone" "${r}" "dead"

# ELBv2 ARNs end in .../loadbalancer/<type>/<name>/<id>. Real ARN of the shared
# ingress NLB; the name is what `describe-load-balancers` reports and what an
# operator can actually look up, the trailing segment is an opaque id.
arn="arn:aws:elasticloadbalancing:us-east-1:000000000000:loadbalancer/net/otterworks-shared-ingress/a589c9578aa918fa"
name="${arn##*loadbalancer/}"; name="${name#*/}"; name="${name%%/*}"
check "parses the load balancer name out of an ELBv2 ARN" "${name}" "otterworks-shared-ingress"

# The scheduled path is the one that matters: the CronJob sets no DRY_RUN, so if
# the reaper does not set it from the control table the sweep reports forever
# while the dashboard shows it as on. Assert both states reach act().
DRY_RUN=true
DELETED=""
act aws elb delete-load-balancer --load-balancer-name stale-elb >/dev/null 2>&1
check "report-only mode deletes nothing" "${DELETED# }" ""

DRY_RUN=false
DELETED=""
act aws elb delete-load-balancer --load-balancer-name stale-elb >/dev/null 2>&1
check "armed mode performs the deletion" "${DELETED# }" "elb delete-load-balancer --load-balancer-name stale-elb"

# "Tagged for a cluster that no longer exists" is not ownership on its own. This
# account holds unrelated workloads, and another team's dead cluster is theirs to
# clean up -- the IAM conditions on the reaper's role refuse these deletes too,
# so a sweep that tried would only produce AccessDenied noise.
DRY_RUN=false
AWS_LIST_CLUSTERS_RC=0
AWS_LIST_CLUSTERS_OUT="otterworks-dev"
# shellcheck disable=SC2034  # read by cluster_is_ours from the sourced sweep
SWEEPABLE_CLUSTERS="otterworks-dev"

ELB_TAG_CLUSTER="someone-elses-cluster"
DELETED=""
infra_sweep >/dev/null 2>&1
check "spares a dead cluster this platform does not own" "${DELETED# }" ""

ELB_TAG_CLUSTER="otterworks-old"
DELETED=""
infra_sweep >/dev/null 2>&1
check "spares a dead cluster missing from the sweepable list" "${DELETED# }" ""

# ...and once that name is declared sweepable, the same orphan is reclaimed.
# shellcheck disable=SC2034  # read by cluster_is_ours from the sourced sweep
SWEEPABLE_CLUSTERS="otterworks-dev otterworks-old"
DELETED=""
infra_sweep >/dev/null 2>&1
check "reclaims a dead cluster the platform used to run under" \
  "${DELETED##* }" "shared-ingress"

# The live cluster's own ingress stays untouched even though it is sweepable.
ELB_TAG_CLUSTER="otterworks-dev"
DELETED=""
infra_sweep >/dev/null 2>&1
check "spares the live cluster's ingress when that cluster is sweepable" "${DELETED# }" ""

# The service-name branch is the other way a load balancer gets deleted, and it
# asks *our* cluster whether the Service exists. Another cluster's Service is
# necessarily absent from ours, so without an ownership check that branch reads
# every foreign load balancer as an orphan -- and Classic ELB has no IAM
# condition to fall back on.
KUBECTL_RC=1
# shellcheck disable=SC2034  # read by cluster_is_ours from the sourced sweep
SWEEPABLE_CLUSTERS="otterworks-dev"
AWS_LIST_CLUSTERS_OUT="otterworks-dev someone-elses-cluster"
ELB_TAG_CLUSTER="someone-elses-cluster"
DELETED=""
infra_sweep >/dev/null 2>&1
check "spares a live foreign load balancer whose Service is not in our cluster" "${DELETED# }" ""

# ...while our own cluster's abandoned Service is still reclaimed.
ELB_TAG_CLUSTER="otterworks-dev"
DELETED=""
infra_sweep >/dev/null 2>&1
check "reclaims our own load balancer once its Service is gone" "${DELETED##* }" "shared-ingress"
KUBECTL_RC=0

# Route53: the zone holds platform records as well as tenant ones. cert-manager
# writes _acme-challenge here during a wildcard renewal, and it matches no
# tenant -- deleting it mid-challenge costs every tenant its TLS.
ctl_tenant_exists() { return 1; }   # no tenant exists, the worst case
# shellcheck disable=SC2034  # read by sweep_route53 from the sourced sweep
DNS_ZONE_ID="Z0TEST"
AWS_LIST_CLUSTERS_OUT="otterworks-dev"

for record in _acme-challenge.demo.otterworks.app ops.demo.otterworks.app; do
  R53_RECORDS="${record}"
  DELETED=""
  sweep_route53 >/dev/null 2>&1
  check "spares the non-tenant record ${record}" "${DELETED# }" ""
done

for record in t-gone.demo.otterworks.app api-t-gone.demo.otterworks.app cname-t-gone.demo.otterworks.app; do
  R53_RECORDS="${record}"
  DELETED=""
  sweep_route53 >/dev/null 2>&1
  check "reclaims the record ${record} of a tenant that no longer exists" \
    "${DELETED# }" "route53:${record}"
done

# ---- Karpenter nodes ---------------------------------------------------------
#
# These are the most expensive orphan class the sweep handles -- an instance,
# not an idle load balancer -- and the only thing that would otherwise terminate
# one is a controller inside a cluster that no longer exists. The live cluster's
# own nodes must survive regardless: they are running tenants.
AWS_LIST_CLUSTERS_RC=0
AWS_LIST_CLUSTERS_OUT="otterworks-dev"
# shellcheck disable=SC2034  # read by cluster_is_ours from the sourced sweep
SWEEPABLE_CLUSTERS="otterworks-dev otterworks-old"

INSTANCE_TAG_CLUSTER="otterworks-dev"
DELETED=""
sweep_karpenter_instances >/dev/null 2>&1
check "spares Karpenter nodes of the live cluster" "${DELETED# }" ""

INSTANCE_TAG_CLUSTER="someone-elses-cluster"
DELETED=""
sweep_karpenter_instances >/dev/null 2>&1
check "spares Karpenter nodes of a dead cluster we do not own" "${DELETED# }" ""

INSTANCE_TAG_CLUSTER="otterworks-old"
DELETED=""
sweep_karpenter_instances >/dev/null 2>&1
check "terminates Karpenter nodes left by a dead cluster of ours" \
  "${DELETED##* }" "i-000karpenter"
INSTANCE_TAG_CLUSTER="otterworks-dev"

# ---- ownership set is built from the environment, not replaced by it --------
#
# The Terraform variable and the Helm value both hold the *extra*, previously-run
# cluster names, so SWEEPABLE_CLUSTERS can legitimately arrive holding only
# those. If it replaced the default instead of adding to it, the live platform's
# own cluster would stop being ours and the sweep would go quiet for exactly the
# orphans it exists to catch -- while still looking like it ran.
ownership() {
  ( export EKS_CLUSTER="otterworks-dev" SWEEPABLE_CLUSTERS="$1"
    # shellcheck source=/dev/null
    . "${SCRIPT_DIR}/infra-sweep.sh"
    cluster_is_ours "$2" && echo ours || echo "not ours" )
}
check "owns the live cluster when only extra names are configured" \
  "$(ownership "otterworks-old" otterworks-dev)" "ours"
check "  and still owns the extra name" \
  "$(ownership "otterworks-old" otterworks-old)" "ours"
check "owns the live cluster when nothing is configured" \
  "$(ownership "" otterworks-dev)" "ours"
check "never owns a cluster nobody declared" \
  "$(ownership "otterworks-old" someone-elses-cluster)" "not ours"

echo "${PASS} passed, ${FAIL} failed"
[ "${FAIL}" -eq 0 ]
