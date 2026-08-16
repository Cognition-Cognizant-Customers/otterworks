#!/usr/bin/env python3
"""Transport-only Databricks fixture.

This intentionally does not emulate Spark SQL or Delta. It exercises the
portion that can be made meaningful locally: deterministic landing layout,
byte preservation, namespace isolation, and cleanup/idempotent reruns.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

UNITS = {
    "analytics_daily": "source artifact landing and rerun inventory",
    "audit_archive_weekly": "source artifact landing and rerun inventory",
    "estate_rollup": "source artifact landing and rerun inventory",
    "finance_excel_report": "source artifact landing and rerun inventory",
    "parse_custbill_fixedwidth": "source artifact landing and rerun inventory",
    "search_reindex_weekly": "source artifact landing and rerun inventory",
    "sftp_ingest_poll": "source artifact landing and rerun inventory",
    "storage_cleanup_daily": "source artifact landing and rerun inventory",
    "user_activity_daily": "source artifact landing and rerun inventory",
}


def files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def validate_namespace(namespace: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", namespace):
        raise SystemExit("--ns must match [A-Za-z0-9_-]+ and must not be empty")


def validate_landing(landing: str) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    sandbox = (repo_root / ".tp-preflight").resolve()
    resolved = Path(landing).resolve()
    if resolved == sandbox or sandbox not in resolved.parents:
        raise SystemExit(
            "--landing must resolve beneath the repository .tp-preflight sandbox "
            f"({sandbox}) and must not be the sandbox root"
        )
    return resolved


def validate_source(source: str) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    resolved = Path(source).resolve()
    if resolved != repo_root and repo_root not in resolved.parents:
        raise SystemExit(
            "--source must resolve beneath the repository root "
            f"({repo_root})"
        )
    if not resolved.exists() or not resolved.is_dir():
        raise SystemExit(f"--source must be an existing directory: {resolved}")
    return resolved


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["land", "verify", "clean"])
    p.add_argument("--ns", default="fixture")
    p.add_argument("--source", default="etl/legacy-extra")
    p.add_argument("--landing", default=".tp-preflight/databricks-fixture/landing")
    args = p.parse_args()
    validate_namespace(args.ns)
    landing = validate_landing(args.landing) / args.ns
    if args.action == "clean":
        shutil.rmtree(landing, ignore_errors=True)
        print(f"fixture cleaned: {landing}")
        return 0
    if args.action == "land":
        source = validate_source(args.source)
        manifest_name = Path("fixture-manifest.json")
        collision = next(
            (
                src for src in files(source)
                if src.relative_to(source) == manifest_name
            ),
            None,
        )
        if collision is not None:
            raise SystemExit(
                "source artifact collides with reserved fixture manifest path: "
                f"{collision}"
            )
        shutil.rmtree(landing, ignore_errors=True)
        landing.mkdir(parents=True, exist_ok=True)
        copied = []
        for src in files(source):
            dst = landing / src.relative_to(source)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            source_bytes = src.read_bytes()
            copied_bytes = dst.read_bytes()
            if source_bytes != copied_bytes:
                raise SystemExit(f"fixture copy mismatch: {src} -> {dst}")
            copied.append({"path": str(dst.relative_to(landing)), "sha256": hashlib.sha256(source_bytes).hexdigest(), "bytes": len(source_bytes)})
        manifest = {"namespace": args.ns, "generated_at": datetime.now(timezone.utc).isoformat(), "transport": "local landing directory", "sql_execution": "not emulated", "files": copied, "unit_coverage": [{"unit": u, "status": "transport-only", "coverage": c} for u, c in UNITS.items()]}
        (landing / "fixture-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"fixture landed {len(copied)} file(s) under {landing}")
        print("SQL execution remains unverified; run the live Databricks job for recon.")
        return 0
    manifest_path = landing / "fixture-manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"missing fixture manifest: run land first ({manifest_path})")
    manifest = json.loads(manifest_path.read_text())
    failures = []
    for item in manifest["files"]:
        path = landing / item["path"]
        issues = []
        if not path.exists():
            issues.append("missing")
        else:
            if path.stat().st_size != item["bytes"]:
                issues.append(f"size {path.stat().st_size} != {item['bytes']} bytes")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != item["sha256"]:
                issues.append(f"digest {digest} != {item['sha256']}")
        if issues:
            failures.append((item["path"], "; ".join(issues)))
    verified = len(manifest["files"]) - len(failures)
    if failures:
        print(f"fixture verification failed: {verified}/{len(manifest['files'])} files byte-identical")
        print("mismatches:")
        for path, reason in failures:
            print(f"  - {path}: {reason}")
    else:
        print(f"fixture verified: {verified}/{len(manifest['files'])} files byte-identical")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
