#!/usr/bin/env bash
# Fixture verification for the mongo_invoices unit: migrate, snapshot the target
# state, migrate again (rerun proof), then emit the recon report with the
# idempotency evidence. Requires the Oracle fixture seeded for NS and a local
# MongoDB (MONGODB_URI / MONGODB_ATLAS_URI, default mongodb://localhost:27777).
set -euo pipefail

NS="${NS:?usage: NS=<ns> verify_invoices.sh}"
DIR="$(cd "$(dirname "$0")" && pwd)"
RUN="uv run --with oracledb==2.5.1 --with pymongo==4.15.5"
STATE="$(mktemp -t mongo_invoices_state.XXXXXX.json)"
trap 'rm -f "$STATE"' EXIT

MODE="${RUN_MODE:-fixture}"
$RUN "$DIR/migrate_invoices.py" --ns "$NS" --run-mode "$MODE" "$@"
$RUN "$DIR/recon_invoices.py" --ns "$NS" --run-mode "$MODE" --state-out "$STATE"
$RUN "$DIR/migrate_invoices.py" --ns "$NS" --run-mode "$MODE" "$@"
$RUN "$DIR/recon_invoices.py" --ns "$NS" --run-mode "$MODE" \
  --idempotency-state "$STATE"
