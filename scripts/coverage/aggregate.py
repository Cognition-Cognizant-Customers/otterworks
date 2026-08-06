#!/usr/bin/env python3
"""Parse every unit's coverage report into one comparable table.

Each build unit in this monorepo emits coverage in whatever format its language
ecosystem produces. This normalises the five shapes actually in use --
Go coverprofile, LCOV, Cobertura XML, JaCoCo XML and SimpleCov's `.last_run.json`
-- into `(covered_lines, total_lines)` so the numbers can be put side by side,
trended and ratcheted.

Input layout, written by scripts/coverage/run-coverage.sh:

    coverage/<unit>/format.txt   one of: goprofile lcov cobertura jacoco simplecov
    coverage/<unit>/status.txt   the unit's test-command exit code
    coverage/<unit>/<report>  the report itself

Output: a markdown table on stdout, plus optional --markdown / --json files.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

METADATA_FILES = {"format.txt", "status.txt"}


@dataclass
class UnitCoverage:
    unit: str
    status: int | None
    covered: int | None
    total: int | None
    fmt: str | None
    note: str = ""

    @property
    def percent(self) -> float | None:
        if not self.total:
            return None
        return round(100.0 * self.covered / self.total, 2)


def _parse_goprofile(path: Path) -> tuple[int, int]:
    """Go coverprofile: `file:startLine.col,endLine.col numStatements count`."""
    covered = total = 0
    for line in path.read_text().splitlines():
        if not line or line.startswith("mode:"):
            continue
        _, counts = line.rsplit(" ", 1)
        statements = int(line.rsplit(" ", 2)[1])
        total += statements
        if int(counts) > 0:
            covered += statements
    return covered, total


def _parse_lcov(path: Path) -> tuple[int, int]:
    """LCOV: LF = lines found, LH = lines hit, one pair per source file."""
    covered = total = 0
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("LF:"):
            total += int(line[3:])
        elif line.startswith("LH:"):
            covered += int(line[3:])
    return covered, total


def _parse_cobertura(path: Path) -> tuple[int, int]:
    """Cobertura: count <line> elements rather than trusting line-rate.

    `line-rate` on the root is a ratio, which loses the denominator we need for
    a meaningful aggregate, and coverlet/scoverage/pytest-cov round it
    differently.
    """
    root = ET.parse(path).getroot()
    covered = total = 0
    # Only the class-level <lines>: coverlet and scoverage repeat every line
    # inside <methods>/<method>/<lines>, so a descendant search counts each twice.
    for cls in root.iter("class"):
        for lines in cls.findall("lines"):
            for line in lines.findall("line"):
                if line.get("hits") is None:
                    continue
                total += 1
                if int(line.get("hits", "0")) > 0:
                    covered += 1
    if total == 0:  # fall back to the ratio if the report has no <line> detail
        rate = float(root.get("line-rate", 0) or 0)
        lines_valid = int(root.get("lines-valid", 0) or 0)
        return round(rate * lines_valid), lines_valid
    return covered, total


def _parse_jacoco(path: Path) -> tuple[int, int]:
    """JaCoCo XML: the report-level LINE counter."""
    root = ET.parse(path).getroot()
    for counter in root.findall("counter"):
        if counter.get("type") == "LINE":
            missed = int(counter.get("missed", "0"))
            covered = int(counter.get("covered", "0"))
            return covered, covered + missed
    return 0, 0


def _parse_simplecov(path: Path) -> tuple[int, int]:
    """SimpleCov `.last_run.json` only carries a percentage.

    It has no denominator, so scale against 10_000 to keep one basis point of
    precision when the numbers are re-derived downstream.
    """
    data = json.loads(path.read_text())
    percent = float(data.get("result", {}).get("line", data.get("result", {}).get("covered_percent", 0)))
    return round(percent * 100), 10_000


PARSERS = {
    "goprofile": _parse_goprofile,
    "lcov": _parse_lcov,
    "cobertura": _parse_cobertura,
    "jacoco": _parse_jacoco,
    "simplecov": _parse_simplecov,
}


def collect(coverage_dir: Path) -> list[UnitCoverage]:
    results: list[UnitCoverage] = []
    for unit_dir in sorted(p for p in coverage_dir.iterdir() if p.is_dir()):
        fmt_file = unit_dir / "format.txt"
        status_file = unit_dir / "status.txt"
        fmt = fmt_file.read_text().strip() if fmt_file.exists() else None
        status = int(status_file.read_text().strip()) if status_file.exists() else None

        reports = [p for p in unit_dir.iterdir() if p.name not in METADATA_FILES and p.is_file()]
        if not reports:
            results.append(UnitCoverage(unit_dir.name, status, None, None, fmt, "no report produced"))
            continue
        if fmt not in PARSERS:
            results.append(UnitCoverage(unit_dir.name, status, None, None, fmt, f"unknown format {fmt!r}"))
            continue

        try:
            covered, total = PARSERS[fmt](reports[0])
        except Exception as exc:  # noqa: BLE001 -- a malformed report must not hide the other units
            results.append(UnitCoverage(unit_dir.name, status, None, None, fmt, f"unparseable: {exc}"))
            continue
        results.append(UnitCoverage(unit_dir.name, status, covered, total, fmt))
    return results


def render_markdown(results: list[UnitCoverage]) -> str:
    lines = [
        "| Unit | Tests | Line coverage | Covered / total | Note |",
        "|---|:--:|---:|---:|---|",
    ]
    agg_covered = agg_total = 0
    for r in results:
        if r.status is None:
            tests = "—"
        elif r.status == 0:
            tests = "pass"
        else:
            tests = f"**FAIL ({r.status})**"
        pct = "—" if r.percent is None else f"{r.percent:.2f}%"
        if r.total and r.fmt != "simplecov":
            agg_covered += r.covered or 0
            agg_total += r.total
        counts = "—" if r.total is None else (
            "n/a (percentage only)" if r.fmt == "simplecov" else f"{r.covered} / {r.total}"
        )
        lines.append(f"| `{r.unit}` | {tests} | {pct} | {counts} | {r.note} |")

    if agg_total:
        overall = round(100.0 * agg_covered / agg_total, 2)
        lines.append(f"| **aggregate** | | **{overall:.2f}%** | **{agg_covered} / {agg_total}** | excludes percentage-only units |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--coverage-dir", type=Path, default=Path("coverage"))
    ap.add_argument("--markdown", type=Path)
    ap.add_argument("--json", dest="json_out", type=Path)
    args = ap.parse_args()

    if not args.coverage_dir.is_dir():
        print(f"no coverage directory at {args.coverage_dir}", file=sys.stderr)
        return 2

    results = collect(args.coverage_dir)
    table = render_markdown(results)
    print("\n## Coverage by build unit\n")
    print(table)
    print()

    if args.markdown:
        args.markdown.write_text("## Coverage by build unit\n\n" + table + "\n")
    if args.json_out:
        payload = {r.unit: {**asdict(r), "percent": r.percent} for r in results}
        args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
