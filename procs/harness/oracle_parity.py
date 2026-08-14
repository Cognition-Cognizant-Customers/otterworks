"""Grades Oracle-vs-Postgres parity for the legacy billing estates.

Compares two transcript sets produced by the same declarative scenario set:
the Postgres recordings (procs/harness/record.py) and the Oracle recordings
(procs/harness/oracle_record.py). Every scenario is graded — this loop has no
extracted/pending distinction because both sides are legacy estates that are
contractually semantically equivalent.

Writes procs/reports/oracle-parity.{md,json} with pass/fail per scenario,
a per-entrypoint rollup, and field/probe-level value diffs on failure.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_TRANSCRIPTS = ROOT / "procs" / "transcripts"
REPORT_DIR = ROOT / "procs" / "reports"

FAIL = 1
SELECTION_EMPTY = 9


def load_transcripts(root: Path) -> dict[str, dict[str, Any]]:
    return {
        payload["scenario"]: payload
        for payload in (json.loads(path.read_text()) for path in sorted(root.glob("*/*.json")))
    }


def diff_section(
    kind: str, expected: dict[str, Any], actual: dict[str, Any]
) -> list[dict[str, Any]]:
    failures = []
    for name in sorted(set(expected) | set(actual)):
        postgres_value = expected.get(name, "<missing>")
        oracle_value = actual.get(name, "<missing>")
        if postgres_value != oracle_value:
            failures.append(
                {"kind": kind, "name": name, "postgres": postgres_value, "oracle": oracle_value}
            )
    return failures


def golden_drift(postgres: dict[str, dict[str, Any]]) -> list[str]:
    if not GOLDEN_TRANSCRIPTS.exists():
        return []
    golden = load_transcripts(GOLDEN_TRANSCRIPTS)
    drift = []
    for scenario, payload in sorted(postgres.items()):
        reference = golden.get(scenario)
        if reference is None:
            continue
        for section in ("business_fields", "probes"):
            if payload.get(section) != reference.get(section):
                drift.append(
                    f"{scenario}: fresh postgres recording diverges from the golden "
                    f"transcript in {section} (procs/transcripts/)"
                )
    return drift


def write_report(results: list[dict[str, Any]], drift: list[str], namespace: str | None) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    by_entrypoint: dict[str, dict[str, int]] = {}
    for item in results:
        rollup = by_entrypoint.setdefault(item["entrypoint"], {"PASS": 0, "FAIL": 0})
        rollup[item["status"]] += 1
    report = {
        "namespace": namespace,
        "results": results,
        "entrypoints": by_entrypoint,
        "golden_drift": drift,
    }
    (REPORT_DIR / "oracle-parity.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    lines = ["# Oracle billing parity report", ""]
    if namespace:
        lines.extend([f"Namespace: `{namespace}`", ""])
    lines.append("## Entrypoints")
    lines.append("")
    lines.append("| Entrypoint | PASS | FAIL |")
    lines.append("| --- | ---: | ---: |")
    for entrypoint in sorted(by_entrypoint):
        rollup = by_entrypoint[entrypoint]
        lines.append(f"| `{entrypoint}` | {rollup['PASS']} | {rollup['FAIL']} |")
    lines.extend(["", "## Scenarios", ""])
    for item in results:
        lines.append(f"- **{item['status']}** `{item['module']}/{item['scenario']}`")
        for failure in item.get("failures", []):
            lines.append(
                f"  - {failure['kind']} `{failure['name']}`: postgres `{failure['postgres']}`, "
                f"oracle `{failure['oracle']}`"
            )
    lines.extend([f"- Golden drift: {item}" for item in drift])
    (REPORT_DIR / "oracle-parity.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--postgres-dir", type=Path, required=True)
    parser.add_argument("--oracle-dir", type=Path, required=True)
    parser.add_argument("--namespace")
    args = parser.parse_args()
    postgres = load_transcripts(args.postgres_dir)
    oracle = load_transcripts(args.oracle_dir)
    if not postgres or not oracle:
        print("no transcripts found in one or both transcript directories", file=sys.stderr)
        return SELECTION_EMPTY
    results = []
    for scenario in sorted(set(postgres) | set(oracle)):
        pg_payload = postgres.get(scenario)
        ora_payload = oracle.get(scenario)
        if pg_payload is None or ora_payload is None:
            present = ora_payload or pg_payload
            results.append(
                {
                    "scenario": scenario,
                    "module": present["module"],
                    "entrypoint": present["entrypoint"],
                    "status": "FAIL",
                    "failures": [
                        {
                            "kind": "transcript",
                            "name": scenario,
                            "postgres": "<missing>" if pg_payload is None else "recorded",
                            "oracle": "<missing>" if ora_payload is None else "recorded",
                        }
                    ],
                }
            )
            continue
        failures = diff_section(
            "field", pg_payload["business_fields"], ora_payload["business_fields"]
        )
        failures.extend(diff_section("probe", pg_payload.get("probes", {}), ora_payload.get("probes", {})))
        results.append(
            {
                "scenario": scenario,
                "module": pg_payload["module"],
                "entrypoint": pg_payload["entrypoint"],
                "status": "PASS" if not failures else "FAIL",
                "failures": failures,
            }
        )
    drift = golden_drift(postgres)
    write_report(results, drift, args.namespace)
    for warning in drift:
        print(f"WARNING: {warning}", file=sys.stderr)
    passed = sum(item["status"] == "PASS" for item in results)
    failed = sum(item["status"] == "FAIL" for item in results)
    print(f"Oracle parity PASS={passed} FAIL={failed}")
    return FAIL if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
