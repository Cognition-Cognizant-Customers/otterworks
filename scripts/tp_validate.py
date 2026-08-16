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
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
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
    failures: list[str] = []
    if args.file:
        files = [Path(args.file)]
    elif args.kind == "contracts":
        files = sorted((ROOT / "docs/tech-partnerships/contracts").glob("*.json"))
        legacy_prose = sorted((ROOT / "docs/tech-partnerships/contracts").glob("*.md"))
    else:
        files = []
        legacy_prose = []
        for candidate in sorted((ROOT / "docs/tech-partnerships/recon").glob("*.json")):
            try:
                data = json.loads(candidate.read_text())
            except Exception as exc:
                failures.append(f"{candidate}: not valid JSON ({exc})")
                continue
            if (
                isinstance(data, dict)
                and (data.get("kind") == "recon-report" or candidate.name.endswith(".recon.json"))
            ):
                files.append(candidate)
            else:
                legacy_prose.append(candidate)
    kind = "contract" if args.kind == "contracts" else "recon"
    failures.extend(err for f in files for err in validate_file(f, schemas[kind]))
    print(f"validated {len(files)} {args.kind} file(s)")
    if legacy_prose:
        label = "legacy prose contract(s)" if args.kind == "contracts" else "unmigrated non-report JSON artifact(s)"
        print(f"informational: {len(legacy_prose)} {label}")
        for path in legacy_prose:
            prefix = "legacy prose" if args.kind == "contracts" else "unmigrated artifact"
            print(f"  {prefix}: {path}")
    if not args.file and args.kind == "contracts" and not files:
        failures.append(
            "no JSON contract files found; migrate these legacy prose contracts to the schema:\n"
            + "\n".join(f"  - {path}" for path in legacy_prose)
        )
    if failures:
        print("\n".join(failures))
        print(f"FAIL: {len(failures)} validation error(s)")
        return 1
    print("PASS")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
