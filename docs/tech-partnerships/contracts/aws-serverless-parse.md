# Component contract — parse (replaces `parse_custbill_fixedwidth.sh`)

Read [`aws-serverless-README.md`](aws-serverless-README.md) first (shared substrate,
event shapes, delivery rules).

## Legacy behaviour being replaced — must be reproduced byte-for-byte

`etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh` parses copybook CBCUST01
fixed-width records with `sed`/`paste`/`cut`/`awk`:

| Field | Columns (1-based) | Legacy transform |
|---|---|---|
| CUST-ID | 1-10 | trailing spaces trimmed |
| CUST-NAME | 11-40 | trailing spaces trimmed |
| BILL-DATE | 41-48 | `YYYYMMDD` → `YYYY-MM-DD` via `substr`, no validity check |
| BILL-AMT | 49-60 | implied 2 decimals: `amt+0`, then `sprintf("%.2f", amt/100)` |
| CURRENCY | 61-63 | trailing spaces trimmed |
| REC-TYPE | 64-65 | **not** trimmed (legacy only gsubs fields 1, 2, 5) |

Output: `parsed/<basename>.psv`, pipe-delimited, one line per body record, `HDR`/`TRL`
records dropped, trailing newline as `awk` emits it. No validation — bad records pass
through. Trailer count is logged, never enforced (ETL-0187, 2011, never implemented).

Match this exactly, including quirks:
- fields 3, 4 and 6 are not space-trimmed (only 1, 2, 5 are);
- a non-numeric amount becomes `0.00` (awk's `+0` coercion);
- a short record yields empty slices rather than an error;
- lines are emitted in input order.

The one improvement over legacy: the trailer count **is** compared, reported in the
state output as `trailer_match`, and surfaced by the verify harness — but a mismatch
must not change the `.psv` bytes.

## What to build

**`infrastructure/terraform-tp-aws/component-parse.tf`**

- `aws_lambda_function` named `local.lambda_names["parse"]`, handler
  `handler_parse.handler`, `python3.12`, timeout 120, memory 512,
  zip/hash from `data.archive_file.lambda`, env `local.lambda_env`.
- `aws_cloudwatch_log_group` with `var.log_retention_days`.
- Role from `data.aws_iam_policy_document.lambda_assume` + basic execution, inline
  policy allowing only: `s3:GetObject`/`s3:PutObject` on
  `"${aws_s3_bucket.ingest.arn}/*"` and
  `dynamodb:PutItem|BatchWriteItem|Query|DeleteItem` on `aws_dynamodb_table.billing.arn`.

**`services/serverless-ingest/src/handler_parse.py` + `src/custbill.py`**

- Keep the record→PSV transform pure in `custbill.py` (`parse_line`, `parse_body`,
  `trailer_count`) so it is unit-testable without AWS.
- Handler input is the pipeline input (`ns`, `bucket`, `key`, `filename`); it
  reads the landing object, writes `parsed/<ns>/<basename>.psv`, writes one DynamoDB
  item per record, and returns the Parse-state result shape from the README.
- DynamoDB items: `ns = <ns>`, `rec = <filename>#<zero-padded line no>`, plus
  `cust_id`, `cust_name`, `bill_date`, `amount` (string, as written to the `.psv`),
  `currency`, `rec_type`, `source_key`. Use `batch_writer()`; writes must be
  idempotent on re-run (same keys overwrite, so a replay cannot double-count).
- Unit tests: byte-identical output vs. a golden `.psv` fixture (check in a small
  fixture pair — legacy input + legacy output — generated with
  `make legacy-etl-gen-data NS=demo`; trim to ~5 records so the fixture stays small),
  plus quirk cases: untrimmed REC-TYPE, non-numeric amount → `0.00`, short line,
  `HDR`/`TRL` dropped, trailer mismatch reported but bytes unchanged.

## Acceptance checks

1. `terraform validate` + `fmt -check` pass.
2. `python -m pytest services/serverless-ingest/tests` passes, including the
   byte-identical fixture test.
3. `make tp-smoke` passes; golden app path untouched.
4. Evidence PR shows, for `NS=demo`, `sha256` of the pipeline's
   `parsed/demo/CUSTBILL_DEMO_00{1,2}.psv` equal to the legacy goldens
   (`7fc03e8c…9665bf`, `b576ad3d…c3c54a3`) and DynamoDB item count = 100 for `ns=demo`.
