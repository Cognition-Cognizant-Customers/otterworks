#!/usr/bin/env python3
"""Run the converted CUSTBILL parse pipeline against the serverless warehouse.

Executes exactly the statement set the `ow_tp_parse_custbill` notebook task runs
(`custbill_sql.ddl_statements`, `custbill_parse_sql.gate_statements`,
`parse_statements`, `recon_gate_statements`) through
`scripts/tp_databricks/dbx.py`, so the pipeline can be exercised and reconciled
without the parent session having applied the job, and without creating any
compute beyond the existing serverless SQL warehouse.

Gate semantics match the notebook: any gate query returning rows aborts the run
with a non-zero exit, which is the behaviour the legacy job lacked -- it logged
the trailer mismatch and carried on.

Usage:
    NS=demo python3 scripts/tp_databricks/run_parse_custbill.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "databricks" / "notebooks"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import custbill_parse_sql  # noqa: E402
import custbill_sql  # noqa: E402
import dbx  # noqa: E402


class GateFailure(RuntimeError):
    pass


def _run_gate(label: str, statements: list[tuple[str, str]]) -> None:
    for name, statement in statements:
        rows = dbx.sql(statement)
        if rows:
            preview = "; ".join(" | ".join(map(str, row)) for row in rows[:5])
            raise GateFailure(f"{label}: {name} failed -> {preview}")
        print(f"  ok: {name}")


def run(ns: str, skip_ddl: bool = False) -> int:
    ns = custbill_parse_sql.validate_namespace(ns)
    if not skip_ddl:
        print("applying DDL")
        for statement in custbill_sql.ddl_statements():
            dbx.sql(statement)

    print("bronze manifest gate")
    _run_gate("gate", custbill_parse_sql.gate_statements(ns))

    print("parsing")
    for name, statement in custbill_parse_sql.parse_statements(ns):
        dbx.sql(statement)
        print(f"  done: {name}")

    print("trailer reconciliation gate")
    _run_gate("recon", custbill_parse_sql.recon_gate_statements(ns))

    ns_literal = custbill_parse_sql._quote(ns)
    rows = dbx.sql(f"SELECT count(*) FROM {custbill_sql.SILVER_RECORDS} WHERE ns = {ns_literal}")
    rejects = dbx.sql(f"SELECT count(*) FROM {custbill_sql.SILVER_REJECTS} WHERE ns = {ns_literal}")
    print(f"ns={ns}: {rows[0][0]} parsed records, {rejects[0][0]} quarantined")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default=os.environ.get("NS", "demo"))
    parser.add_argument("--skip-ddl", action="store_true")
    args = parser.parse_args()
    try:
        return run(args.ns, args.skip_ddl)
    except GateFailure as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
