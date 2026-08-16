#!/usr/bin/env python3
"""Small shared helpers for platform capability preflight scripts."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


class Manifest:
    def __init__(self, platform: str, identity: str = "unavailable") -> None:
        self.output_dir = validate_manifest_dir()
        self.data = {
            "platform": platform,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "credential_identity": self._redact_identity(identity),
            "probes": [],
        }

    def set_identity(self, identity: str) -> None:
        self.data["credential_identity"] = self._redact_identity(identity)

    def add(self, probe_id: str, description: str, api: str, result: str, detail: str) -> None:
        redacted_detail = self._redact(str(detail))
        probe = {"id": probe_id, "description": description, "api": api,
                 "result": result, "detail": redacted_detail}
        for key, value in probe.items():
            if isinstance(value, str):
                probe[key] = self._redact(value)
        self.data["probes"].append(probe)
        print(f"[{result.upper():8}] {probe_id}: {redacted_detail}")

    @staticmethod
    def _redact(detail: str) -> str:
        secrets = [
            value for key, value in os.environ.items()
            if value and (
                key in {
                    "MONGODB_ATLAS_PUBLIC_KEY",
                    "MONGODB_ATLAS_PRIVATE_KEY",
                    "MONGODB_ATLAS_PROJECT_ID",
                    "DATABRICKS_DEMO_HOST",
                    "DATABRICKS_DEMO_TOKEN",
                    "AWS_ACCESS_KEY_ID",
                    "AWS_SECRET_ACCESS_KEY",
                }
                or len(value) >= 12 and any(
                    re.search(rf"(?:^|_){token}(?:$|_)", key.upper())
                    for token in ("TOKEN", "SECRET", "PRIVATE_KEY", "ACCESS_KEY", "URI")
                )
            )
        ]
        for secret in sorted(secrets, key=len, reverse=True):
            detail = detail.replace(secret, "[REDACTED]")
        detail = re.sub(r"(?i)mongodb(?:\+srv)?://[^\s\"']+", "mongodb://[REDACTED]", detail)
        detail = re.sub(r"\bAKIA[0-9A-Z]{16}\b", "[REDACTED_AWS_KEY]", detail)
        detail = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", detail)
        return detail[:1000]

    @classmethod
    def _redact_identity(cls, identity: str) -> str:
        value = cls._redact(str(identity))
        match = re.fullmatch(r"([^@\s]+)@([^\s@]+)", value)
        if match:
            return f"{match.group(1)[0]}***@{match.group(2)}"
        return value

    def write(self, platform: str) -> int:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{platform}-capabilities.json"
        path.write_text(json.dumps(self.data, indent=2) + "\n")
        denied_probes = [
            p for p in self.data["probes"]
            if p["result"] == "denied"
        ]
        print(f"\nmanifest: {path}")
        print(f"probes: {len(self.data['probes'])}, denied: {len(denied_probes)}")
        return 1 if denied_probes else 0


def validate_manifest_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    sandbox = (repo_root / ".tp-preflight").resolve()
    configured = os.environ.get("TP_PREFLIGHT_DIR")
    if configured and not Path(configured).is_absolute():
        raise SystemExit("TP_PREFLIGHT_DIR must be an absolute path when set")
    resolved = Path(configured).resolve() if configured else sandbox
    if resolved == repo_root or repo_root in resolved.parents:
        if resolved != sandbox and sandbox not in resolved.parents:
            raise SystemExit(
                "TP_PREFLIGHT_DIR resolves inside the repository outside the "
                f".tp-preflight sandbox ({resolved}); out-of-tree destinations are allowed"
            )
    return resolved


def require_env(*names: str) -> None:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        print(f"missing required environment variable(s): {', '.join(missing)}", file=sys.stderr)
        raise SystemExit(2)


def exception_detail(exc: Exception) -> str:
    line = str(exc).splitlines()[0][:240] if str(exc) else ""
    line = re.sub(r"https?://\S+", "[URL]", line)
    return f"{type(exc).__name__}: {line}" if line else type(exc).__name__
