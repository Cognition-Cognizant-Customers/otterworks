#!/usr/bin/env python3
"""Validate JSON contract/recon artifacts without changing legacy prose files."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = {
    "contract": ROOT / "docs/tech-partnerships/contracts/schema/unit-contract.schema.json",
    "recon": ROOT / "docs/tech-partnerships/contracts/schema/recon-report.schema.json",
}

def validate_file(path: Path, schema: dict) -> list[str]:
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        return [f"{path}: not valid JSON ({exc})"]
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
    return [f"{path}: {'.'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors]

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=["contracts", "recon"])
    p.add_argument("file", nargs="?")
    args = p.parse_args()
    schemas = {}
    for kind, path in SCHEMA.items():
        try:
            schemas[kind] = json.loads(path.read_text())
            Draft202012Validator.check_schema(schemas[kind])
        except Exception as exc:
            print(f"{path}: invalid JSON Schema ({exc})")
            return 2
    legacy_prose: list[Path] = []
    if args.file:
        files = [Path(args.file)]
    elif args.kind == "contracts":
        files = sorted((ROOT / "docs/tech-partnerships/contracts").glob("*.json"))
        legacy_prose = sorted((ROOT / "docs/tech-partnerships/contracts").glob("*.md"))
    else:
        files = sorted((ROOT / "docs/tech-partnerships/recon").glob("*.json"))
        legacy_prose = []
    kind = "contract" if args.kind == "contracts" else "recon"
    failures = [err for f in files for err in validate_file(f, schemas[kind])]
    print(f"validated {len(files)} {args.kind} file(s)")
    if legacy_prose:
        print(f"informational: {len(legacy_prose)} legacy prose contract(s) are not yet migrated to JSON schema")
        for path in legacy_prose:
            print(f"  legacy prose: {path}")
    if not args.file and args.kind == "contracts" and not files:
        failures.append(
            "no JSON contract files found; legacy Markdown contracts are not yet migrated to the schema"
        )
    if failures:
        print("\n".join(failures))
        print(f"FAIL: {len(failures)} validation error(s)")
        return 1
    print("PASS")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
