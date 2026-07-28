#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# Provision demo tenants from the command line, through the dashboard.
#
# This is the scripted equivalent of clicking around ops.otterworks.app, and it
# is deliberately the *only* path a provisioner needs: the dashboard runs the
# actual work as a runner Job under the control plane's IRSA role, so the caller
# needs no cluster access and no AWS permissions beyond reading the passcode.
# See infra/terraform/iam_provisioner.tf for the credential this expects
# (`de-demo-provisioner`).
#
# The passcode comes from Secrets Manager, or from DASHBOARD_PASSCODE if it is
# already in the environment. It is never passed on a process argv -- not to
# curl, and not to the jq that builds the login body: /proc/<pid>/cmdline is
# world-readable, so on the shared container an agent platform runs this in, any
# other local process could read it out of `ps`. It travels by environment and
# stdin instead, and the session cookie lands in a jar with 0600 permissions
# that is removed on exit.
#
# Usage:
#   tenant.sh list
#   tenant.sh checkout <id> [branch] [ttl]     # branch defaults to workshop-<id>
#   tenant.sh checkin  <id>
#   tenant.sh extend   <id> <ttl>
#   tenant.sh status   <id>
#   tenant.sh sync     <branch> [image-tag]    # CD: redeploy, creating if absent
#   tenant.sh persist  <id> true|false
#
# TENANT_PREFIX namespaces the ids `sync` derives, so that a fork of this repo
# drives its own environments off branch names identical to this one's.
#
# Examples:
#   tenant.sh checkout derek                   # -> workshop-derek, 8h
#   tenant.sh checkout derek workshop-derek 24h
#   tenant.sh sync workshop-derek              # -> tenant derek
#   OPS_HOST=https://ops.example.app tenant.sh list
# ------------------------------------------------------------------------------
set -euo pipefail

OPS_HOST="${OPS_HOST:-https://ops.otterworks.app}"
AWS_REGION="${AWS_REGION:-us-east-1}"
PASSCODE_SECRET_ID="${PASSCODE_SECRET_ID:-otterworks/dev/dashboard/passcode}"
DEFAULT_TTL="${DEFAULT_TTL:-8h}"
# What CD gives a tenant it creates for a branch nobody checked out by hand.
# Long enough to span a few days' work, short enough that an abandoned branch
# stops costing anything on its own.
CD_TTL="${CD_TTL:-72h}"

log()  { echo "[tenant] $*"; }
fail() { echo "[tenant] ERROR: $*" >&2; exit 1; }

for bin in curl jq; do
  command -v "${bin}" >/dev/null 2>&1 || fail "${bin} is required"
done

JAR="$(mktemp)"
chmod 600 "${JAR}"
cleanup() { rm -f "${JAR}"; }
trap cleanup EXIT

# ------------------------------------------------------------------------------

login() {
  local passcode
  passcode="${DASHBOARD_PASSCODE:-}"

  if [ -z "${passcode}" ]; then
    command -v aws >/dev/null 2>&1 ||
      fail "aws is required to read ${PASSCODE_SECRET_ID} (or set DASHBOARD_PASSCODE)"
    passcode="$(aws secretsmanager get-secret-value \
                  --region "${AWS_REGION}" \
                  --secret-id "${PASSCODE_SECRET_ID}" \
                  --query SecretString --output text 2>/dev/null)" ||
      fail "cannot read ${PASSCODE_SECRET_ID} -- is this credential the provisioner user?"
  fi

  [ -n "${passcode}" ] || fail "passcode is empty"

  # env.PASSCODE, not --arg: an argument would be visible in `ps` for the life
  # of the jq process. The environment of a process is readable only by its own
  # user and root.
  local code
  code="$(PASSCODE="${passcode}" jq -nc '{passcode: env.PASSCODE}' |
            curl -sS -o /dev/null -w '%{http_code}' \
                 -c "${JAR}" -X POST "${OPS_HOST}/api/auth/login" \
                 -H 'content-type: application/json' --data-binary @-)"

  case "${code}" in
    200|204) ;;
    401) fail "passcode rejected by ${OPS_HOST}" ;;
    429) fail "rate limited by ${OPS_HOST} -- too many failed logins, wait and retry" ;;
    *)   fail "login to ${OPS_HOST} returned HTTP ${code}" ;;
  esac
}

# Fails on any non-2xx so a rejected checkout is an error rather than a silent
# no-op that leaves the caller believing a tenant exists.
api() {
  local method="$1" path="$2" body="${3:-}"
  local out code

  if [ -n "${body}" ]; then
    out="$(printf '%s' "${body}" |
             curl -sS -w '\n%{http_code}' -b "${JAR}" -X "${method}" "${OPS_HOST}${path}" \
                  -H 'content-type: application/json' --data-binary @-)"
  else
    out="$(curl -sS -w '\n%{http_code}' -b "${JAR}" -X "${method}" "${OPS_HOST}${path}")"
  fi

  code="$(printf '%s' "${out}" | tail -n1)"
  out="$(printf '%s' "${out}" | sed '$d')"

  case "${code}" in
    2*) printf '%s' "${out}" ;;
    409) fail "conflict: $(printf '%s' "${out}" | jq -r '.error // .' 2>/dev/null || printf '%s' "${out}")" ;;
    *)   fail "${method} ${path} returned HTTP ${code}: ${out}" ;;
  esac
}

# GET that tolerates a 404, for "does this tenant exist?". Prints the body on
# 2xx and nothing on 404; any other status is still a hard failure, so a broken
# dashboard cannot be mistaken for an absent tenant (which would make CD create
# a second environment for a branch that already has one).
api_get_optional() {
  local path="$1" out code
  out="$(curl -sS -w '\n%{http_code}' -b "${JAR}" "${OPS_HOST}${path}")"
  code="$(printf '%s' "${out}" | tail -n1)"
  out="$(printf '%s' "${out}" | sed '$d')"

  case "${code}" in
    2*) printf '%s' "${out}" ;;
    404) return 0 ;;
    *)   fail "GET ${path} returned HTTP ${code}: ${out}" ;;
  esac
}

# Aligned without `column`, which is not in every base image (and is absent
# from the slim images an agent platform is likely to run this in).
#
# Only for endpoints that return tenant objects. The mutating endpoints return
# result objects instead ({ok, status}, {ok, expiresAt}), which have none of
# these fields and would render as a row of dashes -- so they get their own
# formatters below rather than being forced through this one.
table() {
  jq -r '(if type == "array" then . else [.] end)
         | .[]
         | [.id, .status, (.branch // "-"), (.url // "-")]
         | @tsv' |
    awk -F'\t' '{ printf "%-14s %-10s %-22s %s\n", $1, $2, $3, $4 }'
}

# A 202 from checkout or check-in means the state changed but the runner Job was
# not enqueued -- the tenant will sit in `deploying`/`draining` forever. Silence
# would make that look like success.
#
# Returns non-zero when degraded, so callers can suppress the progress messages
# that would otherwise tell someone to wait for work that was never started.
warn_if_degraded() {
  local warning
  warning="$(printf '%s' "$1" | jq -r '.warning // empty')"
  [ -n "${warning}" ] || return 0

  echo "[tenant] WARNING: ${warning}" >&2
  return 1
}

# ------------------------------------------------------------------------------

cmd="${1:-}"
[ -n "${cmd}" ] || fail "usage: tenant.sh <list|checkout|checkin|extend|status> [args]"
shift || true

case "${cmd}" in
  list)
    login
    api GET /api/tenants | table
    ;;

  checkout)
    id="${1:-}"
    [ -n "${id}" ] || fail "usage: tenant.sh checkout <id> [branch] [ttl]"
    branch="${2:-workshop-${id}}"
    ttl="${3:-${DEFAULT_TTL}}"

    login
    log "checking out '${id}' from ${branch} (ttl ${ttl})..."

    # 201 returns the tenant; 202 wraps it as {tenant, warning}.
    out="$(api POST /api/tenants/checkout \
             "$(jq -nc --arg id "${id}" --arg b "${branch}" --arg t "${ttl}" \
                     '{id:$id, branch:$b, ttl:$t, owner:"cli"}')")"
    printf '%s' "${out}" | jq -c '.tenant // .' | table

    if warn_if_degraded "${out}"; then
      log "deploying -- takes a few minutes; watch with: tenant.sh status ${id}"
    else
      # The id is now reserved but nothing is building it, so leaving with a
      # success status would strand the slot silently.
      fail "'${id}' is reserved but not deploying -- check the runner, then 'tenant.sh checkin ${id}' to release it"
    fi
    ;;

  checkin)
    id="${1:-}"
    [ -n "${id}" ] || fail "usage: tenant.sh checkin <id>"

    login
    log "checking in '${id}' (namespace, database, DNS and IRSA trust are removed)..."
    out="$(api POST "/api/tenants/${id}/checkin" '{}')"
    log "${id}: $(printf '%s' "${out}" | jq -r '.status // "accepted"')"

    # Explicit rather than relying on set -e to trip on the return value: a
    # teardown that was never enqueued leaves tenant resources billing, so it
    # has to be a visible failure.
    warn_if_degraded "${out}" ||
      fail "'${id}' was released in the control table but its resources were not torn down"
    ;;

  extend)
    id="${1:-}" ttl="${2:-}"
    if [ -z "${id}" ] || [ -z "${ttl}" ]; then fail "usage: tenant.sh extend <id> <ttl>"; fi

    login
    api POST "/api/tenants/${id}/extend" "$(jq -nc --arg t "${ttl}" '{ttl:$t}')" |
      jq -r --arg id "${id}" '"[tenant] \($id): now expires \(.expiresAt | todate)"'
    ;;

  status)
    id="${1:-}"
    [ -n "${id}" ] || fail "usage: tenant.sh status <id>"

    login
    api GET "/api/tenants/${id}" |
      jq -r '"id       : \(.id)",
             "status   : \(.status)",
             "branch   : \(.branch // "-")",
             "url      : \(.url // "-")",
             "api      : \(.apiUrl // "-")",
             "expires  : \(if .expiresAt then (.expiresAt | todate) else "-" end)",
             "pods     : \(.live.readyPods // 0)/\(.live.totalPods // 0) ready"'
    ;;

  # The CD entry point: make the environment for a branch match that branch.
  # Idempotent by design -- every push runs the same command whether or not the
  # tenant already exists, so the pipeline needs no state of its own.
  sync)
    branch="${1:-}"
    [ -n "${branch}" ] || fail "usage: tenant.sh sync <branch> [image-tag]"
    image_tag="${2:-}"

    # workshop-derek and demo-derek both mean tenant 'derek'. The dashboard
    # refuses a redeploy from a branch other than the one the tenant was
    # checked out from, so the second branch fails loudly instead of
    # overwriting the first branch's environment.
    #
    # TENANT_PREFIX scopes ids to one repository, so a fork's demo-derek is a
    # separate environment rather than the same one under two owners. Identical
    # branch names in two repositories are the case the branch check cannot
    # catch, because the branch names match.
    # Sanitized exactly as sanitizeId() in the dashboard does it, so that the
    # id CD asks for is the id the tenant is created under -- and so the image
    # tag CI pushed for it is the one the deploy then looks up.
    id="$(printf '%s' "${branch}" | sed -E 's#^(workshop|demo)[-/]##')"
    id="$(printf '%s' "${TENANT_PREFIX:+${TENANT_PREFIX}-}${id}" |
            tr '[:upper:]' '[:lower:]' |
            sed 's/[^a-z0-9-]/-/g; s/-\{2,\}/-/g; s/^-*//; s/-*$//' |
            cut -c1-40 | sed 's/-*$//')"
    [ -n "${id}" ] || fail "cannot derive a tenant id from branch '${branch}'"

    login
    existing="$(api_get_optional "/api/tenants/${id}")"

    if [ -z "${existing}" ] || [ "$(printf '%s' "${existing}" | jq -r '.status // "free"')" = "free" ]; then
      # CD creates ephemeral environments only. A perpetual tenant never expires
      # and never idles, so standing one up is a cost decision an operator makes
      # deliberately -- not something a push to a trusted branch does silently.
      if [ "${id}" = "main" ]; then
        fail "the perpetual tenant 'main' does not exist; create it with: tenant.sh checkout main main never"
      fi

      log "no environment for '${branch}'; creating tenant '${id}' (ttl ${CD_TTL})..."
      out="$(api POST /api/tenants/checkout \
               "$(jq -nc --arg id "${id}" --arg b "${branch}" --arg t "${CD_TTL}" --arg img "${image_tag}" \
                       '{id:$id, branch:$b, ttl:$t, owner:"ci"} + (if $img == "" then {} else {image_tag:$img} end)')")"
      printf '%s' "${out}" | jq -c '.tenant // .' | table
      warn_if_degraded "${out}" ||
        fail "'${id}' is reserved but not deploying -- check the runner"
    else
      log "redeploying '${id}' from ${branch}..."
      out="$(api POST "/api/tenants/${id}/redeploy" \
               "$(jq -nc --arg b "${branch}" --arg img "${image_tag}" \
                       '{branch:$b} + (if $img == "" then {} else {image_tag:$img} end)')")"
      warn_if_degraded "${out}" || fail "'${id}' redeploy was not enqueued -- check the runner"
      log "${id}: $(printf '%s' "${out}" | jq -r '.job // "accepted"')"
    fi

    log "watch with: tenant.sh status ${id}"
    ;;

  # Perpetual tenants are exempt from the reaper and from idle-suspend, so they
  # bill continuously. Deliberately a separate command from checkout.
  persist)
    id="${1:-}" flag="${2:-}"
    case "${flag}" in true|false) ;; *) fail "usage: tenant.sh persist <id> true|false" ;; esac
    [ -n "${id}" ] || fail "usage: tenant.sh persist <id> true|false"

    login
    api POST "/api/tenants/${id}/persist" "$(jq -nc --argjson p "${flag}" '{persistent:$p}')" |
      jq -r --arg id "${id}" '"[tenant] \($id): persistent=\(.persistent), expires \(.expiresAt | todate)"'
    ;;

  *)
    fail "unknown command '${cmd}' -- expected list, checkout, checkin, extend, status, sync or persist"
    ;;
esac
