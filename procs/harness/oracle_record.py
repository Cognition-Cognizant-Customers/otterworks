"""Oracle driver for the procs parity harness.

Records immutable transcripts of the OW_BILLING PL/SQL entrypoints
(services/legacy-billing/db/oracle/) by replaying the declarative scenario set
in procs/scenarios/ against the running Oracle billing estate, exactly the way
procs/harness/record.py records the Postgres legacy-billing procedures.

The Postgres-dialect capture/probe SQL in each scenario is translated to the
Oracle dialect through procs/oracle/oracle_map.yaml; scenario ids, inputs,
declared fields, and probe identifiers are shared so the two transcript sets
are directly comparable by procs/harness/oracle_parity.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import oracledb
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "procs" / "scenarios"
ORACLE_MAP = ROOT / "procs" / "oracle" / "oracle_map.yaml"
ORACLE_TRANSCRIPTS = ROOT / "procs" / "oracle" / "transcripts"
ORACLE_DB_DIR = ROOT / "services" / "legacy-billing" / "db" / "oracle"
STATIC_SEED = ORACLE_DB_DIR / "schema" / "03_seed_static.sql"

# Baseline reset scope: only the static-seed tenants that the scenario set
# exercises. Rows seeded per-namespace by testdata/legacy/oracle_billing_seed.py
# (name-prefixed tenants, CUSTOMER_MASTER, INVOICE_HEADER, ...) are left alone,
# mirroring how the Postgres recorder rebuilds billing_<ns> from the checked-in
# schema + seed without touching anything else.
STATIC_TENANTS = tuple(f"00000000-0000-0000-0000-00000000000{n}" for n in range(1, 10))
RESET_DELETES = [
    "DELETE FROM notifications WHERE tenant_id IN ({ids})",
    "DELETE FROM dunning_attempts WHERE tenant_id IN ({ids})",
    "DELETE FROM invoice_lines WHERE invoice_id IN"
    " (SELECT id FROM invoices WHERE tenant_id IN ({ids}))",
    "DELETE FROM invoices WHERE tenant_id IN ({ids})",
    "DELETE FROM rating_results WHERE period_id IN"
    " (SELECT id FROM rating_periods WHERE tenant_id IN ({ids}))",
    "DELETE FROM rating_periods WHERE tenant_id IN ({ids})",
    "DELETE FROM usage_events WHERE tenant_id IN ({ids})",
    "DELETE FROM credit_notes WHERE tenant_id IN ({ids})",
    "DELETE FROM subscriptions WHERE tenant_id IN ({ids})",
    "DELETE FROM subscriptions_hist WHERE tenant_id IN ({ids})",
    "DELETE FROM tenants WHERE id IN ({ids})",
]

WOULD_OVERWRITE = 2
STACK_UNREACHABLE = 3
SCENARIO_FAILED = 4
CONTRACT_MISSING = 6

oracledb.defaults.fetch_decimals = True


def oracle_source_sha() -> str:
    digest = hashlib.sha256()
    paths = sorted(ORACLE_DB_DIR.rglob("*.sql"))
    for path in paths:
        digest.update(str(path.relative_to(ORACLE_DB_DIR)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def connection():
    return oracledb.connect(
        user=os.getenv("DB_USER", "ow_billing"),
        password=os.getenv("DB_PASSWORD", "ow_billing"),
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "52521")),
        service_name=os.getenv("DB_SERVICE", "FREEPDB1"),
        tcp_connect_timeout=10,
    )


def typed(value: Any, kind: str) -> Any:
    if kind == "uuid":
        return str(value)
    if kind == "date":
        return date.fromisoformat(str(value))
    if kind == "integer":
        return int(value)
    if kind == "decimal":
        return Decimal(str(value))
    if kind == "boolean":
        return bool(value)
    return value


def normalized(value: Any, kind: str | None = None) -> Any:
    if value is None:
        return None
    if kind == "decimal":
        return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if kind == "integer":
        current = Decimal(str(value))
        if current != current.to_integral_value():
            return str(current)
        return int(current)
    if kind == "date":
        if isinstance(value, datetime):
            return value.date().isoformat()
        return date.fromisoformat(str(value)).isoformat()
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return str(value)
    if isinstance(value, datetime):
        current = value
        if current.tzinfo is not None:
            current = current.astimezone(timezone.utc)
            return current.isoformat(timespec="seconds").replace("+00:00", "Z")
        return current.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [normalized(item) for item in value]
    if isinstance(value, dict):
        return {str(key): normalized(item) for key, item in sorted(value.items())}
    return value


def rows_from_cursor(cursor) -> list[dict[str, Any]]:
    names = [column[0].lower() for column in cursor.description] if cursor.description else []
    return [
        {name: normalized(value) for name, value in zip(names, row)}
        for row in cursor.fetchall()
    ]


def query_rows(connection_handle, query: str) -> list[dict[str, Any]]:
    with connection_handle.cursor() as cursor:
        cursor.execute(query)
        return rows_from_cursor(cursor)


def seed_statements() -> list[str]:
    statements = []
    for chunk in STATIC_SEED.read_text().split(";"):
        statement = "\n".join(
            line for line in chunk.splitlines() if not line.strip().startswith("--")
        ).strip()
        if not statement:
            continue
        keyword = statement.split(None, 1)[0].upper()
        if keyword in {"WHENEVER", "EXIT", "COMMIT"}:
            continue
        statements.append(statement)
    return statements


def reset_baseline(connection_handle) -> None:
    ids = ", ".join(f"'{tenant}'" for tenant in STATIC_TENANTS)
    with connection_handle.cursor() as cursor:
        for template in RESET_DELETES:
            cursor.execute(template.format(ids=ids))
        for statement in seed_statements():
            try:
                cursor.execute(statement)
            except oracledb.IntegrityError as error:
                (details,) = error.args
                # Shared static rows (plans) may already exist and may be
                # referenced by namespace-seeded subscriptions; keep them.
                if details.full_code != "ORA-00001":
                    raise
    connection_handle.commit()


def capture_fields(rows: list[dict[str, Any]], specs: list[dict[str, Any]]) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    for spec in specs:
        source = str(spec["from"]) if "from" in spec else None
        values = [row.get(source) for row in rows] if source else []
        if spec.get("first"):
            values = values[:1]
        elif spec.get("last"):
            values = values[-1:]
        if spec.get("collect"):
            captured[spec["name"]] = [
                normalized(value, spec.get("type")) for value in values
            ]
        elif spec.get("collect_rows"):
            captured[spec["name"]] = [
                {
                    key: normalized(row.get(key), kind)
                    for key, kind in spec["columns"].items()
                }
                for row in rows
            ]
        else:
            captured[spec["name"]] = normalized(
                values[0] if values else None, spec.get("type")
            )
    return captured


def run_scenario(
    connection_handle, scenario: dict[str, Any], mapping: dict[str, Any]
) -> dict[str, Any]:
    inputs = scenario.get("inputs", [])
    params = [typed(item.get("value"), item["type"]) for item in inputs]
    entrypoint = scenario["entrypoint"]
    contract = mapping["entrypoints"].get(entrypoint)
    if contract is None:
        raise KeyError(f"entrypoint {entrypoint} missing from oracle_map.yaml")
    call = contract["call"]
    if not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", call):
        raise ValueError(f"invalid oracle call target: {call}")
    overrides = mapping.get("scenarios", {}).get(scenario["id"], {})
    with connection_handle.cursor() as cursor:
        if scenario["kind"] == "function":
            ref_cursor = cursor.callfunc(call, oracledb.DB_TYPE_CURSOR, params)
            result_rows = rows_from_cursor(ref_cursor)
        else:
            cursor.callproc(call, params)
            result_rows = []
        if scenario.get("after_sql"):
            after_call = overrides.get("after_call")
            if not after_call:
                raise KeyError(f"{scenario['id']}: after_sql has no oracle after_call mapping")
            cursor.execute(f"BEGIN {after_call}; END;")
    if scenario.get("capture_query"):
        capture_query = overrides.get("capture_query")
        if not capture_query:
            raise KeyError(f"{scenario['id']}: capture_query has no oracle mapping")
        result_rows = query_rows(connection_handle, capture_query)
    probes = {}
    for probe in scenario.get("probes", []):
        probe_query = overrides.get("probes", {}).get(probe["id"])
        if not probe_query:
            raise KeyError(f"{scenario['id']}: probe {probe['id']} has no oracle mapping")
        probe_rows = query_rows(connection_handle, probe_query)
        probes[probe["id"]] = (
            probe_rows
            if probe.get("collect_rows")
            else (probe_rows[0][next(iter(probe_rows[0]))] if probe_rows else None)
        )
    return {
        "scenario": scenario["id"],
        "module": scenario["module"],
        "entrypoint": entrypoint,
        "oracle_entrypoint": call,
        "inputs": {
            str(item["name"]): normalized(item.get("value"), item.get("type"))
            for item in inputs
        },
        "business_fields": capture_fields(result_rows, scenario.get("fields", [])),
        "probes": probes,
    }


def load_scenarios(module: str | None) -> list[dict[str, Any]]:
    paths = sorted(SCENARIOS.glob(f"{module}/*.yaml" if module else "*/*.yaml"))
    return [yaml.safe_load(path.read_text()) for path in paths]


def check_immutability(
    scenarios: list[dict[str, Any]],
    digest: str,
    allow: bool,
    transcript_root: Path,
) -> None:
    existing = []
    for scenario in scenarios:
        path = transcript_root / scenario["module"] / f"{scenario['id']}.json"
        if path.exists():
            payload = json.loads(path.read_text())
            existing.append((path, payload.get("oracle_source_sha")))
    if not existing:
        return
    if not allow or all(old_sha == digest for _, old_sha in existing):
        names = ", ".join(
            str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
            for path, _ in existing
        )
        reason = (
            "unchanged oracle estate source"
            if allow
            else "pass --allow-rerecord only after oracle estate source changes"
        )
        raise RuntimeError(f"would overwrite immutable oracle transcript(s): {names} ({reason})")


def write_transcripts(records: list[dict[str, Any]], digest: str, transcript_root: Path) -> None:
    if not records:
        return
    transcript_root.mkdir(parents=True, exist_ok=True)
    index_path = transcript_root / "index.json"
    if index_path.exists():
        index_by_key = {
            (item["module"], item["scenario"]): item
            for item in json.loads(index_path.read_text())
        }
    else:
        index_by_key = {}
    for record in records:
        record["oracle_source_sha"] = digest
        destination = transcript_root / record["module"] / f"{record['scenario']}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        index_by_key[(record["module"], record["scenario"])] = {
            "scenario": record["scenario"],
            "module": record["module"],
            "oracle_entrypoint": record["oracle_entrypoint"],
        }
    index = sorted(index_by_key.values(), key=lambda item: (item["module"], item["scenario"]))
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    (transcript_root / "ORACLE_SOURCE_SHA").write_text(digest + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module")
    parser.add_argument("--allow-rerecord", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=ORACLE_TRANSCRIPTS)
    args = parser.parse_args()
    scenarios = load_scenarios(args.module)
    if args.module and not scenarios:
        print(f"unknown scenario module: {args.module}", file=sys.stderr)
        return SCENARIO_FAILED
    mapping = yaml.safe_load(ORACLE_MAP.read_text())
    digest = oracle_source_sha()
    try:
        check_immutability(scenarios, digest, args.allow_rerecord, args.output_dir)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return WOULD_OVERWRITE
    records = []
    for scenario in scenarios:
        try:
            connection_handle = connection()
        except oracledb.Error as error:
            print(f"oracle billing estate unreachable: {error}", file=sys.stderr)
            return STACK_UNREACHABLE
        try:
            reset_baseline(connection_handle)
            records.append(run_scenario(connection_handle, scenario, mapping))
        except KeyError as error:
            print(f"{scenario['id']}: contract missing: {error}", file=sys.stderr)
            return CONTRACT_MISSING
        except Exception as error:
            print(f"{scenario['id']}: scenario failed: {error}", file=sys.stderr)
            return SCENARIO_FAILED
        finally:
            connection_handle.close()
    write_transcripts(records, digest, args.output_dir)
    print(f"Recorded {len(records)} oracle scenario(s), ORACLE_SOURCE_SHA={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
