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


def parse_simplecov(path: Path) -> tuple[int, int]:
    """simplecov's .last_run.json carries a percentage, not line counts."""
    data = json.loads(path.read_text())
    percent = data.get("result", {}).get("line")
    if percent is None:
        return 0, 0
    # Scale to a 10,000-line pseudo-total so the percentage survives rounding.
    return round(float(percent) * 100), 10_000


PARSERS: list[tuple[str, callable]] = [
    ("coverage.out", parse_go_profile),
    ("jacocoTestReport.xml", parse_jacoco),
    ("coverage.cobertura.xml", parse_cobertura),
    ("coverage.xml", parse_cobertura),
    ("lcov.info", parse_lcov),
    (".last_run.json", parse_simplecov),
]


def collect(unit_dir: Path) -> UnitCoverage:
    result = UnitCoverage(unit=unit_dir.name)

    status_file = unit_dir / "status.txt"
    if status_file.is_file():
        try:
            result.status = int(status_file.read_text().strip())
        except ValueError:
            result.status = None

    for filename, parser in PARSERS:
        for path in sorted(unit_dir.rglob(filename)):
            try:
                covered, total = parser(path)
            except (ET.ParseError, ValueError, OSError) as exc:
                print(f"warning: cannot parse {path}: {exc}", file=sys.stderr)
                continue
            if total:
                result.covered += covered
                result.total += total
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
        return f"{unit.percent:.1f}%"
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
        lines = f"{unit.covered:,} / {unit.total:,}" if unit.measured else "—"
        cells = [unit.unit, coverage_label(unit), lines, status_label(unit)]
        if baseline is not None:
            previous = baseline.get(unit.unit)
            if previous is None or not unit.measured:
                delta = "—"
            else:
                delta = f"{unit.percent - previous:+.1f} pp"
            cells.insert(2, delta)
        rows.append("| " + " | ".join(cells) + " |")

    measured = [u for u in units if u.measured]
    if measured:
        covered = sum(u.covered for u in measured)
        total = sum(u.total for u in measured)
        rows.append(
            f"| **Total ({len(measured)} instrumented units)** | "
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
    if args.baseline and args.baseline.is_file():
        baseline = {
            name: entry["percent"]
            for name, entry in json.loads(args.baseline.read_text())["units"].items()
            if entry.get("percent") is not None
        }

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
            and u.percent < baseline[u.unit] - args.tolerance
        ]
        if drops:
            for unit, was, now in drops:
                print(f"coverage regression: {unit} {was:.1f}% -> {now:.1f}%", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
