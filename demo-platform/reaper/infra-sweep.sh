#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# OtterWorks Demo Platform — infrastructure orphan sweep
#
# The tenant sweep in reaper.sh GCs resources that belong to a *tenant* (its
# namespace, database, S3 prefix, DynamoDB partition). This sweep covers the
# other, historically leakier class: AWS resources that Kubernetes creates
# IMPLICITLY and that therefore live outside Terraform state.
#
# Why this exists: a `Service type=LoadBalancer` makes the AWS cloud-controller
# provision an ELB/NLB. That load balancer is owned by nothing Terraform knows
# about, so deleting the cluster (or the Service, while the controller is down)
# strands it. Three Classic ELBs and one NLB were stranded exactly this way when
# the cluster was replaced, and billed for over a month with zero backends.
#
# The sweep is deliberately conservative — a resource is only ever deleted when
# it carries an OtterWorks/Kubernetes ownership tag AND its owner provably no
# longer exists. Untagged resources are reported, never deleted, because this
# account is shared with unrelated workloads.
#
# Covered:
#   (a) ELB (classic) + ELBv2 (ALB/NLB) tagged kubernetes.io/cluster/<name>
#       where <name> is not a live EKS cluster, or tagged
#       kubernetes.io/service-name=<ns>/<svc> where that Service is gone.
#   (b) ELBv2 target groups with no load balancer attached.
#   (c) Available (unattached) EBS volumes tagged for a dead cluster.
#   (d) Unassociated Elastic IPs tagged for a dead cluster.
#   (e) Route53 A/TXT records under the demo host suffix with no live tenant.
#   (f) k8s-elb-* security groups with no attached ENI (these block VPC deletes).
#   (g) EC2 instances Karpenter launched for a cluster that no longer exists.
#
# DRY_RUN=true (default) reports only. Set DRY_RUN=false to actually delete.
# ------------------------------------------------------------------------------
set -uo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
EKS_CLUSTER="${EKS_CLUSTER:-otterworks-dev}"
HOST_SUFFIX="${HOST_SUFFIX:-demo.otterworks.app}"
DNS_ZONE_ID="${DNS_ZONE_ID:-}"
DRY_RUN="${DRY_RUN:-true}"

sweep_log()  { echo "[infra-sweep] $*"; }
sweep_warn() { echo "[infra-sweep] WARN: $*" >&2; }

# Emit the action, and only perform it when DRY_RUN=false.
act() {
  if [ "${DRY_RUN}" = "false" ]; then
    sweep_log "DELETE $*"
    "$@" >/dev/null 2>&1 || sweep_warn "delete failed: $*"
  else
    sweep_log "DRY-RUN would delete: $*"
  fi
}

# Cache of live EKS cluster names; a load balancer tagged for a cluster that is
# not in this list can never be reclaimed by any cloud-controller.
#
# Every deletion in this file is justified by a cluster being absent from this
# list, so a failed lookup must never be mistaken for "no clusters exist" --
# that inverts the safety check and makes the entire live estate look orphaned.
# Fail closed, the same way service_is_live does for an unreachable API server.
LIVE_CLUSTERS=""
load_live_clusters() {
  local out status
  out="$(aws eks list-clusters --region "${AWS_REGION}" --query 'clusters[]' --output text 2>&1)"
  status=$?
  if [ "${status}" -ne 0 ]; then
    sweep_warn "could not list EKS clusters (exit ${status}): ${out}"
    return 1
  fi
  # `--output text` separates a flat list with TABS, but cluster_is_live matches
  # on space-delimited word boundaries. Left as-is, a second cluster in the
  # account would make *every* cluster fail that match -- including the one
  # being swept for -- and the sweep would treat the whole live estate as
  # orphaned. Normalise to single spaces so the boundary check holds.
  LIVE_CLUSTERS="$(printf '%s' "${out}" | tr -s '[:space:]' ' ')"
  sweep_log "live EKS clusters: ${LIVE_CLUSTERS:-<none>}"
}

cluster_is_live() {
  local name="$1"
  case " ${LIVE_CLUSTERS} " in *" ${name} "*) return 0 ;; *) return 1 ;; esac
}

# Which clusters' leftovers this platform is entitled to delete. "Tagged for a
# cluster that no longer exists" alone is not ownership: this account also holds
# unrelated workloads, and another team's dead cluster is not ours to clean up.
#
# The same list is expressed as IAM conditions on the reaper's role (see
# iam_dashboard.tf), so a bug here cannot widen the blast radius beyond it --
# IAM refuses the call. Keeping both in step is why this is a list rather than
# an implicit "anything not live".
#
# The platform's own cluster is always ours; the variable adds names it used to
# run under. Unioned rather than overridden, because a caller that passes only
# the extra names -- which is what "keep this in step with the Terraform
# variable" invites, since that one holds extras only -- would otherwise silence
# the sweep for the live platform's own orphans, the ones it exists to catch.
# The Terraform local concats cluster_name the same way.
SWEEPABLE_CLUSTERS="${EKS_CLUSTER} ${SWEEPABLE_CLUSTERS:-}"

cluster_is_ours() {
  local name="$1"
  case " ${SWEEPABLE_CLUSTERS} " in *" ${name} "*) return 0 ;; *) return 1 ;; esac
}

# An orphan is a resource tagged for a cluster we own that no longer exists.
cluster_is_dead_and_ours() {
  local name="$1"
  cluster_is_ours "${name}" || return 1
  ! cluster_is_live "${name}"
}

# A k8s Service still exists (and so still owns its load balancer). Only
# meaningful when we can reach the cluster; if we cannot, report "exists" so the
# sweep never deletes on the basis of an unreachable API server.
#
# This asks OUR cluster. A Service belonging to someone else's cluster is
# trivially absent here, so callers must establish ownership first -- the answer
# is meaningless otherwise.
service_is_live() {
  local ns="${1%%/*}" svc="${1##*/}"
  [ -n "${ns}" ] && [ -n "${svc}" ] || return 0
  kubectl version >/dev/null 2>&1 || return 0
  kubectl -n "${ns}" get svc "${svc}" >/dev/null 2>&1
}

# Extract the cluster name from a kubernetes.io/cluster/<name>=owned tag key.
cluster_from_tags() {
  jq -r '.[]? | select(.Key | startswith("kubernetes.io/cluster/")) | .Key | sub("kubernetes.io/cluster/";"")' 2>/dev/null | head -1
}

# ------------------------------------------------------------------------------
# (a) Classic ELBs
# ------------------------------------------------------------------------------
sweep_classic_elbs() {
  local lb tags cluster svc backends
  for lb in $(aws elb describe-load-balancers --region "${AWS_REGION}" \
                --query 'LoadBalancerDescriptions[].LoadBalancerName' --output text 2>/dev/null); do
    tags="$(aws elb describe-tags --region "${AWS_REGION}" --load-balancer-names "${lb}" \
              --query 'TagDescriptions[0].Tags' --output json 2>/dev/null)"
    cluster="$(printf '%s' "${tags}" | cluster_from_tags)"
    svc="$(printf '%s' "${tags}" | jq -r '.[]? | select(.Key=="kubernetes.io/service-name") | .Value' 2>/dev/null)"
    # Not Kubernetes-owned -> not ours to reap.
    [ -n "${cluster}" ] || continue

    if cluster_is_dead_and_ours "${cluster}"; then
      backends="$(aws elb describe-load-balancers --region "${AWS_REGION}" --load-balancer-names "${lb}" \
                    --query 'length(Instances)' --output text 2>/dev/null)"
      sweep_warn "orphan classic ELB ${lb} (cluster '${cluster}' no longer exists, svc=${svc:-?}, backends=${backends:-?})"
      act aws elb delete-load-balancer --region "${AWS_REGION}" --load-balancer-name "${lb}"
    elif cluster_is_ours "${cluster}" && [ -n "${svc}" ] && ! service_is_live "${svc}"; then
      sweep_warn "orphan classic ELB ${lb} (Service ${svc} is gone)"
      act aws elb delete-load-balancer --region "${AWS_REGION}" --load-balancer-name "${lb}"
    fi
  done
}

# ------------------------------------------------------------------------------
# (a) ALB / NLB
# ------------------------------------------------------------------------------
sweep_v2_elbs() {
  local arn name tags cluster svc
  for arn in $(aws elbv2 describe-load-balancers --region "${AWS_REGION}" \
                 --query 'LoadBalancers[].LoadBalancerArn' --output text 2>/dev/null); do
    # .../loadbalancer/net/<name>/<id> -- the last segment is the id, not the name.
    name="${arn##*loadbalancer/}"; name="${name#*/}"; name="${name%%/*}"
    tags="$(aws elbv2 describe-tags --region "${AWS_REGION}" --resource-arns "${arn}" \
              --query 'TagDescriptions[0].Tags' --output json 2>/dev/null)"
    cluster="$(printf '%s' "${tags}" | cluster_from_tags)"
    svc="$(printf '%s' "${tags}" | jq -r '.[]? | select(.Key=="kubernetes.io/service-name") | .Value' 2>/dev/null)"
    [ -n "${cluster}" ] || continue

    if cluster_is_dead_and_ours "${cluster}"; then
      sweep_warn "orphan ELBv2 ${name} (cluster '${cluster}' no longer exists, svc=${svc:-?})"
      act aws elbv2 delete-load-balancer --region "${AWS_REGION}" --load-balancer-arn "${arn}"
    elif cluster_is_ours "${cluster}" && [ -n "${svc}" ] && ! service_is_live "${svc}"; then
      sweep_warn "orphan ELBv2 ${name} (Service ${svc} is gone)"
      act aws elbv2 delete-load-balancer --region "${AWS_REGION}" --load-balancer-arn "${arn}"
    fi
  done
}

# ------------------------------------------------------------------------------
# (b) Target groups left behind by a deleted load balancer
# ------------------------------------------------------------------------------
sweep_target_groups() {
  local tg name lbs cluster
  for tg in $(aws elbv2 describe-target-groups --region "${AWS_REGION}" \
                --query 'TargetGroups[].TargetGroupArn' --output text 2>/dev/null); do
    lbs="$(aws elbv2 describe-target-groups --region "${AWS_REGION}" --target-group-arns "${tg}" \
             --query 'length(TargetGroups[0].LoadBalancerArns)' --output text 2>/dev/null)"
    [ "${lbs}" = "0" ] || continue
    name="${tg##*/}"
    # Only reap target groups Kubernetes created for a cluster we own. A target
    # group with no load balancer is garbage whether or not its cluster still
    # runs, but whose garbage it is still decides if we may touch it.
    cluster="$(aws elbv2 describe-tags --region "${AWS_REGION}" --resource-arns "${tg}" \
                 --query 'TagDescriptions[0].Tags' --output json 2>/dev/null | cluster_from_tags)"
    [ -n "${cluster}" ] || continue
    cluster_is_ours "${cluster}" || continue
    sweep_warn "orphan target group ${name} (no load balancer attached)"
    act aws elbv2 delete-target-group --region "${AWS_REGION}" --target-group-arn "${tg}"
  done
}

# ------------------------------------------------------------------------------
# (c) Unattached EBS volumes belonging to a dead cluster
# ------------------------------------------------------------------------------
sweep_ebs_volumes() {
  local vol cluster
  for vol in $(aws ec2 describe-volumes --region "${AWS_REGION}" \
                 --filters Name=status,Values=available \
                 --query 'Volumes[].VolumeId' --output text 2>/dev/null); do
    cluster="$(aws ec2 describe-volumes --region "${AWS_REGION}" --volume-ids "${vol}" \
                 --query 'Volumes[0].Tags' --output json 2>/dev/null | cluster_from_tags)"
    [ -n "${cluster}" ] || { sweep_log "unattached EBS ${vol} has no cluster tag; reporting only"; continue; }
    cluster_is_dead_and_ours "${cluster}" || continue
    sweep_warn "orphan EBS volume ${vol} (cluster '${cluster}' no longer exists)"
    act aws ec2 delete-volume --region "${AWS_REGION}" --volume-id "${vol}"
  done
}

# ------------------------------------------------------------------------------
# (d) Unassociated Elastic IPs (billed hourly while idle)
# ------------------------------------------------------------------------------
sweep_eips() {
  local alloc cluster
  for alloc in $(aws ec2 describe-addresses --region "${AWS_REGION}" \
                   --query 'Addresses[?AssociationId==null].AllocationId' --output text 2>/dev/null); do
    [ -n "${alloc}" ] || continue
    # An unassociated EIP is not evidence of an orphan: other workloads in this
    # account legitimately reserve addresses for stopped instances. Release only
    # what a dead cluster left behind.
    cluster="$(aws ec2 describe-addresses --region "${AWS_REGION}" --allocation-ids "${alloc}" \
                 --query 'Addresses[0].Tags' --output json 2>/dev/null | cluster_from_tags)"
    if [ -z "${cluster}" ]; then
      sweep_log "unassociated Elastic IP ${alloc} has no cluster tag; reporting only"
      continue
    fi
    cluster_is_dead_and_ours "${cluster}" || continue
    sweep_warn "orphan Elastic IP ${alloc} (cluster '${cluster}' no longer exists)"
    act aws ec2 release-address --region "${AWS_REGION}" --allocation-id "${alloc}"
  done
}

# ------------------------------------------------------------------------------
# (e) Route53 records for tenants that no longer exist
#
# external-dns normally removes these, but only while it is running — records
# created before external-dns was reconfigured, or left when the cluster died,
# persist forever. Requires ctl_tenant_exists from control-common.sh; skipped
# when the control plane is not sourced.
# ------------------------------------------------------------------------------
sweep_route53() {
  [ -n "${DNS_ZONE_ID}" ] || { sweep_log "DNS_ZONE_ID unset; skipping Route53 sweep"; return 0; }
  command -v ctl_tenant_exists >/dev/null 2>&1 || { sweep_log "control plane not sourced; skipping Route53 sweep"; return 0; }

  local records name label id batch
  records="$(aws route53 list-resource-record-sets --hosted-zone-id "${DNS_ZONE_ID}" \
               --query "ResourceRecordSets[?Type=='A' || Type=='TXT'].Name" --output text 2>/dev/null)"
  for name in ${records}; do
    name="${name%.}"
    case "${name}" in
      *".${HOST_SUFFIX}") ;;
      *) continue ;;
    esac
    label="${name%%".${HOST_SUFFIX}"}"
    # Only the shapes a tenant deploy actually produces: t-<id> / api-t-<id>,
    # plus the cname-/txt- ownership records external-dns writes alongside them.
    #
    # Matching "anything under the suffix" and inferring a tenant id from what
    # is left would make every platform record look like a tenant that does not
    # exist. cert-manager's _acme-challenge record lives here during a wildcard
    # renewal, and deleting it mid-challenge fails the renewal -- losing TLS for
    # every tenant at once, to reclaim nothing.
    case "${label}" in
      t-*|api-t-*|cname-t-*|cname-api-t-*|txt-t-*|txt-api-t-*) ;;
      *) sweep_log "skipping ${name}: not a tenant record"; continue ;;
    esac
    id="${label#cname-}"; id="${id#txt-}"; id="${id#api-}"; id="${id#t-}"
    [ -n "${id}" ] || continue
    if ! ctl_tenant_exists "${id}"; then
      sweep_warn "orphan Route53 record ${name} (no TENANT# item for '${id}')"
      if [ "${DRY_RUN}" = "false" ]; then
        batch="$(aws route53 list-resource-record-sets --hosted-zone-id "${DNS_ZONE_ID}" \
                   --query "ResourceRecordSets[?Name=='${name}.']" --output json 2>/dev/null \
                 | jq -c '{Changes: [.[] | {Action:"DELETE", ResourceRecordSet: .}]}')"
        if [ "$(printf '%s' "${batch}" | jq '.Changes | length')" -gt 0 ]; then
          aws route53 change-resource-record-sets --hosted-zone-id "${DNS_ZONE_ID}" \
            --change-batch "${batch}" >/dev/null 2>&1 || sweep_warn "failed deleting ${name}"
        fi
      fi
    fi
  done
}

# ------------------------------------------------------------------------------
# (f) Security groups left behind by deleted Kubernetes load balancers
#
# Deleting a Classic ELB does NOT delete the `k8s-elb-<hash>` security group the
# cloud-controller created for it. These cost nothing, which is why they go
# unnoticed -- but they hold a reference to the VPC and will block `terraform
# destroy` on it indefinitely. Three of them stalled a teardown of this very
# cluster after the orphan ELBs above were removed.
#
# Deleted only when the group has no attached network interface, so a group
# still in use by a live resource is never touched.
# ------------------------------------------------------------------------------
sweep_elb_security_groups() {
  local sg name attached cluster
  for sg in $(aws ec2 describe-security-groups --region "${AWS_REGION}" \
                --filters "Name=group-name,Values=k8s-elb-*" \
                --query 'SecurityGroups[].GroupId' --output text 2>/dev/null); do
    [ -n "${sg}" ] || continue
    name="$(aws ec2 describe-security-groups --region "${AWS_REGION}" --group-ids "${sg}" \
              --query 'SecurityGroups[0].GroupName' --output text 2>/dev/null)"
    cluster="$(aws ec2 describe-security-groups --region "${AWS_REGION}" --group-ids "${sg}" \
                 --query 'SecurityGroups[0].Tags' --output json 2>/dev/null | cluster_from_tags)"
    [ -n "${cluster}" ] || { sweep_log "security group ${sg} has no cluster tag; reporting only"; continue; }
    cluster_is_ours "${cluster}" || continue
    attached="$(aws ec2 describe-network-interfaces --region "${AWS_REGION}" \
                  --filters "Name=group-id,Values=${sg}" \
                  --query 'length(NetworkInterfaces)' --output text 2>/dev/null)"
    [ "${attached}" = "0" ] || continue
    sweep_warn "orphan load balancer security group ${sg} (${name}, no attached ENI)"
    act aws ec2 delete-security-group --region "${AWS_REGION}" --group-id "${sg}"
  done
}

# ------------------------------------------------------------------------------
# (g) Karpenter nodes whose cluster is gone
#
# Karpenter nodes are the same hazard as load balancers, and a more expensive
# one: nothing in Terraform state knows they exist, and the only thing that ever
# terminates them is the Karpenter controller running inside the cluster. Delete
# the cluster and the instances keep running -- a c5.4xlarge at roughly $250 a
# month each, indefinitely. teardown-cluster.sh drains them first for that
# reason; this is the backstop for when it was not used, or died partway.
#
# Managed node group instances are deliberately not covered: those belong to a
# node group Terraform destroys with the cluster, and terminating one that is
# merely between health checks would be destructive.
# ------------------------------------------------------------------------------
sweep_karpenter_instances() {
  local id cluster
  for id in $(aws ec2 describe-instances --region "${AWS_REGION}" \
                --filters "Name=tag-key,Values=karpenter.sh/nodepool" \
                          "Name=instance-state-name,Values=pending,running,stopping,stopped" \
                --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null); do
    [ -n "${id}" ] || continue
    cluster="$(aws ec2 describe-instances --region "${AWS_REGION}" --instance-ids "${id}" \
                 --query 'Reservations[0].Instances[0].Tags' --output json 2>/dev/null | cluster_from_tags)"
    [ -n "${cluster}" ] || { sweep_log "Karpenter instance ${id} has no cluster tag; reporting only"; continue; }
    cluster_is_dead_and_ours "${cluster}" || continue
    sweep_warn "orphan Karpenter instance ${id} (cluster '${cluster}' no longer exists)"
    act aws ec2 terminate-instances --region "${AWS_REGION}" --instance-ids "${id}"
  done
}

infra_sweep() {
  sweep_log "infrastructure orphan sweep starting (region=${AWS_REGION}, dry_run=${DRY_RUN})"
  # Without a trustworthy live-cluster list there is no safe way to tell an
  # orphan from a running tenant's load balancer, so do nothing at all. A
  # skipped sweep costs a few idle resources until the next run; a sweep run on
  # bad data deletes the shared ingress out from under every tenant.
  if ! load_live_clusters; then
    sweep_warn "aborting sweep: cannot confirm which clusters are live"
    return 1
  fi
  sweep_classic_elbs
  sweep_v2_elbs
  sweep_target_groups
  # After the load balancers are gone, their security groups become deletable.
  sweep_elb_security_groups
  sweep_karpenter_instances
  # Ordered after the instances: terminating one releases its root volume, which
  # this then finds unattached.
  sweep_ebs_volumes
  sweep_eips
  sweep_route53
  sweep_log "infrastructure orphan sweep complete."
}

# Allow direct execution as well as sourcing from reaper.sh.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }
  infra_sweep
fi
