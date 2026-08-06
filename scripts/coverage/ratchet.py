#!/usr/bin/env python3
"""Coverage ratchet: coverage may not go down.

There is deliberately no absolute target here. A hard floor on a repo whose
worst unit sits at 3.4 cases/KLOC would either be set so low it means nothing or
so high it blocks every PR. A ratchet is enforceable from day one: whatever a
unit measures today becomes the number it may not fall below.

A unit missing from the baseline is new -- it is recorded, not failed. A unit
missing from the summary was not run in this job (the CI jobs are path-filtered)
and is ignored.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Coverage instrumentation is not bit-for-bit reproducible across toolchain
# patch versions, so a sub-point wobble must not turn a PR red.
DEFAULT_TOLERANCE = 0.5


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--summary", type=Path, default=Path("coverage/summary.json"))
    ap.add_argument("--baseline", type=Path, default=Path("coverage-baseline.json"))
    # Left as None so an explicit flag can be told apart from the baseline's own
    # recorded tolerance, which otherwise wins over the module default.
    ap.add_argument("--tolerance", type=float, default=None)
    ap.add_argument("--update", action="store_true", help="write the current numbers as the new baseline")
    args = ap.parse_args()

    if not args.summary.exists():
        print(f"no coverage summary at {args.summary}", file=sys.stderr)
        return 2

    summary = json.loads(args.summary.read_text())
    current = {unit: data["percent"] for unit, data in summary.items() if data.get("percent") is not None}

    recorded: dict = {}
    if args.baseline.exists():
        recorded = json.loads(args.baseline.read_text())
    baseline: dict[str, float] = recorded.get("units", {})
    tolerance = args.tolerance if args.tolerance is not None else float(recorded.get("tolerance", DEFAULT_TOLERANCE))

    if args.update:
        merged = {**baseline, **current}
        args.baseline.write_text(
            json.dumps({"tolerance": tolerance, "units": dict(sorted(merged.items()))}, indent=2) + "\n"
        )
        print(f"baseline updated with {len(current)} unit(s) -> {args.baseline}")
        return 0

    regressions, improvements, new = [], [], []
    for unit, pct in sorted(current.items()):
        if unit not in baseline:
            new.append((unit, pct))
        elif pct < baseline[unit] - tolerance:
            regressions.append((unit, baseline[unit], pct))
        elif pct > baseline[unit] + tolerance:
            improvements.append((unit, baseline[unit], pct))

    for unit, pct in new:
        print(f"NEW        {unit}: {pct:.2f}% (not in baseline; run `make coverage-baseline-update` to record it)")
    for unit, was, now in improvements:
        print(f"IMPROVED   {unit}: {was:.2f}% -> {now:.2f}%")
    for unit, was, now in regressions:
        print(f"REGRESSED  {unit}: {was:.2f}% -> {now:.2f}% (tolerance {tolerance}pp)")

    if regressions:
        print(f"\n{len(regressions)} unit(s) lost coverage. Add tests, or justify and run "
              f"`make coverage-baseline-update` in the same PR.")
        return 1
    print("\nCoverage ratchet OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
