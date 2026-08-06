#!/usr/bin/env bash
#
# Run every unit's test suite with coverage instrumentation, copy each unit's
# machine-readable report into coverage/<unit>/, print an aggregate table, and
# exit non-zero if any unit failed.
#
# The old `make test-coverage` appended `|| true` to all seven of its lines, so
# it could not fail and produced no aggregate -- coverage could be neither
# trended nor gated. This script is the replacement: it keeps running after a
# unit fails (so one broken toolchain does not hide the other thirteen numbers)
# but it always reports that failure in the table and in the exit status.
#
# Usage:
#   scripts/coverage/run-coverage.sh                 # all units
#   scripts/coverage/run-coverage.sh api-gateway ... # only the named units
#
# Env:
#   COVERAGE_DIR   where reports are collected (default: coverage)
#   FAIL_FAST=1    stop at the first failing unit

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# shellcheck source=scripts/coverage/units.sh
source "$REPO_ROOT/scripts/coverage/units.sh"

COVERAGE_DIR="${COVERAGE_DIR:-coverage}"
FAIL_FAST="${FAIL_FAST:-0}"
SELECTED=("$@")

mkdir -p "$COVERAGE_DIR"

selected() {
  [[ ${#SELECTED[@]} -eq 0 ]] && return 0
  local want
  for want in "${SELECTED[@]}"; do [[ "$want" == "$1" ]] && return 0; done
  return 1
}

failed=()
ran=()

for record in "${COVERAGE_UNITS[@]}"; do
  # `read` with a non-whitespace IFS leaves the unsplit remainder in the last
  # variable, so a command containing a pipe survives intact.
  IFS='|' read -r name workdir fmt report cmd <<<"$record"
  selected "$name" || continue
  ran+=("$name")

  echo ""
  echo "=============================================================="
  echo "=== $name  ($workdir)"
  echo "=============================================================="

  dest="$COVERAGE_DIR/$name"
  rm -rf "$dest" && mkdir -p "$dest"
  echo "$fmt" >"$dest/format.txt"

  if (cd "$workdir" && eval "$cmd"); then
    status=0
  else
    status=$?
    failed+=("$name")
    echo "!!! $name FAILED (exit $status)"
  fi
  echo "$status" >"$dest/status.txt"

  # The report path may be a glob -- Scala and .NET bury the compiler version or
  # a run GUID in theirs. First match wins.
  found=$(compgen -G "$workdir/$report" | head -1 || true)
  if [[ -n "$found" ]]; then
    # Strip any leading dot: SimpleCov's report is `.last_run.json`, and
    # actions/upload-artifact silently drops hidden files.
    base=$(basename "$found")
    cp "$found" "$dest/${base#.}"
  else
    echo "!!! $name produced no coverage report at $workdir/$report"
  fi

  if [[ "$FAIL_FAST" == "1" && $status -ne 0 ]]; then
    break
  fi
done

if [[ ${#ran[@]} -eq 0 ]]; then
  echo "No units matched: ${SELECTED[*]}" >&2
  exit 2
fi

echo ""
"$REPO_ROOT/scripts/coverage/aggregate.py" \
  --coverage-dir "$COVERAGE_DIR" \
  --markdown "$COVERAGE_DIR/summary.md" \
  --json "$COVERAGE_DIR/summary.json"

if [[ ${#failed[@]} -gt 0 ]]; then
  echo ""
  echo "FAILED units: ${failed[*]}"
  exit 1
fi
