#!/usr/bin/env python3
"""Single Pane of Glass — end-to-end agentic workflow orchestrator.

Pulls from three heterogeneous systems, transforms/reconciles the data, and
renders one consolidated dashboard:

  1. Structured API/DB  (OtterWorks enterprise drive gateway)
  2. UI-only web app     (OtterWorks web portal, driven through the browser)
  3. External public web (World Bank Open Data)

Usage:
  python run.py                 # full run (all three systems)
  python run.py --skip-portal   # skip the browser leg (no CDP browser needed)

Outputs land in ./output: dashboard.html, data.json, portal.png.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config  # noqa: E402
from aggregate import aggregate  # noqa: E402
from connectors import external_web, structured_api  # noqa: E402
import render  # noqa: E402

# Reuse the web client's Playwright install for the Node browser connector.
NODE_MODULES = os.path.abspath(
    os.path.join(HERE, "..", "..", "frontend", "client-app", "node_modules")
)


def _step(msg):
    print(f"\033[36m\u25b6\033[0m {msg}", flush=True)


def _ok(msg):
    print(f"  \033[32m\u2713\033[0m {msg}", flush=True)


def _warn(msg):
    print(f"  \033[33m!\033[0m {msg}", flush=True)


def run_structured():
    _step("System 1/3 \u2014 structured API/DB (OtterWorks enterprise drive)")
    data = structured_api.collect()
    _ok(f"{data['total_files']:,} files, {data['total_documents']} docs, "
        f"{data['department_count']} departments")
    return data


def run_portal(output_dir):
    _step("System 2/3 \u2014 UI-only web portal (via the browser)")
    script = os.path.join(HERE, "connectors", "web_portal.mjs")
    env = dict(os.environ)
    env["SPOG_OUTPUT_DIR"] = output_dir
    env["NODE_PATH"] = NODE_MODULES
    try:
        proc = subprocess.run(
            ["node", script],
            capture_output=True, text=True, env=env, timeout=180,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        _warn(f"browser leg skipped: {e}")
        return {}
    if proc.returncode != 0:
        _warn(f"browser leg failed: {proc.stderr.strip().splitlines()[-1:]}")
        return {}
    try:
        data = json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        _warn("browser leg produced no parseable output")
        return {}
    _ok(f"logged in via browser; read {len(data.get('recent_files', []))} "
        f"files + {len(data.get('recent_documents', []))} docs on screen + screenshot")
    return data


def run_external():
    _step("System 3/3 \u2014 external public web (World Bank Open Data)")
    data = external_web.collect()
    _ok(f"{len(data.get('indicators', []))} macro indicators retrieved")
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-portal", action="store_true",
                    help="skip the browser leg (no CDP browser required)")
    ap.add_argument("--output", default=config.OUTPUT_DIR)
    args = ap.parse_args()

    output_dir = os.path.abspath(args.output)
    os.makedirs(output_dir, exist_ok=True)
    generated_at = datetime.now(timezone.utc)

    print("\n\033[1mSingle Pane of Glass \u2014 agentic aggregation\033[0m\n")

    structured = run_structured()
    portal = {} if args.skip_portal else run_portal(output_dir)
    external = run_external()

    _step("Transform \u2014 roll up, reconcile UI vs system of record, enrich")
    data = aggregate(structured, portal, external)
    recon = data["reconciliation"]
    (_ok if recon.get("ok") else _warn)(recon["detail"])

    _step("Render \u2014 single pane of glass")
    with open(os.path.join(output_dir, "data.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_at": generated_at.isoformat(), **data}, f, indent=2)
    html_path = render.write(data, output_dir, generated_at)
    _ok(f"dashboard: {html_path}")

    png_path = _snapshot(html_path, output_dir)
    if png_path:
        _ok(f"shareable image: {png_path}")

    live = sum(1 for s in data["sources"] if s["status"] == "ok")
    print(f"\n\033[1mDone.\033[0m {live}/3 systems live \u2192 "
          f"file://{html_path}\n")


def _snapshot(html_path, output_dir):
    """Best-effort full-page PNG of the dashboard via the CDP browser."""
    png_path = os.path.join(output_dir, "dashboard_full.png")
    script = os.path.join(HERE, "screenshot.mjs")
    env = dict(os.environ)
    env["NODE_PATH"] = NODE_MODULES
    try:
        proc = subprocess.run(
            ["node", script, html_path, png_path],
            capture_output=True, text=True, env=env, timeout=90,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return png_path if proc.returncode == 0 and os.path.exists(png_path) else None


if __name__ == "__main__":
    main()
