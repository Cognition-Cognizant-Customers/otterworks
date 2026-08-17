#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
JOB="${1:?job name required}"
NS="${NS:-demo}"
STATE="$ROOT/scripts/tp_cronbox/state"
mkdir -p "$STATE/logs" "$STATE"
PYTHON="$("$ROOT/scripts/tp_cronbox/ensure_venv.sh")"
cat > "$STATE/config.ini" <<EOF
[aws]
access_key = 123456789012
secret_key = cronbox-local-secret
region = us-east-1
[database]
host = localhost
port = 5432
database = otterworks_analytics
user = otterworks
password = otterworks_dev
[services]
document_service_url = http://127.0.0.1:8088
file_service_url = http://127.0.0.1:8088
meilisearch_url = http://127.0.0.1:7700
meilisearch_api_key =
[s3]
data_lake_bucket = otterworks-data-lake
file_storage_bucket = otterworks-file-storage
quarantine_bucket = otterworks-file-quarantine
archive_bucket = otterworks-audit-archive
analytics_prefix = analytics/daily
EOF
sudo mkdir -p /opt/etl
sudo cp "$STATE/config.ini" /opt/etl/config.ini
if ! curl -fsS http://127.0.0.1:8088/health >/dev/null 2>&1; then
  nohup "$PYTHON" "$ROOT/scripts/tp_cronbox/corpus_api.py" --port 8088 > "$STATE/corpus.log" 2>&1 &
  echo $! > "$STATE/corpus.pid"
  for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:8088/documents >/dev/null 2>&1 && break; sleep 1; done
fi
export AWS_ENDPOINT_URL="${AWS_ENDPOINT_URL:-http://127.0.0.1:4566}"
export AWS_ACCESS_KEY_ID=123456789012
export AWS_SECRET_ACCESS_KEY=cronbox-local-secret
export AWS_DEFAULT_REGION=us-east-1
export TP_FAKETIME="${TP_FAKETIME:-2026-01-15 00:00:00}"
export PYTHONPATH="$ROOT/scripts/tp_cronbox${PYTHONPATH:+:$PYTHONPATH}"
case "$JOB" in
  analytics_daily|analytics_daily.py) SCRIPT=analytics_daily.py ;;
  storage_cleanup_daily|storage_cleanup_daily.py) SCRIPT=storage_cleanup_daily.py ;;
  audit_archive_weekly|audit_archive_weekly.py) SCRIPT=audit_archive_weekly.py ;;
  search_reindex_weekly|search_reindex_weekly.py) SCRIPT=search_reindex_weekly.py ;;
  user_activity_daily|user_activity_daily.py) SCRIPT=user_activity_daily.py ;;
  *) echo "unknown job: $JOB" >&2; exit 2 ;;
esac
"$ROOT/scripts/tp-run-deterministic.sh" "$STATE/venv/bin/python" "$ROOT/etl/scripts/$SCRIPT" > "$STATE/logs/${JOB%.py}.log" 2>&1
