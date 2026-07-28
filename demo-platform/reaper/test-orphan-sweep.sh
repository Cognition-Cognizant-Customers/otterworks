#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# Safety tests for the orphan-database sweep.
#
# The sweep drops any otterworks_* database it cannot account for, so the only
# thing standing between a live tenant and an irreversible DROP DATABASE is the
# mapping between a database name and a control-table id. That mapping is lossy
# in one direction -- tenant_db_name turns every non-alphanumeric character into
# '_', so otterworks_a_b is what an id of "a-b" produces -- which is why the
# comparison has to run id -> database and never the reverse.
#
# aws / list_tenant_dbs / gc_tenant are stubbed; this runs anywhere.
# ------------------------------------------------------------------------------
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  ok   - $1"; }
nope() { FAIL=$((FAIL+1)); echo "  FAIL - $1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else nope "$1 (expected '$3', got '$2')"; fi; }

# ---- stubs -------------------------------------------------------------------
# shellcheck disable=SC2034
CONTROL_TABLE="test-control"
# shellcheck disable=SC2034
AWS_REGION="us-east-1"
IDS=""            # control-table ids, newline separated (first page)
IDS_PAGE2=""      # second scan page, exercised when non-empty
SCAN_RC=0
DBS=""            # databases the RDS instance holds
REAPED=""
log()  { :; }
warn() { :; }
gc_tenant() { REAPED="${REAPED} $1"; }
# DBS is a space-separated list of database names, deliberately unquoted so it
# splits into one name per line, the shape the real function returns.
# shellcheck disable=SC2086
list_tenant_dbs() { printf '%s\n' ${DBS}; }

aws() {
  [ "${1:-}" = "dynamodb" ] && [ "${2:-}" = "scan" ] || return 0
  [ "${SCAN_RC}" -eq 0 ] || return "${SCAN_RC}"
  local page="${IDS}" next=""
  # --starting-token is only passed on the second call, so it identifies the page.
  if printf '%s' "$*" | grep -q -- "--starting-token"; then
    page="${IDS_PAGE2}"
  elif [ -n "${IDS_PAGE2}" ]; then
    next="tok"
  fi
  jq -nc --arg ids "${page}" --arg next "${next}" \
    '{Items: ($ids | split("\n") | map(select(length > 0) | {id:{S:.}}))}
     + (if $next == "" then {} else {NextToken: $next} end)'
}

eval "$(sed -n '/^tenant_db_name()/p' "${REPO_ROOT}/scripts/lib/tenant-common.sh")"
eval "$(sed -n '/^ctl_tenant_ids()/,/^}/p' "${REPO_ROOT}/demo-platform/lib/control-common.sh")"
eval "$(sed -n '/^sweep_orphan_dbs()/,/^}/p' "${SCRIPT_DIR}/reaper.sh")"

run() { REAPED=""; sweep_orphan_dbs >/dev/null 2>&1; }

echo "orphan-database sweep"

# The regression this suite exists for. Hyphens are legal in a tenant id and the
# fork prefix makes them common, but the database is otterworks_gtm_derek: read
# database -> id, the sweep looks up "gtm_derek", finds nothing, and drops the
# database of a tenant that was checked out minutes ago.
IDS="gtm-derek"
DBS="otterworks_gtm_derek"
run
check "keeps the database of a live tenant whose id contains a hyphen" "${REAPED# }" ""

IDS="derek"
DBS="otterworks_derek"
run
check "keeps the database of a live tenant with a plain id" "${REAPED# }" ""

IDS="derek"
DBS="otterworks_ghost"
run
check "reaps a database with no tenant behind it" "${REAPED# }" "ghost"

IDS="gtm-derek"
DBS="otterworks_gtm_derek otterworks_ghost"
run
check "  and still reaps one beside a live hyphenated tenant" "${REAPED# }" "ghost"

# The shared application database is not a tenant and has no control-table item.
IDS="derek"
DBS="otterworks otterworks_derek"
run
check "never reaps the shared otterworks database" "${REAPED# }" ""

# Both of these read as "every database is an orphan" if taken at face value,
# which is the shape of a mass wipe. Neither may delete anything.
SCAN_RC=1
IDS="derek"
DBS="otterworks_derek otterworks_ghost"
run
check "deletes nothing when the control-table scan fails" "${REAPED# }" ""
SCAN_RC=0

IDS=""
DBS="otterworks_derek otterworks_ghost"
run
check "deletes nothing when the control table reads empty" "${REAPED# }" ""

# A tenant listed only on the second page is still a live tenant: stopping at
# the first 1MB of results would delete everyone past the page boundary.
IDS="derek"
IDS_PAGE2="vedant"
DBS="otterworks_derek otterworks_vedant otterworks_ghost"
run
check "keeps tenants found beyond the first scan page" "${REAPED# }" "ghost"
IDS_PAGE2=""

echo "${PASS} passed, ${FAIL} failed"
[ "${FAIL}" -eq 0 ]
