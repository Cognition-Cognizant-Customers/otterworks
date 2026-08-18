#!/usr/bin/env python3
"""Fixed-profile concurrent load test for the portal API (stdlib only).

Drives N workers for a pinned duration against a pinned request mix (the same
read-heavy mix the demo page generates), and reports p50/p95/p99 latency,
error rate, and throughput as a JSON document. Run the identical profile
against the monolith (port 8095) and the deployed gateway so the two result
sets are comparable; never quote numbers from a run with a different profile.

Usage:
  # after-state (closed front door: token required)
  python3 load_test.py --base-url https://<api-id>.execute-api.us-east-1.amazonaws.com \\
      --token "$(terraform output -raw demo_api_token)" \\
      --workers 32 --duration 60 --out load-aws.json

  # before-state (legacy monolith)
  python3 load_test.py --base-url http://localhost:8095 \\
      --workers 32 --duration 60 --out load-monolith.json
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import threading
import time
import urllib.error
import urllib.request

# Pinned request mix: read-heavy, matching the demo page's polling behavior.
# Do not edit between the before/after takes of one comparison.
REQUEST_MIX = [
    {"method": "GET", "path": "/health"},
    {"method": "GET", "path": "/api/announcements"},
    {"method": "GET", "path": "/api/preferences/alice"},
    {"method": "GET", "path": "/api/feedback/average-rating"},
    {"method": "GET", "path": "/api/feedback?userId=alice"},
]

PROFILE_VERSION = "portal-load-v1"


def percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    k = max(0, min(len(sorted_values) - 1, round(pct / 100 * (len(sorted_values) - 1))))
    return sorted_values[k]


def worker(base_url: str, token: str | None, stop_at: float,
           latencies: list[float], failures: list[str], lock: threading.Lock) -> int:
    count = 0
    i = 0
    while time.monotonic() < stop_at:
        step = REQUEST_MIX[i % len(REQUEST_MIX)]
        i += 1
        url = base_url.rstrip("/") + step["path"]
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, method=step["method"], headers=headers)
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
                status = resp.status
        except urllib.error.HTTPError as e:
            e.read()
            status = e.code
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            status = 0
            with lock:
                failures.append(f"{step['method']} {step['path']}: {type(e).__name__}")
        elapsed_ms = (time.monotonic() - start) * 1000
        count += 1
        with lock:
            latencies.append(elapsed_ms)
            if status >= 500:
                failures.append(f"{step['method']} {step['path']}: HTTP {status}")
    return count


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", required=True)
    p.add_argument("--token", default=os.environ.get("PORTAL_API_TOKEN"),
                   help="Bearer token for the closed front door "
                        "(default: env PORTAL_API_TOKEN; omit for the monolith)")
    p.add_argument("--workers", type=int, default=32,
                   help="concurrent workers (default 32)")
    p.add_argument("--duration", type=int, default=60,
                   help="seconds to sustain the load (default 60)")
    p.add_argument("--out", help="write the JSON report here as well as stdout")
    args = p.parse_args()

    latencies: list[float] = []
    failures: list[str] = []
    lock = threading.Lock()
    started = time.time()
    stop_at = time.monotonic() + args.duration

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(worker, args.base_url, args.token, stop_at,
                               latencies, failures, lock)
                   for _ in range(args.workers)]
        total = sum(f.result() for f in futures)

    wall = time.time() - started
    ordered = sorted(latencies)
    error_count = len(failures)
    report = {
        "kind": "load-test-report",
        "profile": PROFILE_VERSION,
        "base_url": args.base_url,
        "workers": args.workers,
        "duration_seconds": args.duration,
        "request_mix": [f"{s['method']} {s['path']}" for s in REQUEST_MIX],
        "requests": total,
        "throughput_rps": round(total / wall, 2) if wall else 0,
        "latency_ms": {
            "p50": round(percentile(ordered, 50), 1),
            "p95": round(percentile(ordered, 95), 1),
            "p99": round(percentile(ordered, 99), 1),
            "max": round(ordered[-1], 1) if ordered else 0,
        },
        "errors": error_count,
        "error_rate": round(error_count / total, 4) if total else 0,
        "error_sample": sorted(set(failures))[:10],
    }
    body = json.dumps(report, indent=2)
    print(body)
    if args.out:
        with open(args.out, "w") as f:
            f.write(body + "\n")


if __name__ == "__main__":
    main()
