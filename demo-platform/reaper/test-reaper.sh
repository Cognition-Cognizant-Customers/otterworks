#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# Unit tests for the reaper's expiry decision.
#
# Everything this function decides is destructive and irreversible -- gc_tenant
# drops the tenant's database along with its namespace -- and it runs unattended
# on a schedule. The perpetual tenant has no owner watching it and no way to be
# rebuilt from a check-out, so the exemption below is the only thing standing
# between a flag regression and deleting the environment everyone shares.
#
# aws / gc_tenant are stubbed; this runs anywhere.
# ------------------------------------------------------------------------------
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  ok   - $1"; }
nope() { FAIL=$((FAIL+1)); echo "  FAIL - $1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else nope "$1 (expected '$3', got '$2')"; fi; }

# ---- stubs -------------------------------------------------------------------
# reap_expired reads these directly; the scan is stubbed, but `set -u` in the
# extracted function still requires them to be set.
# shellcheck disable=SC2034
CONTROL_TABLE="test-control"
# shellcheck disable=SC2034
AWS_REGION="us-east-1"
ITEMS=""
REAPED=""
log()  { :; }
warn() { :; }
gc_tenant() { REAPED="${REAPED} $1"; }
aws() {
  # Only the control-table scan is used by reap_expired.
  [ "${1:-}" = "dynamodb" ] && [ "${2:-}" = "scan" ] || return 0
  jq -nc --argjson items "[${ITEMS%,}]" '{Items: $items}'
}

eval "$(sed -n '/^reap_expired()/,/^}/p' "${SCRIPT_DIR}/reaper.sh")"

NOW="$(date -u +%s)"
LONG_EXPIRED=$(( NOW - 86400 ))
FUTURE=$(( NOW + 86400 ))

# DynamoDB item as the scan returns it. `persistent` is absent on tenants
# created before the flag existed, which must read as false rather than error.
item() {
  local id="$1" exp="$2" persistent="${3:-}"
  jq -nc --arg id "${id}" --arg e "${exp}" --arg p "${persistent}" \
    '{PK:{S:("TENANT#" + $id)}, SK:{S:"META"}, id:{S:$id}}
     + (if $e == "" then {} else {expires_at:{N:$e}} end)
     + (if $p == "" then {} else {persistent:{BOOL:($p == "true")}} end)'
}

run() { REAPED=""; reap_expired "${1:-0}" >/dev/null 2>&1; }

echo "reaper expiry decisions"

ITEMS="$(item expired "${LONG_EXPIRED}"),"
run
check "reaps an expired tenant" "${REAPED# }" "expired"

ITEMS="$(item live "${FUTURE}"),"
run
check "leaves a tenant that has not expired" "${REAPED# }" ""

# The perpetual tenant carries a ten-year expires_at as a backstop, so this
# case is deliberately the one where that backstop has failed -- an expiry
# already in the past. Only the flag can save it.
ITEMS="$(item main "${LONG_EXPIRED}" true),"
run
check "never reaps a persistent tenant, even with a stale expiry" "${REAPED# }" ""

# The exemption must be the flag and nothing else: an ordinary expired tenant
# in the same scan is still collected.
ITEMS="$(item main "${LONG_EXPIRED}" true),$(item ephemeral "${LONG_EXPIRED}"),"
run
check "  and still reaps expired tenants beside it" "${REAPED# }" "ephemeral"

ITEMS="$(item explicit "${LONG_EXPIRED}" false),"
run
check "reaps a tenant whose persistence was cleared" "${REAPED# }" "explicit"

# A reserved tenant is mid-deploy: expires_at is written only once the deploy
# succeeds, so a missing one means "too early to judge", not "expired in 1970".
ITEMS="$(item deploying ""),"
run
check "skips a tenant with no expiry yet" "${REAPED# }" ""

# Grace is what stops a tenant being collected in the seconds between its TTL
# lapsing and its owner extending it.
ITEMS="$(item recent "$(( NOW - 60 ))"),"
run 3600
check "honours the grace period" "${REAPED# }" ""
run 0
check "  and collects it once grace has passed" "${REAPED# }" "recent"

echo "${PASS} passed, ${FAIL} failed"
[ "${FAIL}" -eq 0 ]
