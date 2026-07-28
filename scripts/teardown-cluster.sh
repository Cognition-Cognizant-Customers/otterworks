#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# Tear down the shared EKS cluster WITHOUT stranding AWS resources.
#
# Deleting an EKS cluster does not delete the load balancers its Services
# created: those are made by the in-cluster AWS cloud-controller, are unknown to
# Terraform, and once the cluster is gone nothing is left to reclaim them. Four
# load balancers were stranded this way in June 2026 and billed for a month with
# zero backends.
#
# So the order here is mandatory, not stylistic:
#   1. delete every LoadBalancer Service and WAIT for AWS to actually release
#      the load balancers (the controller is still alive to do it)
#   2. delete Ingresses so external-dns removes their Route53 records
#   3. delete Karpenter's NodeClaims, which have the same problem: the only
#      thing that terminates those instances is the controller in this cluster
#   4. only then destroy the cluster with Terraform
#   5. verify nothing tagged for this cluster survives
#
# Usage: scripts/teardown-cluster.sh [--yes] [--skip-terraform]
# ------------------------------------------------------------------------------
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
EKS_CLUSTER="${EKS_CLUSTER:-otterworks-dev}"
DRAIN_TIMEOUT="${DRAIN_TIMEOUT:-300}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[teardown]${NC} $*"; }
warn() { echo -e "${YELLOW}[teardown]${NC} $*"; }
err()  { echo -e "${RED}[teardown]${NC} $*" >&2; }

ASSUME_YES=false
SKIP_TERRAFORM=false
for arg in "$@"; do
  case "$arg" in
    --yes|-y)         ASSUME_YES=true ;;
    --skip-terraform) SKIP_TERRAFORM=true ;;
    *) err "unknown argument: $arg"; exit 1 ;;
  esac
done

command -v aws >/dev/null 2>&1     || { err "aws CLI not found"; exit 1; }
command -v kubectl >/dev/null 2>&1 || { err "kubectl not found"; exit 1; }

# Count AWS load balancers still tagged as owned by this cluster.
#
# This number is the sole evidence that destroying the cluster is safe, so it
# must never be produced by guessing. A throttled or unauthorised describe
# returns nothing, and "nothing" is indistinguishable from "none left" -- which
# would clear the drain wait instantly and destroy the cluster while its load
# balancers were still live, causing exactly the orphans this script exists to
# prevent. Failures therefore return non-zero and the callers stop.
cluster_lb_count() {
  local n=0 lb arn out

  out="$(aws elb describe-load-balancers --region "${AWS_REGION}" \
           --query 'LoadBalancerDescriptions[].LoadBalancerName' --output text 2>&1)" || {
    err "could not list Classic ELBs: ${out}"
    return 1
  }
  for lb in ${out}; do
    aws elb describe-tags --region "${AWS_REGION}" --load-balancer-names "${lb}" \
      --query "TagDescriptions[0].Tags[?Key=='kubernetes.io/cluster/${EKS_CLUSTER}']" \
      --output text 2>/dev/null | grep -q . && n=$((n + 1))
  done

  out="$(aws elbv2 describe-load-balancers --region "${AWS_REGION}" \
           --query 'LoadBalancers[].LoadBalancerArn' --output text 2>&1)" || {
    err "could not list ALB/NLBs: ${out}"
    return 1
  }
  for arn in ${out}; do
    aws elbv2 describe-tags --region "${AWS_REGION}" --resource-arns "${arn}" \
      --query "TagDescriptions[0].Tags[?Key=='kubernetes.io/cluster/${EKS_CLUSTER}']" \
      --output text 2>/dev/null | grep -q . && n=$((n + 1))
  done

  echo "${n}"
}

if [ "${ASSUME_YES}" != true ]; then
  warn "This deletes the EKS cluster ${EKS_CLUSTER} and every tenant on it."
  read -r -p "Type the cluster name to confirm: " confirm
  [ "${confirm}" = "${EKS_CLUSTER}" ] || { err "confirmation did not match; aborting."; exit 1; }
fi

# ---------- Step 1: drain LoadBalancer Services while the controller lives ----

log "Configuring kubectl for ${EKS_CLUSTER}..."
if ! aws eks update-kubeconfig --name "${EKS_CLUSTER}" --region "${AWS_REGION}" >/dev/null 2>&1; then
  warn "cluster ${EKS_CLUSTER} is not reachable; skipping drain."
  warn "Any load balancers it owned are already orphaned -- run:"
  warn "  DRY_RUN=false demo-platform/reaper/infra-sweep.sh"
else
  before="$(cluster_lb_count)" || {
    err "cannot inventory this cluster's load balancers, so cannot tell whether"
    err "the teardown would strand them. Fix the AWS access above and re-run."
    exit 1
  }
  log "AWS load balancers tagged for ${EKS_CLUSTER}: ${before}"

  log "Deleting Ingresses so external-dns removes their DNS records..."
  kubectl delete ingress --all-namespaces --all --wait=false >/dev/null 2>&1 || true

  log "Deleting LoadBalancer Services (this is what releases the ELBs)..."
  kubectl get svc --all-namespaces \
    -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.namespace}{" "}{.metadata.name}{"\n"}{end}' \
    2>/dev/null | while read -r ns name; do
      [ -n "${ns}" ] || continue
      log "  deleting svc ${ns}/${name}"
      kubectl -n "${ns}" delete svc "${name}" --wait=false >/dev/null 2>&1 || true
    done

  # The controller deletes load balancers asynchronously. Destroying the cluster
  # before it finishes recreates the exact orphan bug, so block until AWS agrees
  # they are gone.
  log "Waiting up to ${DRAIN_TIMEOUT}s for AWS to release them..."
  deadline=$(( $(date +%s) + DRAIN_TIMEOUT ))
  while :; do
    # A failed lookup here is not "zero remaining"; keep waiting and let the
    # deadline below decide, so a transient throttle does not wave the
    # teardown through.
    if ! remaining="$(cluster_lb_count)"; then
      remaining="unknown"
    elif [ "${remaining}" -eq 0 ]; then
      log "all load balancers released."
      break
    fi
    if [ "$(date +%s)" -ge "${deadline}" ]; then
      if [ "${remaining}" = "unknown" ]; then
        err "still cannot inventory ${EKS_CLUSTER}'s load balancers after ${DRAIN_TIMEOUT}s."
      else
        err "${remaining} load balancer(s) still tagged for ${EKS_CLUSTER} after ${DRAIN_TIMEOUT}s."
      fi
      err "Destroying the cluster now would orphan them. Investigate, or force cleanup with:"
      err "  DRY_RUN=false demo-platform/reaper/infra-sweep.sh"
      exit 1
    fi
    sleep 10
  done
fi

# ---------- Step 2: return Karpenter's nodes ----------------------------------

# Karpenter launches instances that exist in no Terraform state, and its
# controller -- running in this cluster -- is the only thing that ever
# terminates them. Destroying the cluster first leaves them running with no
# owner, which is the load balancer story again at instance prices.
#
# Deleting the NodeClaims makes Karpenter drain and terminate them while it can
# still see them. Nothing here is fatal: if Karpenter is not installed there is
# nothing to do, and if the drain fails the sweep in step 5 reports what is left.
if kubectl get nodeclaims >/dev/null 2>&1; then
  claims="$(kubectl get nodeclaims -o name 2>/dev/null | wc -l | tr -d ' ')"
  if [ "${claims}" != "0" ]; then
    log "Returning ${claims} Karpenter node(s) before the cluster goes away..."
    kubectl delete nodeclaims --all --timeout="${DRAIN_TIMEOUT}s" >/dev/null 2>&1 || \
      warn "not all NodeClaims drained; infra-sweep.sh will reclaim the instances"
  fi
fi

# ---------- Step 3: release the load balancers' security groups ---------------

# Deleting a Classic ELB leaves its `k8s-elb-<hash>` security group behind. The
# group is free, so it is easy to miss, but it pins the VPC and makes the
# destroy below hang until it times out. Clear the ones nothing is using now
# that the load balancers (and their ENIs) are gone.
log "Releasing security groups left by deleted load balancers..."
for sg in $(aws ec2 describe-security-groups --region "${AWS_REGION}" \
              --filters "Name=group-name,Values=k8s-elb-*" \
              --query 'SecurityGroups[].GroupId' --output text 2>/dev/null); do
  [ -n "${sg}" ] || continue
  attached="$(aws ec2 describe-network-interfaces --region "${AWS_REGION}" \
                --filters "Name=group-id,Values=${sg}" \
                --query 'length(NetworkInterfaces)' --output text 2>/dev/null || echo 1)"
  [ "${attached}" = "0" ] || continue
  if aws ec2 delete-security-group --region "${AWS_REGION}" --group-id "${sg}" >/dev/null 2>&1; then
    log "  released ${sg}"
  else
    warn "  could not release ${sg}; it may still be referenced"
  fi
done

# ---------- Step 4: destroy the cluster ---------------------------------------

if [ "${SKIP_TERRAFORM}" = true ]; then
  log "Skipping terraform destroy (--skip-terraform)."
else
  log "Destroying platform infrastructure (EKS, node groups, VPC)..."
  terraform -chdir="${REPO_ROOT}/platform/terraform" init -input=false >/dev/null
  terraform -chdir="${REPO_ROOT}/platform/terraform" destroy \
    -var-file=environments/dev.tfvars -auto-approve -input=false
fi

# ---------- Step 5: verify ----------------------------------------------------

log "Verifying no resources remain tagged for ${EKS_CLUSTER}..."
left="$(cluster_lb_count)" || {
  err "could not verify the teardown left nothing behind. Check by hand:"
  err "  DRY_RUN=true demo-platform/reaper/infra-sweep.sh"
  exit 1
}
if [ "${left}" -ne 0 ]; then
  err "${left} load balancer(s) survived the teardown -- these are now orphans."
  err "Run: DRY_RUN=false demo-platform/reaper/infra-sweep.sh"
  exit 1
fi

vols="$(aws ec2 describe-volumes --region "${AWS_REGION}" \
          --filters Name=status,Values=available "Name=tag-key,Values=kubernetes.io/cluster/${EKS_CLUSTER}" \
          --query 'length(Volumes)' --output text 2>/dev/null || echo 0)"
[ "${vols}" != "0" ] && warn "${vols} unattached EBS volume(s) tagged for ${EKS_CLUSTER}; infra-sweep.sh will reclaim them."

nodes="$(aws ec2 describe-instances --region "${AWS_REGION}" \
           --filters "Name=tag-key,Values=karpenter.sh/nodepool" \
                     "Name=tag-key,Values=kubernetes.io/cluster/${EKS_CLUSTER}" \
                     "Name=instance-state-name,Values=pending,running,stopping,stopped" \
           --query 'length(Reservations[].Instances[])' --output text 2>/dev/null || echo 0)"
if [ "${nodes}" != "0" ]; then
  err "${nodes} Karpenter instance(s) survived the teardown -- these bill until terminated."
  err "Run: DRY_RUN=false demo-platform/reaper/infra-sweep.sh"
  exit 1
fi

log "Teardown complete; no orphaned load balancers or nodes."
