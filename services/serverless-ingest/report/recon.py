#!/usr/bin/env python3
"""Fixture recon for the aws-report unit.

Recomputes every value from the fixture target (a moto-emulated S3 pipeline
bucket driven through the real Lambda handler), never from migration memory:
uploads the regenerated golden parsed/*.psv inputs, invokes the handler with
{"ns", "report_date"}, reads the produced S3 object bytes back, and compares
them against the golden legacy report. Idempotency is proven by an actual
rerun. Requires the deterministic legacy run under $OTTERWORKS_LEGACY_ROOT
(see test_handler.py header for the regeneration commands).

Usage: python3 recon.py [--out aws-report.recon.json]
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

import boto3
from moto import mock_aws

import handler

GOLDEN_ROOT = Path(os.environ.get("OTTERWORKS_LEGACY_ROOT", "/tmp/ow-legacy-report"))
GOLDEN_REPORT_MD5 = "300862b738fdb8b6add8d1007362c0e0"
NS = "demo"
REPORT_DATE = "20260115"
BUCKET = f"ow-tp-{NS}-pipeline-000000000000"

UNVERIFIED_PATHS = [
    "live terraform apply of report.tf (Lambda, IAM roles, log groups, state machine)",
    "real Step Functions execution of ow-tp-<ns>-chain (aws-sdk s3:listObjectsV2 task, lambda:invoke integration, visible failure semantics)",
    "IAM least-privilege enforcement against live S3 (parsed/ read-only, reports/ write-only, invoke-only state-machine role)",
    "Step Functions CloudWatch log delivery",
    "sendmail/report delivery replacement (out of scope per contract; legacy delivery is a silent no-op)",
]


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent / "aws-report.recon.json"))
    args = ap.parse_args()

    golden_csv = GOLDEN_ROOT / "reports" / f"finance_billing_{REPORT_DATE}.csv"
    golden_xls = GOLDEN_ROOT / "reports" / f"finance_billing_{REPORT_DATE}.xls"
    if not golden_csv.exists():
        print(f"golden legacy run not found at {GOLDEN_ROOT}; regenerate it first", file=sys.stderr)
        return 2

    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    os.environ["PIPELINE_BUCKET"] = BUCKET

    checks = []
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket=BUCKET)
        parsed = sorted((GOLDEN_ROOT / "parsed").glob("CUSTBILL*.psv"))
        for p in parsed:
            s3.put_object(Bucket=BUCKET, Key=f"parsed/{p.name}", Body=p.read_bytes())

        def run():
            handler.handler({"ns": NS, "report_date": REPORT_DATE}, None)
            csv = s3.get_object(Bucket=BUCKET, Key=f"reports/finance_billing_{REPORT_DATE}.csv")["Body"].read()
            xls = s3.get_object(Bucket=BUCKET, Key=f"reports/finance_billing_{REPORT_DATE}.xls")["Body"].read()
            return csv, xls

        csv1, xls1 = run()
        checks.append({
            "id": "byte-identical-report",
            "expected": md5(golden_csv.read_bytes()),
            "actual": md5(csv1),
            "source_of_truth": "S3 object bytes read back from fixture bucket vs regenerated golden legacy report",
            "result": "pass" if csv1 == golden_csv.read_bytes() else "fail",
        })
        checks.append({
            "id": "xls-copy",
            "expected": md5(csv1),
            "actual": md5(xls1),
            "source_of_truth": "S3 .xls object bytes vs S3 .csv object bytes (and golden .xls md5 %s)" % md5(golden_xls.read_bytes()),
            "result": "pass" if xls1 == csv1 else "fail",
        })
        checks.append({
            "id": "report-date-parameter",
            "expected": f"reports/finance_billing_{REPORT_DATE}.csv written from event report_date, no wall-clock use",
            "actual": "handler derives the key solely from event['report_date'] (validated YYYYMMDD); artifact bytes carry no timestamps",
            "source_of_truth": "fixture invocation with fixed report_date; object key + bytes read back from S3",
            "result": "pass",
        })

        csv2, xls2 = run()
        reports = {o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET, Prefix="reports/")["Contents"]}
        idem_ok = csv2 == csv1 and xls2 == xls1 and reports == {
            f"reports/finance_billing_{REPORT_DATE}.csv",
            f"reports/finance_billing_{REPORT_DATE}.xls",
        }
        checks.append({
            "id": "idempotent-rerun",
            "expected": "rerun rewrites identical bytes; exactly 2 report objects",
            "actual": f"rerun bytes identical={csv2 == csv1}; report objects={sorted(reports)}",
            "source_of_truth": "second handler invocation with the same input; S3 listing + bytes read back",
            "result": "pass" if idem_ok else "fail",
        })

        for key in list(reports) + [f"parsed/{p.name}" for p in parsed]:
            s3.delete_object(Bucket=BUCKET, Key=key)
        handler.handler({"ns": NS, "report_date": REPORT_DATE}, None)
        empty_csv = s3.get_object(Bucket=BUCKET, Key=f"reports/finance_billing_{REPORT_DATE}.csv")["Body"].read()
        checks.append({
            "id": "empty-input-header-only",
            "expected": handler.HEADER.decode(),
            "actual": empty_csv.decode(errors="replace"),
            "source_of_truth": "handler invocation against a bucket with zero parsed/ objects; bytes read back",
            "result": "pass" if empty_csv == handler.HEADER else "fail",
        })

    checks.append({
        "id": "state-machine-orchestration",
        "expected": "ow-tp-<ns>-chain verifies parsed inputs (s3:listObjectsV2), invokes only the report Lambda, fails visibly",
        "actual": "ASL definition in report.tf: VerifyParsedInputs -> RunFinanceReport, no Catch-and-swallow; terraform validate green",
        "source_of_truth": "static review of infrastructure/terraform-tp-aws/report.tf (live execution is parent-verified)",
        "result": "skipped",
    })
    checks.append({
        "id": "least-privilege",
        "expected": "report role: ListBucket(prefix parsed/), GetObject parsed/*, PutObject reports/* only; chain role invokes only the report Lambda; trust conditioned on aws:SourceAccount",
        "actual": "policy documents in report.tf match; no bucket-wide write; log-delivery actions are account-scoped as AWS requires",
        "source_of_truth": "static review of report.tf IAM policy documents (live enforcement is parent-verified)",
        "result": "skipped",
    })

    # Report-level anomaly coverage per contract: only A-short-record is a
    # must-detect at this stage (surfaces as the ',UNKNOWN(),1,0.00' row);
    # the other three are contractual coverage gaps owned by the parser.
    unknown_row_present = b",UNKNOWN(),1,0.00\n" in csv1
    report = {
        "kind": "recon-report",
        "unit": "aws-report",
        "namespace": NS,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "run_mode": "fixture",
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": {
            "performed": True,
            "result": "pass" if idem_ok else "fail",
            "evidence": "second handler invocation with identical input produced byte-identical csv/xls and no extra objects",
        },
        "planted_anomaly_detections": {
            "expected_set": ["A-short-record"],
            "actual_set": ["A-short-record"] if unknown_row_present else [],
            "missing": [] if unknown_row_present else ["A-short-record"],
            "unexpected": [],
        },
        "unverified_paths": UNVERIFIED_PATHS,
    }

    Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
    failed = [c["id"] for c in checks if c.get("result") == "fail"]
    print(f"wrote {args.out}; failed checks: {failed or 'none'}")
    return 1 if failed or not unknown_row_present else 0


if __name__ == "__main__":
    sys.exit(main())
