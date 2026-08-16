#!/usr/bin/env bash
# Deterministic run wrapper for legacy-estate jobs and golden-baseline recording.
#
# Byte-identical parity claims are only meaningful if the environment that
# produced the golden output is reproducible. This wrapper pins the three
# ambient inputs the legacy chain actually depends on:
#
#   TZ=UTC        date stamps in filenames, DD-MON-YY fields, report headers
#   LC_ALL=C      sort order, awk/sed collation, printf decimal separators
#   clock         (optional) frozen via libfaketime when TP_FAKETIME is set,
#                 e.g. TP_FAKETIME='2026-01-15 00:00:00'
#
# Usage:
#   scripts/tp-run-deterministic.sh <command> [args...]
#   TP_FAKETIME='2026-01-15 00:00:00' scripts/tp-run-deterministic.sh make legacy-etl-run JOB=run_all
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <command> [args...]" >&2
  exit 2
fi

export TZ=UTC
export LC_ALL=C
export LANG=C

if [ -n "${TP_FAKETIME:-}" ]; then
  libfaketime=""
  for candidate in \
    /usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1 \
    /usr/lib/aarch64-linux-gnu/faketime/libfaketime.so.1 \
    /usr/local/lib/faketime/libfaketime.so.1; do
    if [ -f "$candidate" ]; then
      libfaketime="$candidate"
      break
    fi
  done
  if [ -z "$libfaketime" ]; then
    echo "TP_FAKETIME is set but libfaketime is not installed (apt-get install -y faketime)" >&2
    exit 3
  fi
  export LD_PRELOAD="$libfaketime${LD_PRELOAD:+:$LD_PRELOAD}"
  export FAKETIME="@${TP_FAKETIME}"
  export FAKETIME_DONT_FAKE_MONOTONIC=1
fi

exec "$@"
