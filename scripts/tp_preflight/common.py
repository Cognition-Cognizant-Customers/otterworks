#!/usr/bin/env python3
"""Small shared helpers for platform capability preflight scripts."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


class Manifest:
    def __init__(self, platform: str, identity: str = "unavailable") -> None:
        self.data = {
            "platform": platform,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "credential_identity": identity,
            "probes": [],
        }

    def add(self, probe_id: str, description: str, api: str, result: str, detail: str) -> None:
        self.data["probes"].append(
            {"id": probe_id, "description": description, "api": api,
             "result": result, "detail": detail}
        )
        print(f"[{result.upper():8}] {probe_id}: {detail}")

    def write(self, platform: str) -> int:
        out = Path(os.environ.get("TP_PREFLIGHT_DIR", ".tp-preflight"))
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{platform}-capabilities.json"
        path.write_text(json.dumps(self.data, indent=2) + "\n")
        required_denied = [
            p for p in self.data["probes"]
            if p["result"] == "denied"
        ]
        print(f"\nmanifest: {path}")
        print(f"probes: {len(self.data['probes'])}, denied: {len(required_denied)}")
        return 1 if required_denied else 0


def require_env(*names: str) -> None:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        print(f"missing required environment variable(s): {', '.join(missing)}", file=sys.stderr)
        raise SystemExit(2)
