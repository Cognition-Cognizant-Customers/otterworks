#!/usr/bin/env python3
"""Fixture verification harness for the mongo_customers unit.

Proves idempotency by an actual rerun: runs the migration twice against the
target, recomputes recon values from the target after each run, and requires
the recomputed numbers to be identical. Writes the final recon report with
the rerun evidence embedded.

Usage: python3 verify_customers_fixture.py --ns demo --out <report.recon.json>
"""

import argparse
import json
import re
import sys

import migrate_customers
import recon_customers


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", required=True)
    ap.add_argument("--run-mode", choices=["fixture", "live"], default="fixture")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_]+", args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2

    migrate_customers.migrate(args.ns)
    first = recon_customers.compute(args.ns)
    migrate_customers.migrate(args.ns)
    second = recon_customers.compute(args.ns)

    identical = first == second
    evidence = (
        "migration executed twice against the same target; recon values "
        "recomputed from the target after each run were "
        + ("identical" if identical else
           f"DIFFERENT: {json.dumps(first)} vs {json.dumps(second)}")
    )
    idempotency = {"performed": True,
                   "result": "pass" if identical else "fail",
                   "evidence": evidence}
    report = recon_customers.build_report(args.ns, args.run_mode, second,
                                          idempotency)
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    failed = [c["id"] for c in report["checks"] if c["result"] != "pass"]
    if not identical:
        failed.append("idempotency_rerun")
    detections = report["planted_anomaly_detections"]
    if detections["missing"] or detections["unexpected"]:
        failed.append("planted-anomaly-detections")
    print(f"[mongo_customers] recon written to {args.out}; "
          f"{'FAIL: ' + ','.join(failed) if failed else 'all checks pass'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
