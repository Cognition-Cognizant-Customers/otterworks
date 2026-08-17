#!/usr/bin/env python3
"""Golden HTTP transcript harness for the legacy-portal decomposition.

record: execute transcript_spec.json in order against a FRESH store and save
        every (request, status, body) as the golden transcript.
replay: execute the same spec against another base URL and diff each response
        against the golden transcript, emitting a machine-readable
        *.recon.json report.

Parity contract (declared in transcript_spec.json):
- status codes match exactly;
- bodies are compared as parsed JSON values (serializer whitespace/key order
  are out of contract);
- timestamp fields are validated as ISO-8601 instants, then normalized to
  "<instant>";
- steps marked assert_status_only compare only the status code.

Usage:
  transcript.py record --base-url http://localhost:8095 --out golden.json
  transcript.py replay --base-url https://<api> --golden golden.json --out replay.recon.json
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SPEC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transcript_spec.json")
INSTANT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$")


def load_spec():
    with open(SPEC_PATH) as f:
        return json.load(f)


def execute(base_url, step):
    if step.get("pre_sleep_ms"):
        time.sleep(step["pre_sleep_ms"] / 1000.0)
    url = base_url.rstrip("/") + step["path"]
    data = None
    headers = {"Accept": "application/json"}
    if "body" in step:
        data = json.dumps(step["body"]).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=step["method"], headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        body = json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        body = {"_non_json_body": raw.decode("utf-8", "replace")[:500]}
    return status, body


def normalize(value, ts_fields, problems, path=""):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            here = f"{path}.{k}" if path else k
            if k in ts_fields:
                if not (isinstance(v, str) and INSTANT_RE.match(v)):
                    problems.append(f"{here}: not an ISO-8601 instant: {v!r}")
                out[k] = "<instant>"
            else:
                out[k] = normalize(v, ts_fields, problems, here)
        return out
    if isinstance(value, list):
        return [normalize(v, ts_fields, problems, f"{path}[{i}]") for i, v in enumerate(value)]
    return value


def run(base_url):
    spec = load_spec()
    ts_fields = set(spec["normalize_timestamp_fields"])
    results = []
    for step in spec["steps"]:
        status, body = execute(base_url, step)
        problems = []
        entry = {
            "id": step["id"],
            "method": step["method"],
            "path": step["path"],
            "assert_status_only": bool(step.get("assert_status_only")),
            "status": status,
            "body": None if step.get("assert_status_only") else normalize(body, ts_fields, problems),
            "timestamp_format_problems": problems,
        }
        results.append(entry)
    return results


def cmd_record(args):
    results = run(args.base_url)
    bad = [r for r in results if r["timestamp_format_problems"]]
    golden = {
        "kind": "golden-transcript",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source_base_url": args.base_url,
        "spec_sha": spec_fingerprint(),
        "steps": results,
    }
    with open(args.out, "w") as f:
        json.dump(golden, f, indent=2)
    print(f"recorded {len(results)} steps -> {args.out}")
    if bad:
        for r in bad:
            print(f"TIMESTAMP-FORMAT {r['id']}: {r['timestamp_format_problems']}")
        return 1
    return 0


def spec_fingerprint():
    import hashlib
    with open(SPEC_PATH, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def cmd_replay(args):
    with open(args.golden) as f:
        golden = json.load(f)
    results = run(args.base_url)
    checks = []
    for g, r in zip(golden["steps"], results):
        mismatches = []
        if g["status"] != r["status"]:
            mismatches.append(f"status: golden={g['status']} live={r['status']}")
        if not g["assert_status_only"] and g["body"] != r["body"]:
            mismatches.append(
                "body: golden=" + json.dumps(g["body"], sort_keys=True)[:400]
                + " live=" + json.dumps(r["body"], sort_keys=True)[:400])
        mismatches.extend(f"timestamp format: {p}" for p in r["timestamp_format_problems"])
        checks.append({
            "id": g["id"],
            "method": g["method"],
            "path": g["path"],
            "assert_status_only": g["assert_status_only"],
            "result": "pass" if not mismatches else "fail",
            "mismatches": mismatches,
        })
    report = {
        "kind": "recon-report",
        "unit": "legacy-portal-decomposition/http-parity",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "golden_source": golden["source_base_url"],
        "golden_spec_sha": golden["spec_sha"],
        "live_spec_sha": spec_fingerprint(),
        "replay_base_url": args.base_url,
        "run_mode": args.run_mode,
        "steps_total": len(checks),
        "steps_passed": sum(1 for c in checks if c["result"] == "pass"),
        "unverified_paths": args.unverified,
        "checks": checks,
    }
    if golden["spec_sha"] != report["live_spec_sha"]:
        report["checks"].append({"id": "spec-fingerprint", "result": "fail",
                                 "mismatches": ["golden was recorded from a different spec"]})
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)
    failed = [c for c in report["checks"] if c["result"] == "fail"]
    print(f"{report['steps_passed']}/{report['steps_total']} steps passed -> {args.out}")
    for c in failed:
        print(f"FAIL {c['id']}: {'; '.join(c['mismatches'])}")
    return 1 if failed else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("record")
    pr.add_argument("--base-url", required=True)
    pr.add_argument("--out", required=True)
    pr.set_defaults(fn=cmd_record)
    pp = sub.add_parser("replay")
    pp.add_argument("--base-url", required=True)
    pp.add_argument("--golden", required=True)
    pp.add_argument("--out", required=True)
    pp.add_argument("--run-mode", default="live", choices=["live", "fixture"])
    pp.add_argument("--unverified", action="append", default=[],
                    help="Path/behavior intentionally outside this replay's coverage "
                         "(listed verbatim in the recon report)")
    pp.set_defaults(fn=cmd_replay)
    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
