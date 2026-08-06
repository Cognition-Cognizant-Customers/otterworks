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

known=()
for record in "${COVERAGE_UNITS[@]}"; do known+=("${record%%|*}"); done

# Reject an unknown name up front rather than quietly measuring the subset that
# did match: `run-coverage.sh api-gatway client-app` would otherwise exit 0
# having never run api-gateway, and summary.json would look like a clean result.
unknown=()
for want in "${SELECTED[@]}"; do
  [[ " ${known[*]} " == *" $want "* ]] || unknown+=("$want")
done
if [[ ${#unknown[@]} -gt 0 ]]; then
  echo "Unknown unit(s): ${unknown[*]}" >&2
  echo "Known units: ${known[*]}" >&2
  exit 2
fi

# Past the point of no return: nothing downstream may read the previous run's
# summary, whether or not this one gets as far as writing a new one.
rm -f "$COVERAGE_DIR/summary.json" "$COVERAGE_DIR/summary.md"

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

  # Delete last run's reports before this one starts. A command that dies before
  # writing a report (compile error, absent toolchain) would otherwise leave the
  # previous file in place, and it would be collected as though it were current.
  for glob in "$report" "${SONAR_EXTRA_REPORTS[$name]:-}"; do
    [[ -n "$glob" ]] || continue
    while IFS= read -r stale; do rm -f "$stale"; done < <(compgen -G "$workdir/$glob" || true)
  done

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

  # Sonar-only formats live in a subdirectory: aggregate.py parses whatever
  # file sits at the top level of the unit's directory, and there must be one.
  extra_glob="${SONAR_EXTRA_REPORTS[$name]:-}"
  if [[ -n "$extra_glob" ]]; then
    extra=$(compgen -G "$workdir/$extra_glob" | head -1 || true)
    if [[ -n "$extra" ]]; then
      mkdir -p "$dest/sonar"
      cp "$extra" "$dest/sonar/"
    fi
  fi

  if [[ "$FAIL_FAST" == "1" && $status -ne 0 ]]; then
    break
  fi
done

echo ""
# Only the units this invocation ran. coverage/ still holds directories from
# earlier runs, and summarising those would let `make coverage-baseline-update`
# after a one-unit run re-freeze every other unit's months-old number.
if ! "$REPO_ROOT/scripts/coverage/aggregate.py" \
  --coverage-dir "$COVERAGE_DIR" \
  --markdown "$COVERAGE_DIR/summary.md" \
  --json "$COVERAGE_DIR/summary.json" \
  --units "${ran[@]}"; then
  echo "!!! aggregation failed; there is no $COVERAGE_DIR/summary.json to ratchet against" >&2
  exit 2
fi

if [[ ${#failed[@]} -gt 0 ]]; then
  echo ""
  echo "FAILED units: ${failed[*]}"
  exit 1
fi
