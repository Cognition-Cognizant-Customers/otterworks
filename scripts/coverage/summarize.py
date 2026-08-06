#!/usr/bin/env python3
"""Aggregate per-unit coverage reports into one table.

Reads every ``coverage-reports/<unit>/`` directory produced by
``scripts/coverage/run-unit.sh``, normalises whatever format that unit's
toolchain emits (Go coverprofile, Cobertura XML, JaCoCo XML, LCOV, simplecov
``.last_run.json``) into line coverage, and writes:

  coverage-reports/summary.md    -- markdown table (PR comment / job summary)
  coverage-reports/summary.json  -- machine-readable, usable as a ratchet baseline

With ``--baseline <file>`` the table gains a delta column and, with
``--fail-on-drop``, the command exits non-zero if any unit's coverage fell.

Usage:
    scripts/coverage/summarize.py [--dir coverage-reports] [--baseline f.json]
                                  [--fail-on-drop] [--tolerance 0.0]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# Units that have no coverage instrumentation yet, and the work package that
# owns wiring it up (docs/TEST-COVERAGE-EXPANSION-SOW.md).
PENDING_INSTRUMENTATION = {
    "analytics-service": "WP-12 (scoverage)",
    "report-service": "WP-12 (jacoco on the Java 8 pom)",
    "legacy-portal": "WP-12 (jacoco)",
}


@dataclass
class UnitCoverage:
    unit: str
    covered: int = 0
    total: int = 0
    sources: list[str] = field(default_factory=list)
    status: int | None = None
    # True when `total` is a synthetic denominator (a report that carries only a
    # percentage). Such a unit keeps its percentage but is kept out of the
    # aggregate row, where fake line counts would distort the weighting.
    synthetic: bool = False

    @property
    def measured(self) -> bool:
        return self.total > 0

    @property
    def percent(self) -> float:
        return 100.0 * self.covered / self.total if self.total else 0.0


def parse_go_profile(path: Path) -> tuple[int, int]:
    """Sum statement counts from a `go test -coverprofile` file."""
    covered = total = 0
    for line in path.read_text().splitlines()[1:]:  # skip `mode:` header
        match = re.match(r".+:\d+\.\d+,\d+\.\d+ (\d+) (\d+)$", line)
        if not match:
            continue
        statements, count = int(match.group(1)), int(match.group(2))
        total += statements
        if count > 0:
            covered += statements
    return covered, total


def parse_cobertura(path: Path) -> tuple[int, int]:
    root = ET.parse(path).getroot()
    valid = root.get("lines-valid")
    covered = root.get("lines-covered")
    if valid is not None and covered is not None:
        return int(covered), int(valid)
    # Cobertura from coverlet omits the totals; count the lines instead.
    hit = seen = 0
    for line in root.iter("line"):
        seen += 1
        if int(line.get("hits", "0")) > 0:
            hit += 1
    return hit, seen


def parse_jacoco(path: Path) -> tuple[int, int]:
    root = ET.parse(path).getroot()
    for counter in root.findall("counter"):
        if counter.get("type") == "LINE":
            missed = int(counter.get("missed", "0"))
            covered = int(counter.get("covered", "0"))
            return covered, missed + covered
    return 0, 0


def parse_lcov(path: Path) -> tuple[int, int]:
    covered = total = 0
    for line in path.read_text().splitlines():
        if line.startswith("LF:"):
            total += int(line[3:])
        elif line.startswith("LH:"):
            covered += int(line[3:])
    return covered, total


def parse_simplecov_resultset(path: Path) -> tuple[int, int]:
    """Real line counts from simplecov's .resultset.json.

    Each file maps to a per-line hit array; `null` means the line is not
    relevant (blank, comment, `end`). Both the modern `{"lines": [...]}` and the
    legacy bare-array shapes appear in the wild.
    """
    covered = total = 0
    for suite in json.loads(path.read_text()).values():
        for hits in suite.get("coverage", {}).values():
            if isinstance(hits, dict):
                hits = hits.get("lines", [])
            for hit in hits:
                if hit is None:
                    continue
                total += 1
                if hit > 0:
                    covered += 1
    return covered, total


def parse_simplecov_last_run(path: Path) -> tuple[int, int]:
    """Fallback: .last_run.json carries a percentage and no line counts."""
    data = json.loads(path.read_text())
    percent = data.get("result", {}).get("line")
    if percent is None:
        return 0, 0
    # Synthetic denominator so the percentage survives; `synthetic` keeps these
    # counts out of the aggregate row.
    return round(float(percent) * 100), 10_000


# filename, parser, synthetic-denominator. Order is priority order.
PARSERS: list[tuple[str, callable, bool]] = [
    ("coverage.out", parse_go_profile, False),
    ("jacocoTestReport.xml", parse_jacoco, False),
    ("coverage.cobertura.xml", parse_cobertura, False),
    ("coverage.xml", parse_cobertura, False),
    ("lcov.info", parse_lcov, False),
    (".resultset.json", parse_simplecov_resultset, False),
    (".last_run.json", parse_simplecov_last_run, True),
]


def collect(unit_dir: Path) -> UnitCoverage:
    result = UnitCoverage(unit=unit_dir.name)

    status_file = unit_dir / "status.txt"
    if status_file.is_file():
        try:
            result.status = int(status_file.read_text().strip())
        except ValueError:
            result.status = None

    for filename, parser, synthetic in PARSERS:
        for path in sorted(unit_dir.rglob(filename)):
            try:
                covered, total = parser(path)
            except (ET.ParseError, ValueError, OSError, AttributeError) as exc:
                print(f"warning: cannot parse {path}: {exc}", file=sys.stderr)
                continue
            if total:
                result.covered += covered
                result.total += total
                result.synthetic = synthetic
                result.sources.append(str(path.relative_to(unit_dir)))
        if result.measured:
            break  # first format that yields numbers wins; don't double-count
    return result


def status_label(unit: UnitCoverage) -> str:
    if unit.status is None:
        return "not run"
    return "pass" if unit.status == 0 else f"FAIL (exit {unit.status})"


def coverage_label(unit: UnitCoverage) -> str:
    if unit.measured:
        suffix = " (percentage only)" if unit.synthetic else ""
        return f"{unit.percent:.1f}%{suffix}"
    pending = PENDING_INSTRUMENTATION.get(unit.unit)
    return f"not instrumented — {pending}" if pending else "no report produced"


def render(units: list[UnitCoverage], baseline: dict[str, float] | None) -> str:
    header = "| Build unit | Line coverage | Lines covered | Tests |"
    divider = "|---|---:|---:|:--:|"
    if baseline is not None:
        header = "| Build unit | Line coverage | Delta | Lines covered | Tests |"
        divider = "|---|---:|---:|---:|:--:|"

    rows = [header, divider]
    for unit in units:
        lines = (
            f"{unit.covered:,} / {unit.total:,}"
            if unit.measured and not unit.synthetic
            else "—"
        )
        cells = [unit.unit, coverage_label(unit), lines, status_label(unit)]
        if baseline is not None:
            previous = baseline.get(unit.unit)
            if previous is None or not unit.measured:
                delta = "—"
            else:
                delta = f"{round(unit.percent, 2) - previous:+.1f} pp"
            cells.insert(2, delta)
        rows.append("| " + " | ".join(cells) + " |")

    measured = [u for u in units if u.measured and not u.synthetic]
    if measured:
        covered = sum(u.covered for u in measured)
        total = sum(u.total for u in measured)
        rows.append(
            f"| **Total ({len(measured)} instrumented "
            f"unit{'' if len(measured) == 1 else 's'})** | "
            f"**{100.0 * covered / total:.1f}%** | "
            + ("— | " if baseline is not None else "")
            + f"**{covered:,} / {total:,}** | |"
        )
    return "\n".join(rows) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="coverage-reports", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--fail-on-drop", action="store_true")
    parser.add_argument("--tolerance", type=float, default=0.0,
                        help="percentage points a unit may drop before failing")
    args = parser.parse_args()

    if not args.dir.is_dir():
        print(f"no coverage directory at {args.dir}", file=sys.stderr)
        return 1

    units = [collect(d) for d in sorted(args.dir.iterdir()) if d.is_dir()]
    if not units:
        print(f"no per-unit reports under {args.dir}", file=sys.stderr)
        return 1

    baseline: dict[str, float] | None = None
    if args.baseline:
        if args.baseline.is_file():
            baseline = {
                name: entry["percent"]
                for name, entry in json.loads(args.baseline.read_text())["units"].items()
                if entry.get("percent") is not None
            }
        else:
            # Never let a missing baseline turn the ratchet into a silent no-op.
            print(f"warning: no baseline at {args.baseline}; the ratchet is NOT enforced "
                  f"on this run. Seed it with `cp {args.dir}/summary.json "
                  f"{args.baseline}`.", file=sys.stderr)

    table = render(units, baseline)
    (args.dir / "summary.md").write_text(table)
    (args.dir / "summary.json").write_text(
        json.dumps(
            {
                "units": {
                    u.unit: {
                        "percent": round(u.percent, 2) if u.measured else None,
                        "covered": u.covered,
                        "total": u.total,
                        "status": u.status,
                        "reports": u.sources,
                    }
                    for u in units
                }
            },
            indent=2,
        )
        + "\n"
    )
    print(table)

    if args.fail_on_drop and baseline:
        drops = [
            (u.unit, baseline[u.unit], u.percent)
            for u in units
            if u.measured and u.unit in baseline
            # Compare at the precision the baseline was written with, so an
            # unchanged run can never read as a drop.
            and round(u.percent, 2) < baseline[u.unit] - args.tolerance
        ]
        if drops:
            for unit, was, now in drops:
                print(f"coverage regression: {unit} {was:.2f}% -> {now:.2f}%", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
