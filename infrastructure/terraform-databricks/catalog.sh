#!/usr/bin/env bash
# Create or drop the parent-owned demo catalog via a SQL statement.
#
# This workspace has Default Storage enabled and rejects catalog creation
# through the Unity Catalog API (and therefore through the Terraform
# databricks_catalog resource); the SQL path succeeds. Only the parent
# orchestration session runs this — children never touch the shared catalog.
#
# Usage: catalog.sh create|drop [prefix]
# Env:   DATABRICKS_HOST, DATABRICKS_TOKEN, DATABRICKS_SQL_WAREHOUSE_ID (optional)
set -euo pipefail

action="${1:-}"
prefix="${2:-ow_tp}"
case "$action" in create|drop) ;; *) echo "usage: $0 create|drop [prefix]" >&2; exit 2 ;; esac
[[ "$prefix" =~ ^[a-z][a-z0-9_]*$ ]] || { echo "prefix must be lowercase snake_case" >&2; exit 2; }
: "${DATABRICKS_HOST:?DATABRICKS_HOST required}"
: "${DATABRICKS_TOKEN:?DATABRICKS_TOKEN required}"

warehouse_id="${DATABRICKS_SQL_WAREHOUSE_ID:-}"
if [[ -z "$warehouse_id" ]]; then
  warehouse_id=$(curl -fsS -H "Authorization: Bearer $DATABRICKS_TOKEN" \
    "$DATABRICKS_HOST/api/2.0/sql/warehouses" |
    python3 -c 'import json,sys; ws=[w for w in json.load(sys.stdin).get("warehouses",[]) if w.get("enable_serverless_compute")]; print(ws[0]["id"] if ws else "")')
fi
[[ -n "$warehouse_id" ]] || { echo "no serverless SQL warehouse available" >&2; exit 1; }

if [[ "$action" == "create" ]]; then
  stmt="CREATE CATALOG IF NOT EXISTS $prefix COMMENT 'OtterWorks tech-partnerships migration demo (parent-owned; safe to destroy)'"
else
  stmt="DROP CATALOG IF EXISTS $prefix CASCADE"
fi

response=$(curl -fsS -X POST -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  -H "Content-Type: application/json" "$DATABRICKS_HOST/api/2.0/sql/statements" \
  -d "$(python3 -c 'import json,sys; print(json.dumps({"statement": sys.argv[1], "warehouse_id": sys.argv[2], "wait_timeout": "50s"}))' "$stmt" "$warehouse_id")")
state=$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",{}).get("state","unknown"))')
echo "catalog $action $prefix: $state"
[[ "$state" == "SUCCEEDED" ]]
