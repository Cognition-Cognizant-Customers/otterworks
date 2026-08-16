#!/usr/bin/env python3
"""Run the converted estate rollup on the pre-existing serverless SQL warehouse.

The `ow_tp_estate_rollup` job task executes `databricks/notebooks/estate_rollup.py` on
serverless job compute. This runner imports that same notebook module and executes its
`run_pipeline` with a runner backed by the SQL Statement Execution API, so the statement
text exercised here is byte-for-byte the statement text the job task runs — there is no
second implementation, which is what makes evidence produced this way evidence about the
job. No compute is created.

It also lands the seed manifest (`testdata/legacy/manifests/<ns>.json`) into
`bronze.seed_anomaly_manifest` over the warehouse, because the demo PAT lacks the Files
API `files` scope and cannot write the landing volume. Every `gold.estate_anomalies` row
cites that manifest row, so the anomalies are traceable to a planted count.

Usage:
  export DATABRICKS_HOST="${DATABRICKS_DEMO_HOST%/}" DATABRICKS_TOKEN="$DATABRICKS_DEMO_TOKEN"
  python3 scripts/tp_databricks/run_estate_rollup.py ddl      [--ns demo]
  python3 scripts/tp_databricks/run_estate_rollup.py manifest [--ns demo]
  python3 scripts/tp_databricks/run_estate_rollup.py run      [--ns demo] [--run-date 2026-08-16]
  python3 scripts/tp_databricks/run_estate_rollup.py show     [--ns demo] [--run-date ...]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = REPO_ROOT / "databricks" / "notebooks" / "estate_rollup.py"
DDL_FILE = REPO_ROOT / "databricks" / "sql" / "estate_rollup_tables.sql"
MANIFEST_DIR = REPO_ROOT / "testdata" / "legacy" / "manifests"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dbx = _load(Path(__file__).with_name("dbx.py"), "tp_dbx")
pipeline = _load(NOTEBOOK, "tp_estate_rollup_notebook")


def _execute(catalog: str):
    return lambda statement: dbx.sql(statement, catalog=catalog)


def _scalar(catalog: str):
    def scalar(statement: str):
        rows = dbx.sql(statement, catalog=catalog)
        return rows[0][0] if rows and rows[0] else None

    return scalar


def apply_ddl(catalog: str) -> None:
    for statement in pipeline.ddl_statements(DDL_FILE.read_text(encoding="utf-8"), catalog):
        print(f"-> {statement['sql'].splitlines()[0][:90]}")
        dbx.sql(statement["sql"], catalog=catalog)
    print(f"applied {DDL_FILE.relative_to(REPO_ROOT)}")


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def land_manifest(catalog: str, ns: str) -> dict:
    """Load the seed manifest's planted-anomaly counts into bronze, over SQL only.

    The manifest is runtime state produced by `make seed-legacy NS=<ns>` and is never
    committed; its sha256 is carried into every row so a re-seeded namespace is visible
    in the anomaly evidence rather than silent.
    """
    pipeline.validate_ns(ns)
    pipeline.validate_identifier(catalog, "catalog")
    path = MANIFEST_DIR / f"{ns}.json"
    if not path.exists():
        raise SystemExit(
            f"no seed manifest at {path}; run `make seed-legacy NS={ns}` first "
            "(the manifest is runtime state and is never committed)"
        )
    raw = path.read_bytes()
    manifest = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    planted = manifest.get("planted_anomalies") or []
    if not planted:
        raise SystemExit(f"{path} declares no planted_anomalies; nothing to land")

    values = ",\n  ".join(
        "({ns}, {kind}, {target}, {count}, {generated}, {digest}, current_timestamp())".format(
            ns=_sql_literal(ns),
            kind=_sql_literal(entry["kind"]),
            target=_sql_literal(entry["target"]),
            count=int(entry["count"]),
            generated=_sql_literal(manifest.get("generated_at", "")),
            digest=_sql_literal(digest),
        )
        for entry in sorted(planted, key=lambda item: item["kind"])
    )
    dbx.sql(
        f"INSERT INTO {catalog}.bronze.seed_anomaly_manifest REPLACE WHERE ns = {_sql_literal(ns)}\nVALUES\n  {values}",
        catalog=catalog,
    )
    summary = {"manifest": str(path), "sha256": digest, "kinds": {e["kind"]: e["count"] for e in planted}}
    print(json.dumps(summary, indent=2))
    return summary


def run(catalog: str, ns: str, run_date: str, job_run_id: str, apply_ddl_first: bool) -> dict:
    return pipeline.run_pipeline(
        execute=_execute(catalog),
        scalar=_scalar(catalog),
        ddl_text=DDL_FILE.read_text(encoding="utf-8") if apply_ddl_first else None,
        catalog=catalog,
        ns=ns,
        run_date=run_date,
        job_run_id=job_run_id,
    )


def show(catalog: str, ns: str, run_date: str) -> None:
    pipeline.validate_ns(ns)
    pipeline.validate_run_date(run_date)
    rows = dbx.sql(
        f"SELECT unit, legacy_source, language_vintage, rows_in, rows_out, rejected, recon_result, "
        f"job_run_id, recon_detail FROM {catalog}.gold.estate_daily_rollup "
        f"WHERE ns = {_sql_literal(ns)} AND run_date = DATE{_sql_literal(run_date)} ORDER BY unit",
        catalog=catalog,
    )
    for row in rows:
        print(" | ".join(str(value) for value in row))
    anomalies = dbx.sql(
        f"SELECT anomaly_type, unit, count(*) FROM {catalog}.gold.estate_anomalies "
        f"WHERE ns = {_sql_literal(ns)} GROUP BY anomaly_type, unit ORDER BY anomaly_type",
        catalog=catalog,
    )
    print("-- anomalies")
    for row in anomalies:
        print(" | ".join(str(value) for value in row))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("ddl", "manifest", "run", "show"))
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--catalog", default=dbx.CATALOG)
    parser.add_argument("--run-date", default=date.today().isoformat())
    parser.add_argument("--job-run-id", default="", help="Databricks run id when invoked for a job run; empty locally")
    parser.add_argument("--no-ddl", action="store_true", help="skip the DDL apply before the loads")
    args = parser.parse_args(argv)

    if args.command == "ddl":
        apply_ddl(args.catalog)
        return 0
    if args.command == "manifest":
        land_manifest(args.catalog, args.ns)
        return 0
    if args.command == "show":
        show(args.catalog, args.ns, args.run_date)
        return 0

    try:
        result = run(args.catalog, args.ns, args.run_date, args.job_run_id, not args.no_ddl)
    except pipeline.EstateNotReconciled as exc:
        print(f"ESTATE NOT RECONCILED: {exc}")
        return 3
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
