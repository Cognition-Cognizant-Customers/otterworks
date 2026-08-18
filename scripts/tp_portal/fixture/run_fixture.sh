#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# Fixture verification for the portal decomposition (no live AWS involved):
#   1. starts DynamoDB Local in Docker,
#   2. creates the ow-tp-portal-fixture-* tables,
#   3. builds the Lambda jars and starts PortalFixtureShim on :9095, which
#      invokes the real handler classes with API Gateway payload-v2 events.
#
# Then replay the golden transcript against it:
#   python3 scripts/tp_portal/transcript.py replay \
#     --base-url http://localhost:9095 \
#     --golden scripts/tp_portal/golden/portal-golden-transcript.json \
#     --run-mode fixture --namespace fixture \
#     --reset-cmd 'python3 scripts/tp_portal/reset_tables.py --prefix ow-tp-portal-fixture --endpoint-url http://localhost:4566' \
#     --out scripts/tp_portal/golden/fixture-replay.recon.json ...
# ------------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
SERVERLESS_DIR="${REPO_ROOT}/services/portal-serverless"
ENDPOINT="${DYNAMO_ENDPOINT:-http://localhost:4566}"
PREFIX="${TABLE_PREFIX:-ow-tp-portal-fixture}"

if ! curl -s -o /dev/null "${ENDPOINT}"; then
  echo "[fixture] starting DynamoDB Local..."
  docker run -d --name portal-fixture-dynamo -p 4566:8000 amazon/dynamodb-local
  for _ in $(seq 1 30); do
    curl -s -o /dev/null "${ENDPOINT}" && break
    sleep 2
  done
fi

echo "[fixture] creating tables under ${PREFIX}-*..."
python3 - "$ENDPOINT" "$PREFIX" <<'EOF'
import sys
import boto3
endpoint, prefix = sys.argv[1], sys.argv[2]
client = boto3.client("dynamodb", endpoint_url=endpoint, region_name="us-east-1",
                      aws_access_key_id="test", aws_secret_access_key="test")
tables = {"announcements": ("pk", "N"), "preferences": ("userId", "S"), "feedback": ("pk", "N")}
existing = client.list_tables()["TableNames"]
for context, (key, key_type) in tables.items():
    name = f"{prefix}-{context}"
    if name in existing:
        continue
    client.create_table(
        TableName=name, BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": key, "AttributeType": key_type}])
    client.get_waiter("table_exists").wait(TableName=name)
    print(f"created {name}")
EOF

if [ ! -f "${SERVERLESS_DIR}/announcements-service/target/announcements-service.jar" ]; then
  echo "[fixture] building Lambda jars..."
  (cd "${SERVERLESS_DIR}" && mvn -B -q package)
fi

CLASSPATH="${SERVERLESS_DIR}/announcements-service/target/announcements-service.jar"
CLASSPATH="${CLASSPATH}:${SERVERLESS_DIR}/preferences-service/target/preferences-service.jar"
CLASSPATH="${CLASSPATH}:${SERVERLESS_DIR}/feedback-service/target/feedback-service.jar"

echo "[fixture] compiling and starting the shim..."
javac -cp "${CLASSPATH}" -d "${SCRIPT_DIR}/out" "${SCRIPT_DIR}/PortalFixtureShim.java"
DYNAMO_ENDPOINT="${ENDPOINT}" TABLE_PREFIX="${PREFIX}" \
  exec java -cp "${CLASSPATH}:${SCRIPT_DIR}/out" PortalFixtureShim
