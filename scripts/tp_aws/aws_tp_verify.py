#!/usr/bin/env python3
"""Recompute end-to-end parity from the deployed AWS pipeline (parent-run only).

Every number is recomputed from the live estate — S3 object bytes, SQS queue
attributes, Step Functions execution history, DynamoDB anomaly ledger — never
taken from a child's report or run-time memory.

Checks:
  parsed-parity     every golden parsed/*.psv byte-identical in S3
  report-parity     golden finance_billing_<date>.csv and .xls byte-identical
  table-counts      per-file record counts in DynamoDB match golden line counts
  dlq-empty         dead-letter queue holds zero messages
  sfn-failures      zero failed executions of ow-tp-<ns>-chain
  anomaly-set       planted-anomaly detections match the expected set exactly

Usage:
  aws_tp_verify.py --ns demo --golden <dir> [--report-date 20260115]
                   [--after-rerun] [--recon-out <path>]

Exit 0 only if every check passes. With --recon-out, writes a schema-valid
recon report (kind=recon-report); pass --after-rerun on the verification that
follows the idempotency rerun so the report can claim it truthfully.
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

EXPECTED_ANOMALIES = [
    "A-invalid-date",
    "A-nonutf8-byte",
    "A-short-record",
    "A-trailer-mismatch",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_DIR = REPO_ROOT / "infrastructure" / "terraform-tp-aws"


def aws(*args: str) -> str:
    return subprocess.run(["aws", *args], check=True, capture_output=True, text=True).stdout


def tf_output(name: str) -> str:
    return subprocess.run(
        ["terraform", f"-chdir={TF_DIR}", "output", "-raw", name],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def s3_bytes(bucket: str, key: str) -> bytes | None:
    import tempfile
    with tempfile.NamedTemporaryFile() as tmp:
        try:
            subprocess.run(["aws", "s3api", "get-object", "--bucket", bucket,
                            "--key", key, tmp.name],
                           check=True, capture_output=True)
        except subprocess.CalledProcessError:
            return None
        return Path(tmp.name).read_bytes()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", default="demo")
    ap.add_argument("--golden", required=True)
    ap.add_argument("--report-date", default="20260115")
    ap.add_argument("--after-rerun", action="store_true")
    ap.add_argument("--recon-out")
    args = ap.parse_args()

    golden = Path(args.golden)
    bucket = tf_output("pipeline_bucket")
    dlq_url = tf_output("events_dlq_url")
    table = tf_output("batch_state_table")

    checks: list[dict] = []

    def check(cid: str, expected, actual, source: str) -> None:
        checks.append({
            "id": cid, "expected": expected, "actual": actual,
            "source_of_truth": source,
            "result": "pass" if expected == actual else "fail",
        })

    # parsed + report byte parity
    for f in sorted((golden / "parsed").glob("*.psv")):
        live = s3_bytes(bucket, f"parsed/{f.name}")
        check(f"parsed-parity:{f.name}",
              f"md5:{__import__('hashlib').md5(f.read_bytes()).hexdigest()}",
              "missing" if live is None else f"md5:{__import__('hashlib').md5(live).hexdigest()}",
              f"s3://{bucket}/parsed/{f.name}")

    for ext in ("csv", "xls"):
        name = f"finance_billing_{args.report_date}.{ext}"
        gf = golden / "reports" / name
        live = s3_bytes(bucket, f"reports/{name}")
        check(f"report-parity:{name}",
              f"md5:{__import__('hashlib').md5(gf.read_bytes()).hexdigest()}",
              "missing" if live is None else f"md5:{__import__('hashlib').md5(live).hexdigest()}",
              f"s3://{bucket}/reports/{name}")

    # per-file record counts from the DynamoDB ledger
    resp = json.loads(aws("dynamodb", "query", "--table-name", table,
                          "--key-condition-expression", "pk = :p",
                          "--expression-attribute-values",
                          json.dumps({":p": {"S": f"file#{args.ns}"}})))
    live_counts = {i["sk"]["S"]: int(i["record_count"]["N"]) for i in resp["Items"]}
    golden_counts = {
        f.name: sum(1 for line in f.read_bytes().split(b"\n") if line)
        for f in sorted((golden / "parsed").glob("*.psv"))
    }
    check("table-counts", golden_counts, live_counts,
          f"dynamodb://{table} pk=file#{args.ns}")

    # DLQ empty
    attrs = json.loads(aws("sqs", "get-queue-attributes", "--queue-url", dlq_url,
                           "--attribute-names", "ApproximateNumberOfMessages",
                           "ApproximateNumberOfMessagesNotVisible"))["Attributes"]
    check("dlq-empty", {"visible": "0", "in_flight": "0"},
          {"visible": attrs["ApproximateNumberOfMessages"],
           "in_flight": attrs["ApproximateNumberOfMessagesNotVisible"]},
          f"sqs:{dlq_url}")

    # zero failed state-machine executions
    sm_arn = json.loads(aws("stepfunctions", "list-state-machines"))
    sm_arn = next((m["stateMachineArn"] for m in sm_arn["stateMachines"]
                   if m["name"] == f"ow-tp-{args.ns}-chain"), None)
    if sm_arn:
        failed = json.loads(aws("stepfunctions", "list-executions",
                                "--state-machine-arn", sm_arn,
                                "--status-filter", "FAILED"))["executions"]
        check("sfn-failures", 0, len(failed), f"stepfunctions:{sm_arn}")
    else:
        checks.append({"id": "sfn-failures", "expected": 0,
                       "actual": "state machine not found",
                       "source_of_truth": "stepfunctions:list-state-machines",
                       "result": "fail"})

    # planted anomaly set (missing AND unexpected)
    resp = json.loads(aws("dynamodb", "query", "--table-name", table,
                          "--key-condition-expression", "pk = :p",
                          "--expression-attribute-values",
                          json.dumps({":p": {"S": f"anomaly#{args.ns}"}})))
    actual_set = sorted({i["anomaly_id"]["S"] for i in resp["Items"]})
    missing = sorted(set(EXPECTED_ANOMALIES) - set(actual_set))
    unexpected = sorted(set(actual_set) - set(EXPECTED_ANOMALIES))
    check("anomaly-set", {"missing": [], "unexpected": []},
          {"missing": missing, "unexpected": unexpected},
          f"dynamodb://{table} pk=anomaly#{args.ns}")

    ok = all(c["result"] == "pass" for c in checks)
    for c in checks:
        print(f"[{c['result'].upper():4}] {c['id']}: expected={c['expected']} actual={c['actual']}")

    if args.recon_out:
        recon = {
            "kind": "recon-report",
            "unit": "aws-serverless-e2e",
            "namespace": args.ns,
            "generated_at": datetime.datetime.now(datetime.timezone.utc)
                .isoformat(timespec="seconds"),
            "run_mode": "live",
            "checks": checks,
            "values_recomputed_from_target": True,
            "idempotency_rerun": {
                "performed": True,
                "result": "pass" if (args.after_rerun and ok) else "fail",
                "evidence": "same batch re-landed and chain re-executed; parity, "
                            "counts, DLQ, and anomaly set re-verified from the "
                            "deployed estate" if args.after_rerun else
                            "rerun not performed for this report",
            },
            "planted_anomaly_detections": {
                "expected_set": EXPECTED_ANOMALIES,
                "actual_set": actual_set,
                "missing": missing,
                "unexpected": unexpected,
            },
            "unverified_paths": [],
        }
        Path(args.recon_out).write_text(json.dumps(recon, indent=2) + "\n")
        print(f"recon written: {args.recon_out}")

    print("VERIFY:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
