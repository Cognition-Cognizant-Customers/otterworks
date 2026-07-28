#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# OtterWorks Demo Platform — idle tenant suspend (scale to zero)
#
# The economics of 100 tenants only work if a provisioned tenant that nobody is
# using costs nothing. A full tenant reserves ~1.5 vCPU / 3.5GiB; 100 of those
# is ~$3,600/month of nodes. Suspended, a tenant costs only its database rows
# and its DNS record, so spend tracks *active* tenants rather than provisioned
# ones -- roughly a 10x reduction at realistic workshop utilization.
#
# Idleness is measured from real HTTP traffic, not from a timer: ingress-nginx
# exports a per-namespace request counter on its metrics port, and every tenant
# is reached exclusively through that controller. Each run walks every tenant
# namespace and compares its counter against the value on the control-table item:
#
#   counter increased        -> tenant is in use; record it and reset the clock
#   counter unchanged/absent -> tenant took zero requests since the last run
#   counter decreased to >0  -> controller restarted, but has served this tenant
#                               since; that is real traffic, reset the clock
#   counter decreased to 0   -> controller restarted and served nothing; keep
#                               the clock, or a cycling controller would keep
#                               idle tenants awake forever
#   scaled up since last run -> just woken; reset the clock (see was_running)
#   idle for IDLE_AFTER      -> scale every Deployment in the namespace to zero
#
# Suspending preserves the namespace, config, secrets and the tenant's database
# (RDS is external), so waking is `tenant-scale.sh <id> up` (which the dashboard
# calls on check-out) and takes seconds.
#
# It does NOT preserve the tenant's in-cluster Redis or MeiliSearch: both run
# without persistence, so scaling them to zero discards sessions, the search
# index (rebuilt on use) and any injected chaos flag. Because a cleared chaos
# flag would silently un-plant the bug an attendee is hunting, a tenant with an
# active scenario is never auto-suspended -- see tenant_has_chaos below.
#
# Sourced by reaper.sh; also runnable standalone.
# ------------------------------------------------------------------------------
set -uo pipefail

CONTROL_TABLE="${CONTROL_TABLE:-otterworks-demo-control}"
AWS_REGION="${AWS_REGION:-us-east-1}"
INGRESS_NAMESPACE="${INGRESS_NAMESPACE:-ingress-nginx}"
# Metrics endpoint of the shared ingress controller. Exposed by the controller
# itself, so this needs no Prometheus deployment.
INGRESS_METRICS_URL="${INGRESS_METRICS_URL:-http://ingress-nginx-controller-metrics.${INGRESS_NAMESPACE}.svc:10254/metrics}"
# How long a tenant must take zero requests before it is suspended.
IDLE_AFTER_SECONDS="${IDLE_AFTER_SECONDS:-3600}"

idle_log()  { echo "[idle-suspend] $*"; }
idle_warn() { echo "[idle-suspend] WARN: $*" >&2; }

# Total ingress requests per tenant namespace, as "<namespace> <count>" lines.
# Uses the reaper pod's own network; falls back to exec-ing the controller pod
# when the metrics Service is not exposed.
#
# Returns non-zero if the scrape itself failed. That is NOT the same as a scrape
# that succeeded and contained no tenant series: the first means traffic is
# unknown, the second means there was none. Conflating them would let a metrics
# outage read as "every tenant is idle" and suspend the whole workshop at once.
ingress_request_counts() {
  local raw=""
  raw="$(curl -sf --max-time 10 "${INGRESS_METRICS_URL}" 2>/dev/null)"
  if [ -z "${raw}" ]; then
    local pod
    pod="$(kubectl -n "${INGRESS_NAMESPACE}" get pod \
             -l app.kubernetes.io/component=controller \
             -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
    [ -n "${pod}" ] || return 1
    raw="$(kubectl -n "${INGRESS_NAMESPACE}" exec "${pod}" -- \
             curl -sf --max-time 10 http://127.0.0.1:10254/metrics 2>/dev/null)"
  fi
  [ -n "${raw}" ] || return 1

  # nginx_ingress_controller_requests{...,namespace="otterworks-x",...} 12345
  #
  # `|| true` because grep exits 1 when the body carries no request series at
  # all, and under `pipefail` that would surface as a scrape failure and skip
  # the whole scan -- the opposite of this function's contract. The scrape has
  # already been validated above; from here an empty result means no traffic.
  printf '%s\n' "${raw}" \
    | grep '^nginx_ingress_controller_requests{' \
    | sed -n 's/.*namespace="\([^"]*\)".*} \([0-9.e+]*\)$/\1 \2/p' \
    | awk '{ total[$1] += $2 } END { for (ns in total) printf "%s %d\n", ns, total[ns] }' \
    || true
}

# Persist the observed counter and the time it was first seen at this value.
# Key attributes are PK/SK, matching control-common.sh -- DynamoDB rejects an
# update whose key names differ from the table schema.
record_activity() {
  local id="$1" count="$2" since="$3" out
  # Report the AWS error rather than discarding it. A silently-dropped write
  # here disables suspension entirely while looking healthy, because the next
  # scan reads no previous counter and restarts the idle clock forever.
  if ! out="$(aws dynamodb update-item --table-name "${CONTROL_TABLE}" --region "${AWS_REGION}" \
                --key "$(jq -n --arg id "TENANT#${id}" '{PK:{S:$id}, SK:{S:"META"}}')" \
                --update-expression "SET req_count = :c, idle_since = :s" \
                --expression-attribute-values \
                  "$(jq -n --arg c "${count}" --arg s "${since}" '{":c":{N:$c},":s":{N:$s}}')" 2>&1)"; then
    idle_warn "could not record activity for ${id}: ${out}"
    return 1
  fi
}

# Remember whether the tenant had any replicas up at the end of a pass. This is
# what makes a wake detectable: nothing on the wake path (tenant-scale.sh, the
# dashboard, a manual kubectl scale) writes to the control table, so the
# transition 0 -> running is the only evidence the reaper gets.
record_running() {
  local id="$1" running="$2" out
  if ! out="$(aws dynamodb update-item --table-name "${CONTROL_TABLE}" --region "${AWS_REGION}" \
                --key "$(jq -n --arg id "TENANT#${id}" '{PK:{S:$id}, SK:{S:"META"}}')" \
                --update-expression "SET was_running = :r" \
                --expression-attribute-values \
                  "$(jq -n --arg r "${running}" '{":r":{N:$r}}')" 2>&1)"; then
    idle_warn "could not record run state for ${id}: ${out}"
    return 1
  fi
}

# Number of Deployments currently running at least one replica.
running_deployments() {
  kubectl -n "$1" get deploy -o jsonpath='{range .items[*]}{.spec.replicas}{"\n"}{end}' 2>/dev/null \
    | awk '$1 > 0 { n++ } END { print n + 0 }'
}

# Does the tenant have an injected chaos scenario running? Such a tenant is a
# lab in progress: its Redis holds the bug, and Redis has no persistence, so
# suspending would quietly un-inject the scenario the attendee is debugging.
tenant_has_chaos() {
  local ns="$1"
  # Same access path inject-bug.sh uses, so the two agree on what "injected" means.
  kubectl -n "${ns}" exec deploy/redis -- redis-cli --scan --pattern 'chaos:*' 2>/dev/null | grep -q .
}

# Returns non-zero if the tenant is still running afterwards. The caller records
# the suspension only on success: writing was_running=0 for a tenant that never
# scaled down would make the next pass read it as a wake, reset the idle clock,
# and start the wait over -- so a persistently failing scale-down would keep the
# tenant running forever while the platform believed it had been suspended.
suspend_tenant() {
  local id="$1" ns="$2"
  idle_log "suspending ${id}: no ingress requests for >= ${IDLE_AFTER_SECONDS}s"
  if kubectl -n "${ns}" scale deployment --all --replicas=0 >/dev/null 2>&1; then
    ctl_audit "${id}" "suspend" "idle for ${IDLE_AFTER_SECONDS}s" 2>/dev/null || true
    return 0
  fi
  idle_warn "failed to scale down ${ns}; leaving its idle clock alone so the next pass retries"
  return 1
}

# Every tenant namespace that currently exists, as "<id> <namespace>" lines.
# The scan is driven from this list rather than from the metrics, because
# ingress-nginx only exports a counter series for a namespace once it has served
# a request since the controller started. A tenant nobody ever opened -- exactly
# the one worth suspending -- has no series at all, and a controller restart
# (routine on Spot) drops the series for every idle tenant.
tenant_namespaces() {
  kubectl get ns -l demo/tenant -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null \
    | while read -r ns; do
        [ -n "${ns}" ] || continue
        case "${ns}" in
          otterworks-platform|otterworks-system) continue ;;
          otterworks-*) printf '%s %s\n' "${ns#otterworks-}" "${ns}" ;;
        esac
      done
}

suspend_idle_tenants() {
  idle_log "idle scan starting (threshold=${IDLE_AFTER_SECONDS}s)"
  local counts now ns id count prev since running item
  # Traffic is the only evidence of use, so without it there is nothing to
  # decide on. Skipping the pass delays suspension until the next run; guessing
  # scales every attendee's environment to zero mid-workshop.
  if ! counts="$(ingress_request_counts)"; then
    idle_warn "skipping idle scan: could not read ingress metrics"
    return 1
  fi
  now="$(date -u +%s)"

  while read -r id ns; do
    [ -n "${id}" ] || continue
    ctl_tenant_exists "${id}" || continue

    # No counter series means the controller has routed nothing to this tenant,
    # which is zero traffic -- not a reason to skip it.
    count="$(printf '%s\n' "${counts}" | awk -v n="${ns}" '$1 == n { print $2; exit }')"
    [ -n "${count}" ] || count=0

    item="$(ctl_get "TENANT#${id}" "META")"
    # A perpetual tenant is the always-on reference environment: scaling it to
    # zero would put a multi-minute cold start in front of whoever opens it
    # next, which is the one thing it exists to avoid.
    if [ "$(printf '%s' "${item}" | jq -r '.Item.persistent.BOOL // false')" = "true" ]; then
      idle_log "${id}: persistent; not suspending"
      continue
    fi
    prev="$(printf '%s' "${item}" | jq -r '.Item.req_count.N // empty')"
    since="$(printf '%s' "${item}" | jq -r '.Item.idle_since.N // empty')"
    was_running="$(printf '%s' "${item}" | jq -r '.Item.was_running.N // empty')"

    running="$(running_deployments "${ns}")"
    if [ "${running}" -eq 0 ]; then
      # Asleep: nothing to suspend. Record that, so the tenant coming back is
      # recognisable as a wake on a later pass.
      [ "${was_running}" = "0" ] || record_running "${id}" 0
      continue
    fi

    # Waking is just `kubectl scale` -- tenant-scale.sh, the dashboard check-out
    # and a manual scale-up all leave the control table untouched, so the idle
    # clock still reads from before the tenant was scaled down and every one of
    # those paths would otherwise be undone by the next pass. Treat the tenant
    # running again as the activity that the wake itself represents.
    if [ "${was_running}" = "0" ]; then
      idle_log "${id}: running again after being scaled down; restarting the idle clock"
      record_activity "${id}" "${count}" "${now}"
      record_running "${id}" 1
      continue
    fi
    [ "${was_running}" = "1" ] || record_running "${id}" 1

    if [ -z "${prev}" ] || [ -z "${since}" ]; then
      # First observation: start the clock, decide on the next pass.
      record_activity "${id}" "${count}" "${now}"
      continue
    fi

    if [ "${count}" -gt "${prev}" ]; then
      # Requests served since the last run: tenant is in use, reset the clock.
      record_activity "${id}" "${count}" "${now}"
      continue
    fi

    if [ "${count}" -lt "${prev}" ]; then
      # Counters only ever increase, so a drop means the controller restarted.
      if [ "${count}" -gt 0 ]; then
        # It has already served this tenant since coming back, which is real
        # traffic in the recent past however high the pre-restart total was.
        record_activity "${id}" "${count}" "${now}"
        continue
      fi
      # Nothing served since the restart. Re-baseline but keep the existing idle
      # clock: crediting a restart alone as activity would let a cycling
      # controller keep genuinely idle tenants awake forever.
      idle_log "${id}: ingress counter reset (${prev} -> ${count}); keeping idle clock"
      record_activity "${id}" "${count}" "${since}"
    fi

    if [ $(( now - since )) -lt "${IDLE_AFTER_SECONDS}" ]; then
      idle_log "${id} idle for $(( now - since ))s (threshold ${IDLE_AFTER_SECONDS}s)"
      continue
    fi

    if tenant_has_chaos "${ns}"; then
      idle_log "${id} is idle but has an injected scenario; leaving it running"
      continue
    fi

    suspend_tenant "${id}" "${ns}" || continue
    record_activity "${id}" "${count}" "${now}"
    record_running "${id}" 0
  done <<< "$(tenant_namespaces)"

  idle_log "idle scan complete."
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
  # shellcheck source=/dev/null
  source "${REPO_ROOT}/demo-platform/lib/control-common.sh"
  suspend_idle_tenants
fi
