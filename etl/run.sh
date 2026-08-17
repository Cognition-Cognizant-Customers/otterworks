#!/bin/bash
# ETL runner — sets up environment and runs the specified script
# Usage: ./run.sh <script_name.py>
# Optional env var (set in /opt/etl/.env): AWS_EXPECTED_BUCKET_OWNER — AWS account ID
# asserted as the owner of every S3 bucket the jobs touch. Leave unset for LocalStack.
set -a
source /opt/etl/.env 2>/dev/null || true
set +a
export PYTHONPATH=/opt/etl
cd /opt/etl/scripts
python3 "$1"
