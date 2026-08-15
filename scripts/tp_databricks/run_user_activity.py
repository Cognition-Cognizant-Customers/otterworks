#!/usr/bin/env python3
"""Run the converted user-activity pipeline on the serverless SQL warehouse.

The Databricks job task executes `databricks/notebooks/user_activity_daily.py` on
serverless job compute. This runner executes the *same* notebook `main()` with a
runner backed by the SQL Statement Execution API, so the statement text under test
here is byte-for-byte the statement text the job runs — no second implementation.
That is what lets the recon evidence be produced without asking the parent session
to apply the job (and without ever creating a cluster).

Usage:
  export DATABRICKS_HOST="$DATABRICKS_DEMO_HOST" DATABRICKS_TOKEN="$DATABRICKS_DEMO_TOKEN"
  python scripts/tp_databricks/run_user_activity.py --ns demo --report-date 2026-08-15 \
      --source-mode table --max-upstream-lag-days 30
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dbx  # noqa: E402
from pipeline_module import load_pipeline  # noqa: E402

pipeline = load_pipeline()

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DDL_FILE = os.path.join(REPO_ROOT, "databricks", "ddl", "user_activity_tables.sql")
LANDING_DDL_FILE = os.path.join(REPO_ROOT, "databricks", "ddl", "user_activity_landing.sql")


class WarehouseRunner:
    """Statement runner backed by the existing serverless SQL warehouse."""

    def __init__(self, catalog: str):
        self.catalog = catalog

    def execute(self, statement: str) -> None:
        dbx.sql(statement, catalog=self.catalog)

    def row(self, statement: str) -> dict:
        # Each API statement is its own session and returns positional values, so the
        # probe row is serialised to JSON server-side and decoded by column name.
        wrapped = f"SELECT TO_JSON(STRUCT(*)) FROM (\n{statement}\n) probe"
        rows = dbx.sql(wrapped, catalog=self.catalog)
        payload = json.loads(rows[0][0]) if rows and rows[0] and rows[0][0] else {}
        # TO_JSON drops NULL fields; restore them so callers can read every key.
        return payload

    def read_text(self, path: str) -> str:
        raise RuntimeError(f"the warehouse runner is given DDL inline, not read from {path}")


def ddl_sql() -> str:
    """The committed DDL, table definitions plus this unit's landing tables."""
    parts = []
    for path in (DDL_FILE, LANDING_DDL_FILE):
        with open(path, encoding="utf-8") as handle:
            parts.append(handle.read())
    return "\n;\n".join(parts)


def run(params: dict[str, str]) -> dict:
    """Execute the notebook pipeline; returns its result dict.

    Missing probe keys are normalised to None because `TO_JSON` omits NULL fields.
    """
    cfg_catalog = params.get("catalog") or pipeline.DEFAULTS["catalog"]
    runner = WarehouseRunner(cfg_catalog)
    original_row = runner.row

    def row(statement: str) -> dict:
        payload = original_row(statement)
        for key in ("upstream_summary_date", "upstream_rows", "latest_event_date",
                    "upstream_lag_days", "report_date"):
            payload.setdefault(key, None)
        return payload

    runner.row = row  # type: ignore[method-assign]
    return pipeline.main(runner, params, ddl_sql=ddl_sql())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--catalog", default="ow_tp")
    parser.add_argument("--report-date", default="")
    parser.add_argument("--lookback-days", default="30")
    parser.add_argument("--max-upstream-lag-days", default=None,
                        help="freshness tolerance; defaults to the job's own default")
    parser.add_argument("--source-mode", choices=("volume", "table"), default="table")
    parser.add_argument("--stage", choices=("pipeline", "freshness_gate"), default="pipeline")
    parser.add_argument("--on-stale", choices=("fail", "mark"), default="fail")
    args = parser.parse_args(argv)

    params = {
        "ns": args.ns,
        "catalog": args.catalog,
        "report_date": args.report_date,
        "lookback_days": args.lookback_days,
        "source_mode": args.source_mode,
        "stage": args.stage,
        "on_stale": args.on_stale,
    }
    if args.max_upstream_lag_days is not None:
        params["max_upstream_lag_days"] = args.max_upstream_lag_days

    try:
        result = run(params)
    except pipeline.UpstreamNotFresh as exc:
        print(f"REFUSED: {exc}")
        return 3
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
