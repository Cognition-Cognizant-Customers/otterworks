#!/usr/bin/env bash
# Create or drop the demo catalog through the SQL Statement Execution API.
# Called only by terraform_data.catalog in main.tf (see the comment there for
# why the Unity Catalog REST API cannot do this on a Default Storage
# workspace). Requires DATABRICKS_HOST and DATABRICKS_TOKEN.
set -euo pipefail

action="${1:?usage: catalog.sh create|drop <warehouse_id> <catalog>}"
warehouse="${2:?missing warehouse id}"
catalog="${3:?missing catalog name}"

case "$catalog" in
ow_tp*) ;;
*)
    echo "refusing to $action catalog '$catalog': shared workspace, ow_tp prefix required" >&2
    exit 1
    ;;
esac

case "$action" in
create) statement="CREATE CATALOG IF NOT EXISTS \`${catalog}\`" ;;
drop) statement="DROP CATALOG IF EXISTS \`${catalog}\` CASCADE" ;;
*)
    echo "unknown action '$action'" >&2
    exit 1
    ;;
esac

response=$(curl -sS --fail-with-body -X POST "${DATABRICKS_HOST%/}/api/2.0/sql/statements" \
    -H "Authorization: Bearer ${DATABRICKS_TOKEN}" \
    -H 'Content-Type: application/json' \
    -d "$(printf '{"warehouse_id":"%s","statement":"%s","wait_timeout":"50s","on_wait_timeout":"CANCEL"}' \
        "$warehouse" "$statement")")

if ! printf '%s' "$response" | grep -q '"SUCCEEDED"'; then
    echo "catalog $action failed: $response" >&2
    exit 1
fi
echo "catalog $action ok: $catalog"
