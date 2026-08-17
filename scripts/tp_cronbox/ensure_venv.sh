#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="$ROOT/scripts/tp_cronbox/state/venv"
mkdir -p "$ROOT/scripts/tp_cronbox/state"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
if ! "$VENV/bin/python" -c 'import boto3, pandas, psycopg2, requests' >/dev/null 2>&1; then
  "$VENV/bin/pip" install --disable-pip-version-check -q -r "$ROOT/scripts/tp_cronbox/requirements.txt"
fi
printf '%s\n' "$VENV/bin/python"
