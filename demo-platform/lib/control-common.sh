#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# OtterWorks Demo Platform — control-table (DynamoDB) helper library
#
# Sourced by:
#   - runner/entrypoint.sh  (deploy/teardown/inject/reset status + audit writes)
#   - reaper/reaper.sh       (read CONFIG#reaper, delete TENANT#/LOCK#, audit)
#
# The control table is the single source of truth for the demo platform (see
# demo-platform/docs/control-table-schema.md). It is durable and independent of
# any ephemeral tenant. Every write here is idempotent / retry-safe.
#
# NEVER put a secret in an argv here: these helpers only ever pass control-plane
# metadata (ids, status, urls, epochs) to the aws CLI — never DB_PASSWORD et al.
# ------------------------------------------------------------------------------

# Defaults (overridable by the environment).
CONTROL_TABLE="${CONTROL_TABLE:-otterworks-demo-control}"
AWS_REGION="${AWS_REGION:-us-east-1}"

# Epoch helpers. epoch_ms is best-effort unique (ms + small jitter) so appended
# AUDIT sort keys (`<epoch_ms>#<action>`) do not collide within the same second.
ctl_now()    { date -u +%s; }
ctl_now_ms() { echo $(( $(date -u +%s) * 1000 + (RANDOM % 1000) )); }

# Low-level GetItem. Prints the raw DynamoDB JSON ({} on any failure/miss).
ctl_get() {
  aws dynamodb get-item \
    --table-name "${CONTROL_TABLE}" --region "${AWS_REGION}" \
    --key "$(jq -n --arg pk "$1" --arg sk "$2" '{PK:{S:$pk},SK:{S:$sk}}')" \
    --output json 2>/dev/null || echo '{}'
}

# Does a TENANT#<id>/META item exist? (used by the reaper orphan sweep)
ctl_tenant_exists() {
  [ -n "$(ctl_get "TENANT#$1" META | jq -r '.Item.PK.S // empty' 2>/dev/null)" ]
}

# Every tenant id in the control table, one per line. Returns non-zero (and no
# output) if the scan itself fails, so callers can tell "no tenants" apart from
# "we do not know" — deleting on the latter would be a mass wipe. The orphan-DB
# sweep deletes anything absent from this list, so a truncated result is a data
# loss risk: page through LastEvaluatedKey rather than trust a single 1MB page.
ctl_tenant_ids() {
  local out token args
  token=""
  while :; do
    args=(dynamodb scan
      --table-name "${CONTROL_TABLE}" --region "${AWS_REGION}"
      --filter-expression "SK = :meta AND begins_with(PK, :tp)"
      --expression-attribute-values '{":meta":{"S":"META"},":tp":{"S":"TENANT#"}}'
      --output json)
    [ -n "${token}" ] && args+=(--starting-token "${token}")
    out="$(aws "${args[@]}" 2>/dev/null)" || return 1
    printf '%s' "${out}" | jq -r '.Items[]? | (.id.S // (.PK.S | sub("^TENANT#";"")))' 2>/dev/null
    token="$(printf '%s' "${out}" | jq -r '.NextToken // empty' 2>/dev/null)"
    [ -n "${token}" ] || break
  done
}

# UpdateItem for TENANT#<id>/META. Sets status + last_seen_at. Upserts if absent
# (safe: the dashboard normally creates the item at checkout, but a lone runner
# retry must never crash on a missing item).
ctl_update_status() {
  local id="$1" status="$2"
  aws dynamodb update-item \
    --table-name "${CONTROL_TABLE}" --region "${AWS_REGION}" \
    --key "$(jq -n --arg pk "TENANT#${id}" '{PK:{S:$pk},SK:{S:"META"}}')" \
    --update-expression "SET #s = :s, last_seen_at = :t" \
    --expression-attribute-names '{"#s":"status"}' \
    --expression-attribute-values \
      "$(jq -n --arg s "$status" --argjson t "$(ctl_now)" \
         '{":s":{S:$s},":t":{N:($t|tostring)}}')" >/dev/null
}

# Mark a tenant active and record its resolved coordinates.
ctl_set_active() {
  local id="$1" url="$2" api_url="$3" db="$4" ns="$5" expires_epoch="$6"
  aws dynamodb update-item \
    --table-name "${CONTROL_TABLE}" --region "${AWS_REGION}" \
    --key "$(jq -n --arg pk "TENANT#${id}" '{PK:{S:$pk},SK:{S:"META"}}')" \
    --update-expression "SET #s = :s, #u = :u, api_url = :a, db_name = :d, #ns = :n, expires_at = :e, last_seen_at = :t" \
    --expression-attribute-names '{"#s":"status","#u":"url","#ns":"namespace"}' \
    --expression-attribute-values \
      "$(jq -n --arg u "$url" --arg a "$api_url" --arg d "$db" --arg n "$ns" \
             --argjson e "$expires_epoch" --argjson t "$(ctl_now)" \
         '{":s":{S:"active"},":u":{S:$u},":a":{S:$a},":d":{S:$d},":n":{S:$n},":e":{N:($e|tostring)},":t":{N:($t|tostring)}}')" >/dev/null
}

# Append an append-only AUDIT#<id> event. `action` must be one of the values in
# control-table-schema.md (checkout, checkin, extend, redeploy, persist,
# deploy_ok, deploy_fail, reap, inject, reset, suspend, login_ok, login_fail).
ctl_audit() {
  local id="$1" action="$2" detail="${3:-}" actor="${ACTOR:-runner}"
  # `ts` MUST be epoch-milliseconds to match the sort key and the dashboard
  # writer/reader (lib/control.ts, lib/format.ts); a seconds value renders as
  # 1970 and mis-sorts. Compute once so the sort key and ts are identical.
  local ms; ms="$(ctl_now_ms)"
  aws dynamodb put-item \
    --table-name "${CONTROL_TABLE}" --region "${AWS_REGION}" \
    --item "$(jq -n \
        --arg pk "AUDIT#${id}" --arg sk "${ms}#${action}" \
        --arg actor "$actor" --arg detail "$detail" --argjson ts "${ms}" \
        '{PK:{S:$pk},SK:{S:$sk},action:{S:($sk|split("#")[1])},actor:{S:$actor},detail:{S:$detail},ts:{N:($ts|tostring)}}')" \
    >/dev/null || true
}

# Release only the reservation LOCK#<id>/LOCK item (idempotent). Called after a
# teardown frees a tenant so its id can be re-checked-out immediately instead of
# waiting for the lock's DynamoDB TTL (~15min) to lapse. The TENANT#/META record
# is left in place (status=free) for dashboard visibility.
ctl_release_lock() {
  local id="$1"
  aws dynamodb delete-item --table-name "${CONTROL_TABLE}" --region "${AWS_REGION}" \
    --key "$(jq -n --arg pk "LOCK#${id}" '{PK:{S:$pk},SK:{S:"LOCK"}}')" >/dev/null 2>&1 || true
}

# Delete both the TENANT#<id>/META and LOCK#<id>/LOCK items (idempotent).
ctl_delete_tenant() {
  local id="$1"
  aws dynamodb delete-item --table-name "${CONTROL_TABLE}" --region "${AWS_REGION}" \
    --key "$(jq -n --arg pk "TENANT#${id}" '{PK:{S:$pk},SK:{S:"META"}}')" >/dev/null 2>&1 || true
  aws dynamodb delete-item --table-name "${CONTROL_TABLE}" --region "${AWS_REGION}" \
    --key "$(jq -n --arg pk "LOCK#${id}" '{PK:{S:$pk},SK:{S:"LOCK"}}')" >/dev/null 2>&1 || true
}
