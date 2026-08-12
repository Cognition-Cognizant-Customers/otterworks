# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "tabulate", "pyyaml"]
# ///
"""
OtterWorks incident reproduction and verification harness.

Reproduces the seeded chaos scenarios as the user experiences them: every
probe drives the symptom endpoint through the API gateway of a *running*
deployment and reaches a verdict from the response alone — never by reading
the chaos flag.

This is the verification loop: a probe that reports FAIL reproduces the
incident, and the same probe reporting PASS after the flag is cleared (or the
bug is fixed) is the proof the incident is gone. PASS also requires that a
legitimate request on the same path succeeds, so a fix that refuses everybody
cannot pass. A backend that is down is INCONCLUSIVE, never a pass.

Usage:
    uv run incidents/harness/incident_check.py list
    uv run incidents/harness/incident_check.py inject --scenario search-service:suggest_500
    uv run incidents/harness/incident_check.py probe  --scenario search-service:suggest_500
    uv run incidents/harness/incident_check.py verify --scenario search-service:suggest_500
    uv run incidents/harness/incident_check.py reset

Environment overrides:
    OTTERWORKS_INCIDENT_TARGET      default target base URL
    CHAOS_SECRET                    sent as X-Chaos-Secret on inject/reset
    INCIDENT_LATENCY_THRESHOLD_MS   latency scenario threshold (default 2500)
    INCIDENT_NOTIFY_TIMEOUT         notification delivery window in seconds

Exit codes:
    0 = every selected scenario reported PASS (probe: the symptom is absent)
    1 = at least one selected scenario reported FAIL (the incident is live)
    2 = target unreachable / configuration error
    3 = no FAIL, but at least one scenario was INCONCLUSIVE — nothing proven
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probes import REGISTRY, IncidentContext, Result, SetupError, Status

INCIDENTS_DIR = Path(__file__).resolve().parents[1]
SCENARIO_SPEC = INCIDENTS_DIR / "scenarios.yaml"
DEFAULT_REPORT_DIR = INCIDENTS_DIR / "reports"
DEFAULT_TARGET = os.getenv("OTTERWORKS_INCIDENT_TARGET", "http://localhost:8080")


# ── scenario spec ─────────────────────────────────────────────────────────────


def load_spec() -> dict[str, dict[str, Any]]:
    """The scenario spec, keyed by id, cross-checked against the registry.

    The spec file and the registered probes describe the same four scenarios;
    a drift between them means the report would name a runbook or endpoint the
    probe does not actually drive.
    """
    data = yaml.safe_load(SCENARIO_SPEC.read_text())
    spec = {entry["id"]: entry for entry in data.get("scenarios", [])}
    if set(spec) != set(REGISTRY):
        missing = set(spec) ^ set(REGISTRY)
        raise SystemExit(
            f"scenario spec and probe registry disagree on: {', '.join(sorted(missing))}"
        )
    return spec


# ── reporting ─────────────────────────────────────────────────────────────────


def to_report(results: list[Result], target: str, mode: str) -> dict[str, Any]:
    return {
        "target": target,
        "mode": mode,
        "run_at": datetime.now(UTC).isoformat(),
        "summary": {
            "scenarios": len(results),
            "pass": sum(1 for r in results if r.status is Status.PASS),
            "fail": sum(1 for r in results if r.status is Status.FAIL),
            "inconclusive": sum(1 for r in results if r.status is Status.INCONCLUSIVE),
        },
        "results": [r.to_dict() for r in results],
    }


def to_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# OtterWorks incident report",
        "",
        f"- Target: `{report['target']}`",
        f"- Mode: {report['mode']}",
        f"- Run: {report['run_at']}",
        f"- Scenarios: {summary['scenarios']} | pass: {summary['pass']} | "
        f"fail: {summary['fail']} | inconclusive: {summary['inconclusive']}",
        "",
        "| Status | Scenario | Service | Endpoint | Runbook |",
        "|---|---|---|---|---|",
    ]
    for result in report["results"]:
        lines.append(
            f"| {result['status']} | `{result['scenario_id']}` | {result['service']} | "
            f"`{result['endpoint']}` | {result['runbook']} |"
        )
    for result in report["results"]:
        lines += [
            "",
            f"## `{result['scenario_id']}` — {result['status']}",
            "",
            f"- Symptom: {result['symptom']}",
            f"- Detail: {result['detail']}",
        ]
        if result["measured_ms"] is not None:
            lines.append(
                f"- Measured: {result['measured_ms']:.0f}ms "
                f"(threshold {result['threshold_ms']:.0f}ms)"
            )
        if result["control_ok"] is not None:
            lines.append(f"- Legitimate request succeeded: {result['control_ok']}")
        for evidence in result["evidence"]:
            excerpt = (evidence["response_excerpt"] or "").strip().replace("\n", " ")[:300]
            lines += [
                "",
                "```http",
                evidence["request"],
                f"-> {evidence['response_status']} {excerpt}",
                "```",
            ]
            if evidence["note"]:
                lines.append(evidence["note"])
    return "\n".join(lines) + "\n"


def write_reports(report_dir: Path, results: list[Result], target: str, mode: str) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    report = to_report(results, target, mode)
    (report_dir / "incident-report.json").write_text(json.dumps(report, indent=2) + "\n")
    (report_dir / "incident-report.md").write_text(to_markdown(report))
    print(f"\nReports written to {report_dir}/incident-report.{{json,md}}")


def print_table(results: list[Result]) -> None:
    rows = [
        [r.status.value, r.scenario_id, r.service, r.detail[:70]]
        for r in sorted(results, key=lambda r: r.scenario_id)
    ]
    print(tabulate(rows, headers=["", "scenario", "service", "detail"], tablefmt="simple"))


# ── main ──────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reproduce and verify seeded incidents.")
    parser.add_argument(
        "command",
        choices=["list", "inject", "probe", "verify", "reset"],
        help="list scenarios, inject/reset chaos, or probe/verify the symptom",
    )
    parser.add_argument("--target", default=DEFAULT_TARGET, help="API gateway base URL")
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        metavar="SCENARIO_ID",
        help="restrict to these scenario ids (repeatable); default is all four",
    )
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args(argv)


def select(spec: dict[str, dict[str, Any]], requested: list[str]) -> list[str]:
    unknown = [s for s in requested if s not in spec]
    if unknown:
        raise SystemExit(f"unknown scenario id(s): {', '.join(unknown)}")
    return requested or sorted(spec)


def run_checks(
    ctx: IncidentContext, scenario_ids: list[str], results: list[Result]
) -> None:
    for scenario_id in scenario_ids:
        entry = REGISTRY[scenario_id]
        try:
            results.append(entry.run(ctx))
        except Exception as exc:  # a broken probe must not mask the rest of the suite
            results.append(
                entry.result(
                    Status.INCONCLUSIVE,
                    f"probe raised {type(exc).__name__}: {exc}",
                )
            )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    spec = load_spec()

    if args.command == "list":
        rows = [
            [sid, spec[sid]["service"], spec[sid]["endpoint"], spec[sid]["runbook"]]
            for sid in sorted(spec)
        ]
        print(tabulate(rows, headers=["scenario", "service", "endpoint", "runbook"]))
        return 0

    scenario_ids = select(spec, args.scenario)
    target = args.target.rstrip("/")
    results: list[Result] = []

    with httpx.Client(
        base_url=target,
        timeout=httpx.Timeout(args.timeout, connect=5.0),
        follow_redirects=False,
    ) as client:
        ctx = IncidentContext(base_url=target, client=client)
        try:
            ctx.wait_for_target()
            ctx.seed_identities()
        except SetupError as exc:
            print(f"incident harness setup failed: {exc}", file=sys.stderr)
            if args.command in ("probe", "verify"):
                # The report has to exist on every exit path: an unreachable
                # target proves nothing about any scenario.
                for scenario_id in scenario_ids:
                    results.append(
                        REGISTRY[scenario_id].result(
                            Status.INCONCLUSIVE, f"target setup failed: {exc}"
                        )
                    )
                write_reports(args.report_dir, results, target, args.command)
            return 2

        if args.command == "inject":
            for scenario_id in scenario_ids:
                response = ctx.inject(scenario_id)
                if response.status_code != 200:
                    print(
                        f"inject failed for {scenario_id}: "
                        f"{response.status_code} {response.text[:200]}",
                        file=sys.stderr,
                    )
                    return 2
                body = response.json()
                print(
                    f"injected {scenario_id}: flag {body.get('key')} "
                    f"expires in {body.get('expires_in')}s"
                )
            return 0

        if args.command == "reset":
            response = ctx.reset()
            if response.status_code != 200:
                print(
                    f"reset failed: {response.status_code} {response.text[:200]}",
                    file=sys.stderr,
                )
                return 2
            cleared = response.json().get("cleared", [])
            print(f"cleared {len(cleared)} chaos flag(s): {', '.join(cleared) or '<none>'}")
            return 0

        # probe / verify: same checks, same report — verify is the remediation
        # proof, probe is the reproduction. Either way the report is written on
        # every exit path.
        try:
            run_checks(ctx, scenario_ids, results)
        finally:
            checked = {r.scenario_id for r in results}
            for scenario_id in scenario_ids:
                if scenario_id not in checked:
                    results.append(
                        REGISTRY[scenario_id].result(
                            Status.INCONCLUSIVE, "the run ended before this scenario was checked"
                        )
                    )
            print_table(results)
            write_reports(args.report_dir, results, target, args.command)

    failed = [r for r in results if r.status is Status.FAIL]
    if failed:
        print(f"\nIncident gate FAILED: {len(failed)} scenario(s) reproduce:")
        for result in failed:
            print(f"  - {result.scenario_id}: {result.detail}")
        return 1

    inconclusive = [r for r in results if r.status is Status.INCONCLUSIVE]
    if inconclusive:
        print(f"\nIncident gate UNPROVEN: {len(inconclusive)} scenario(s) reached no verdict:")
        for result in inconclusive:
            print(f"  - {result.scenario_id}: {result.detail}")
        return 3

    print("\nIncident gate PASSED: every selected scenario is symptom-free and serving.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
