#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# Capture a request/response transcript for every legacy-portal route, covering
# all three bounded contexts plus the health endpoint. Run it against the
# on-prem deployment (before) and the containerized/platform deployment (after)
# and diff the two files — that diff is the parity contract for the migration.
#
# Usage:
#   BASE_URL=http://localhost:8095 ./capture-transcript.sh transcript.txt
#
# The requests are deterministic (fixed payloads, fixed order) so two runs
# against behaviorally-identical instances produce identical transcripts,
# modulo the volatile fields (ids, timestamps) that are normalized below.
# ------------------------------------------------------------------------------
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8095}"
OUT="${1:?usage: capture-transcript.sh <output-file>}"
: > "${OUT}"

# Normalize volatile fields so before/after transcripts are byte-comparable:
# ids and timestamps differ per run but are not behavior.
normalize() {
  sed -E \
    -e 's/"id":[0-9]+/"id":<ID>/g' \
    -e 's/"createdAt":"[^"]*"/"createdAt":"<TS>"/g' \
    -e 's/"timestamp":"[^"]*"/"timestamp":"<TS>"/g'
}

record() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-s -o /tmp/parity-body.$$ -w '%{http_code} %{content_type}' -X "${method}" "${BASE_URL}${path}")
  [ -n "${body}" ] && args+=(-H 'Content-Type: application/json' -d "${body}")
  local meta; meta="$(curl "${args[@]}")"
  {
    echo "=== ${method} ${path}"
    [ -n "${body}" ] && echo "--- request body: ${body}"
    echo "--- status content-type: ${meta}"
    echo "--- response body:"
    normalize < /tmp/parity-body.$$
    echo
    echo
  } >> "${OUT}"
  rm -f /tmp/parity-body.$$
}

# --- Health (common) ---
record GET /health
record GET /actuator/health

# --- Announcements context ---
record GET  /api/announcements
record POST /api/announcements '{"title":"Parity check announcement","body":"Captured for the on-prem -> platform migration transcript.","published":false}'
record GET  /api/announcements/1
record POST /api/announcements/1/publish
record GET  /api/announcements
record GET  '/api/announcements?publishedOnly=false'
# error path: unknown id -> 404 via GlobalExceptionHandler
record GET  /api/announcements/99999

# --- User Preferences context ---
record GET /api/preferences/parity-user
record PUT /api/preferences/parity-user '{"theme":"dark","locale":"en-US","emailNotifications":true}'
record GET /api/preferences/parity-user

# --- Feedback context ---
record POST /api/feedback '{"userId":"parity-user","rating":4,"message":"Parity transcript feedback entry"}'
record POST /api/feedback '{"userId":"parity-user","rating":5,"message":"Second entry for the average"}'
record GET  '/api/feedback?userId=parity-user'
record GET  /api/feedback/average-rating

echo "Transcript written to ${OUT}"
