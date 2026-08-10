#!/usr/bin/env python3
"""Edge-case mutant harness.

Plants each cataloged bug (mutant) into a service module, runs that service's
test suite, and restores the module. A mutant is KILLED when at least one test
fails while the bug is in place, and SURVIVED when the suite stays green —
i.e., the suite has no positive/negative edge-case coverage for that class of
input.

Usage:
    edgecase_mutants.py --list
    edgecase_mutants.py --verify [--mutant EDGE-...] [--service NAME]
"""

import argparse
import signal
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG = Path(__file__).resolve().parent / "mutants.yaml"


def load_catalog() -> dict:
    with open(CATALOG) as f:
        return yaml.safe_load(f)


def run_tests(service_cfg: dict) -> bool:
    """Return True when the suite passes."""
    proc = subprocess.run(
        service_cfg["test_command"].split(),
        cwd=REPO_ROOT / service_cfg["dir"],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def list_mutants(catalog: dict) -> None:
    rows = [(m["id"], m["service"], m["category"], m["description"]) for m in catalog["mutants"]]
    widths = [max(len(r[i]) for r in rows + [("MUTANT", "SERVICE", "CATEGORY", "DESCRIPTION")]) for i in range(3)]
    header = f"{'MUTANT':<{widths[0]}}  {'SERVICE':<{widths[1]}}  {'CATEGORY':<{widths[2]}}  DESCRIPTION"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r[0]:<{widths[0]}}  {r[1]:<{widths[1]}}  {r[2]:<{widths[2]}}  {r[3]}")


def verify(catalog: dict, only_mutant: str | None, only_service: str | None) -> int:
    mutants = catalog["mutants"]
    if only_mutant:
        mutants = [m for m in mutants if m["id"] == only_mutant]
        if not mutants:
            print(f"error: unknown mutant {only_mutant}", file=sys.stderr)
            return 2
    if only_service:
        if only_service not in catalog["services"]:
            print(f"error: unknown service {only_service}", file=sys.stderr)
            return 2
        mutants = [m for m in mutants if m["service"] == only_service]
    if not mutants:
        print("error: no mutants selected", file=sys.stderr)
        return 2

    # Baseline: the untouched suite must be green for every service in scope.
    for svc in sorted({m["service"] for m in mutants}):
        cfg = catalog["services"][svc]
        print(f"baseline  {svc} ... ", end="", flush=True)
        if not run_tests(cfg):
            print("FAIL")
            print(f"error: {svc} suite is red before any mutation; fix the suite first", file=sys.stderr)
            return 2
        print("green")

    results = []
    for m in mutants:
        cfg = catalog["services"][m["service"]]
        target = REPO_ROOT / m["file"]
        source = target.read_text()
        if source.count(m["original"]) != 1:
            print(f"error: snippet for {m['id']} not found exactly once in {m['file']}", file=sys.stderr)
            return 2
        print(f"mutant    {m['id']} ... ", end="", flush=True)

        def _restore_and_exit(signum, frame, _target=target, _source=source):
            _target.write_text(_source)
            signal.signal(signum, signal.SIG_DFL)
            signal.raise_signal(signum)

        previous = {
            sig: signal.signal(sig, _restore_and_exit)
            for sig in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            target.write_text(source.replace(m["original"], m["mutated"]))
            killed = not run_tests(cfg)
        finally:
            target.write_text(source)
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        if target.read_text() != source:
            print(f"error: failed to restore {m['file']} after {m['id']}", file=sys.stderr)
            return 2
        results.append((m, killed))
        print("KILLED" if killed else "SURVIVED")

    survivors = [m for m, killed in results if not killed]
    print()
    print(f"{'MUTANT':<32}  {'CATEGORY':<15}  RESULT")
    print("-" * 60)
    for m, killed in results:
        print(f"{m['id']:<32}  {m['category']:<15}  {'KILLED' if killed else 'SURVIVED'}")
    print()
    if survivors:
        print(f"RESULT: FAIL — {len(survivors)} mutant(s) survived; the suite is missing edge-case coverage:")
        for m in survivors:
            print(f"  {m['id']}: {m['description']}")
        return 1
    print(f"RESULT: PASS — all {len(results)} mutants killed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list the mutant catalog")
    parser.add_argument("--verify", action="store_true", help="plant each mutant and run the tests")
    parser.add_argument("--mutant", help="verify a single mutant by id")
    parser.add_argument("--service", help="restrict to one service")
    args = parser.parse_args()

    catalog = load_catalog()
    if args.list:
        list_mutants(catalog)
        return 0
    if args.verify or args.mutant:
        return verify(catalog, args.mutant, args.service)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
