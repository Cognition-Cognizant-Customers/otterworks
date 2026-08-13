#!/usr/bin/env python3
"""Mutation-testing gate for OtterWorks services.

Enumerates deterministic AST-level mutants of a service's source, runs the
service's own test suite against each mutant, and compares the surviving set
against a committed baseline ledger. A mutant that survives is a test-coverage
hole: the suite cannot tell the mutated program from the real one.

Gate semantics (fail-closed):
  * a survivor NOT in the baseline  -> FAIL (new coverage hole)
  * a baseline entry now killed     -> FAIL (stale baseline; ratchet it down)
  * source fingerprint mismatch     -> FAIL (source changed since baseline)
Baseline changes go through --rebaseline, which requires an audited
REBASELINE_REASON recorded in the ledger.

Reports are written to qe/reports/ on every path, including failures.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import random
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
BASELINE_DIR = Path(__file__).resolve().parent / "baselines"
REPORT_DIR = REPO_ROOT / "qe" / "reports"

CMP_SWAPS = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE,
    ast.LtE: ast.Gt,
    ast.Gt: ast.LtE,
    ast.GtE: ast.Lt,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}
BIN_SWAPS = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.FloorDiv,
}


@dataclass(frozen=True)
class Candidate:
    rel_path: str
    lineno: int
    col: int
    op: str
    occ: int
    description: str

    @property
    def mutant_id(self) -> str:
        return f"{self.rel_path}:{self.lineno}:{self.col}:{self.op}:{self.occ}"


def _node_mutation(node: ast.AST) -> tuple[str, str] | None:
    """Return (op, description) if this node has a supported mutation."""
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        op_cls = type(node.ops[0])
        if op_cls in CMP_SWAPS:
            return (
                f"cmp-{op_cls.__name__}",
                f"{op_cls.__name__} -> {CMP_SWAPS[op_cls].__name__}",
            )
    elif isinstance(node, ast.BoolOp):
        op_name = type(node.op).__name__
        return (
            f"bool-{op_name}",
            f"{op_name} -> {'Or' if isinstance(node.op, ast.And) else 'And'}",
        )
    elif isinstance(node, ast.BinOp) and type(node.op) in BIN_SWAPS:
        op_cls = type(node.op)
        return (
            f"bin-{op_cls.__name__}",
            f"{op_cls.__name__} -> {BIN_SWAPS[op_cls].__name__}",
        )
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return ("unary-Not", "remove `not`")
    elif isinstance(node, ast.Constant) and node.value is True:
        return ("const-True", "True -> False")
    elif isinstance(node, ast.Constant) and node.value is False:
        return ("const-False", "False -> True")
    return None


def _iter_candidates_in_tree(tree: ast.AST, rel_path: str):
    """Yield (Candidate, node) pairs. Nodes sharing (line, col, op) — e.g.
    chained same-operator BinOps — are disambiguated by an occurrence index
    assigned in ast.walk order, so every enumerated mutant is reachable."""
    counter: dict[tuple[int, int, str], int] = {}
    for node in ast.walk(tree):
        mutation = _node_mutation(node)
        if mutation is None:
            continue
        op, description = mutation
        key = (node.lineno, node.col_offset, op)
        occ = counter.get(key, 0)
        counter[key] = occ + 1
        yield Candidate(
            rel_path, node.lineno, node.col_offset, op, occ, description
        ), node


class _Mutator(ast.NodeTransformer):
    """Applies exactly one mutation, to the exact node instance targeted."""

    def __init__(self, op: str, target_node: ast.AST):
        self.op = op
        self.target_node = target_node
        self.applied = False

    def _matches(self, node: ast.AST) -> bool:
        return not self.applied and node is self.target_node

    def visit_Compare(self, node: ast.Compare):
        self.generic_visit(node)
        if self._matches(node):
            node.ops = [CMP_SWAPS[type(node.ops[0])]()]
            self.applied = True
        return node

    def visit_BoolOp(self, node: ast.BoolOp):
        self.generic_visit(node)
        if self._matches(node):
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
            self.applied = True
        return node

    def visit_BinOp(self, node: ast.BinOp):
        self.generic_visit(node)
        if self._matches(node):
            node.op = BIN_SWAPS[type(node.op)]()
            self.applied = True
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp):
        self.generic_visit(node)
        if self._matches(node):
            self.applied = True
            return node.operand
        return node

    def visit_Constant(self, node: ast.Constant):
        if self._matches(node):
            self.applied = True
            return ast.copy_location(ast.Constant(value=not node.value), node)
        return node


def load_config(service: str, require_active: bool = True) -> dict:
    config = yaml.safe_load(CONFIG_PATH.read_text())
    services = config.get("services", {})
    if service not in services:
        known = ", ".join(sorted(services))
        sys.exit(f"error: unknown service '{service}' (known: {known})")
    svc = services[service]
    if require_active and svc.get("status") != "active":
        sys.exit(
            f"error: service '{service}' is not active in qe/mutation/config.yaml "
            f"(status: {svc.get('status')}). Reason: {svc.get('status_reason', 'n/a')}"
        )
    return svc


def source_files(svc_cfg: dict) -> list[Path]:
    svc_dir = REPO_ROOT / svc_cfg["dir"]
    files: set[Path] = set()
    for pattern in svc_cfg["source_globs"]:
        files.update(svc_dir.glob(pattern))
    for pattern in svc_cfg.get("exclude_globs", []):
        files -= set(svc_dir.glob(pattern))
    return sorted(p for p in files if p.is_file())


def fingerprint(svc_cfg: dict, files: list[Path]) -> str:
    h = hashlib.sha256()
    for path in files:
        h.update(str(path.relative_to(REPO_ROOT)).encode())
        h.update(hashlib.sha256(path.read_bytes()).digest())
    h.update(Path(__file__).read_bytes())
    h.update(json.dumps(svc_cfg, sort_keys=True).encode())
    return h.hexdigest()


def enumerate_candidates(files: list[Path]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for path in files:
        rel = str(path.relative_to(REPO_ROOT))
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError as exc:
            sys.exit(f"error: cannot parse {rel}: {exc}")
        candidates.extend(cand for cand, _ in _iter_candidates_in_tree(tree, rel))
    candidates.sort(key=lambda c: c.mutant_id)
    return candidates


def select(candidates: list[Candidate], cap: int, seed: int) -> list[Candidate]:
    if len(candidates) <= cap:
        return candidates
    rng = random.Random(seed)
    chosen = rng.sample(candidates, cap)
    return sorted(chosen, key=lambda c: c.mutant_id)


def run_suite(svc_cfg: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        shlex.split(svc_cfg["test_cmd"]),
        cwd=REPO_ROOT / svc_cfg["dir"],
        capture_output=True,
        text=True,
        timeout=svc_cfg.get("timeout_seconds", 120),
    )


def run_setup(svc_cfg: dict) -> int:
    for cmd in svc_cfg.get("setup_cmds", []):
        print(f"[qe-mutation] setup: {cmd}")
        result = subprocess.run(shlex.split(cmd), cwd=REPO_ROOT / svc_cfg["dir"])
        if result.returncode != 0:
            return result.returncode
    return 0


def run_mutant(svc_cfg: dict, candidate: Candidate) -> str:
    path = REPO_ROOT / candidate.rel_path
    original = path.read_bytes()
    tree = ast.parse(original.decode())
    target_node = next(
        (node for cand, node in _iter_candidates_in_tree(tree, candidate.rel_path)
         if cand.mutant_id == candidate.mutant_id),
        None,
    )
    if target_node is None:
        return "not-applied"
    mutator = _Mutator(candidate.op, target_node)
    mutated = mutator.visit(tree)
    if not mutator.applied:
        return "not-applied"
    try:
        path.write_text(ast.unparse(ast.fix_missing_locations(mutated)) + "\n")
        try:
            result = run_suite(svc_cfg)
        except subprocess.TimeoutExpired:
            return "killed-timeout"
        return "killed" if result.returncode != 0 else "survived"
    finally:
        path.write_bytes(original)


def load_baseline(service: str) -> dict | None:
    path = BASELINE_DIR / f"{service}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def write_reports(service: str, payload: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"mutation-{service}.json").write_text(json.dumps(payload, indent=2) + "\n")
    lines = [
        f"# Mutation gate — {service}",
        "",
        f"Result: **{payload['gate']}**",
        f"Mutants run: {payload['stats']['run']}  "
        f"killed: {payload['stats']['killed']}  "
        f"survived: {payload['stats']['survived']}  "
        f"not-applied: {payload['stats'].get('not_applied', 0)}",
        f"Fingerprint: `{payload['fingerprint'][:16]}…`",
        "",
    ]
    if payload["failures"]:
        lines.append("## Gate failures")
        lines += [f"- {f}" for f in payload["failures"]]
        lines.append("")
    if payload["survivors"]:
        lines.append("## Surviving mutants (test-coverage holes)")
        lines += [f"- `{s['id']}` — {s['description']}" for s in payload["survivors"]]
        lines.append("")
    (REPORT_DIR / f"mutation-{service}.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", required=True)
    parser.add_argument("--rebaseline", action="store_true",
                        help="rewrite the baseline ledger (requires --reason)")
    parser.add_argument("--reason", default="",
                        help="audited reason for a rebaseline")
    parser.add_argument("--setup", action="store_true",
                        help="run the service's configured setup_cmds and exit")
    args = parser.parse_args()

    if args.setup:
        return run_setup(load_config(args.service, require_active=False))

    if args.rebaseline and not args.reason.strip():
        sys.exit("error: --rebaseline requires --reason (audited rebaseline only)")

    svc_cfg = load_config(args.service)
    files = source_files(svc_cfg)
    if not files:
        sys.exit("error: no source files matched source_globs")
    fp = fingerprint(svc_cfg, files)

    print(f"[qe-mutation] {args.service}: verifying clean suite is green…")
    try:
        clean = run_suite(svc_cfg)
    except subprocess.TimeoutExpired:
        clean = None
    if clean is None or clean.returncode != 0:
        reason = (
            "clean test suite timed out — raise timeout_seconds or speed up the suite"
            if clean is None
            else "clean test suite is red — fix the suite before mutation testing"
        )
        payload = {
            "service": args.service, "gate": "FAIL", "fingerprint": fp,
            "stats": {"run": 0, "killed": 0, "survived": 0, "not_applied": 0},
            "survivors": [],
            "failures": [reason],
        }
        write_reports(args.service, payload)
        if clean is not None:
            print(clean.stdout[-2000:], clean.stderr[-2000:], sep="\n")
        print(f"[qe-mutation] FAIL: {reason}")
        return 1

    candidates = enumerate_candidates(files)
    selected = select(candidates, svc_cfg.get("mutant_cap", 60), svc_cfg.get("seed", 20260813))
    print(f"[qe-mutation] {len(candidates)} candidates, running {len(selected)} mutants")

    survivors: list[dict] = []
    not_applied: list[str] = []
    killed = 0
    started = time.time()
    for i, cand in enumerate(selected, 1):
        status = run_mutant(svc_cfg, cand)
        if status == "survived":
            survivors.append({"id": cand.mutant_id, "description": cand.description})
            print(f"  [{i}/{len(selected)}] SURVIVED  {cand.mutant_id} ({cand.description})")
        elif status == "not-applied":
            not_applied.append(cand.mutant_id)
            print(f"  [{i}/{len(selected)}] NOT-APPLIED  {cand.mutant_id}")
        else:
            killed += 1
    elapsed = time.time() - started
    print(
        f"[qe-mutation] done in {elapsed:.0f}s: "
        f"killed={killed} survived={len(survivors)} not-applied={len(not_applied)}"
    )

    survivor_ids = {s["id"] for s in survivors}
    failures: list[str] = []
    for mid in not_applied:
        failures.append(
            f"mutant could not be applied (proves nothing about the suite): {mid}"
        )

    if args.rebaseline:
        BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        (BASELINE_DIR / f"{args.service}.json").write_text(json.dumps({
            "service": args.service,
            "fingerprint": fp,
            "reason": args.reason.strip(),
            "allowed_survivors": sorted(survivor_ids),
        }, indent=2) + "\n")
        print(f"[qe-mutation] baseline rewritten with {len(survivor_ids)} allowed survivors")
    else:
        baseline = load_baseline(args.service)
        if baseline is None:
            failures.append("no baseline ledger — create one with `make qe-mutation-baseline`")
        else:
            if baseline.get("fingerprint") != fp:
                failures.append(
                    "source fingerprint changed since the baseline was recorded — "
                    "re-run `make qe-mutation-baseline` with a REBASELINE_REASON"
                )
            allowed = set(baseline.get("allowed_survivors", []))
            for sid in sorted(survivor_ids - allowed):
                failures.append(f"NEW survivor not in baseline: {sid}")
            for sid in sorted(allowed - survivor_ids):
                failures.append(
                    f"stale baseline entry (mutant now killed — ratchet the baseline): {sid}"
                )

    gate = "FAIL" if failures else "PASS"
    payload = {
        "service": args.service,
        "gate": gate,
        "fingerprint": fp,
        "stats": {
            "run": len(selected),
            "killed": killed,
            "survived": len(survivors),
            "not_applied": len(not_applied),
        },
        "survivors": survivors,
        "not_applied": not_applied,
        "failures": failures,
    }
    write_reports(args.service, payload)
    print(f"[qe-mutation] gate: {gate}")
    for failure in failures:
        print(f"  - {failure}")
    return 0 if gate == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
