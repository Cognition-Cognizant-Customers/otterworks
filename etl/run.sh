#!/bin/bash
# ETL runner — sets up environment and runs the specified script
# Usage: ./run.sh <script_name.py>
if [ ! -f /opt/etl/.env ]; then
  echo "ERROR: /opt/etl/.env not found — required for AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, ETL_DB_PASSWORD, MEILISEARCH_API_KEY" >&2
  exit 1
fi
set -a
source /opt/etl/.env
set +a
export PYTHONPATH=/opt/etl
cd /opt/etl/scripts
python3 "$1"
