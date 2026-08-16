#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --with pymongo==4.10.1 --with requests==2.32.3 "$REPO_ROOT/scripts/tp_preflight/atlas.py" "$@"
