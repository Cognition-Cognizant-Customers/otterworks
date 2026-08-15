# serverless-ingest

Lambda handlers for the AWS tech-partnerships CUSTBILL pipeline — the event-driven
replacement for the legacy batch chain in `etl/legacy-extra/`. Deployed by
`infrastructure/terraform-tp-aws` (single zip, one handler module per component).

| Module | Lambda | Replaces | Role |
|---|---|---|---|
| `src/handler_trigger.py` | `ow-tp-trigger` | `jobs/sftp_ingest_poll.ksh` | consumes SQS landing events, starts one pipeline execution per file |
| `src/handler_parse.py` | `ow-tp-parse` | `jobs/parse_custbill_fixedwidth.sh` | fixed-width (copybook CBCUST01) -> `.psv`, byte-identical to the legacy parser, plus DynamoDB records |
| `src/handler_report.py` | `ow-tp-report` | `jobs/finance_excel_report.pl` | aggregates `.psv` files into the finance report CSV (+ `.xls` copy) |
| `src/pipeline.py` | — | — | shared key layout / env conventions |

Runtime: Python 3.12, `boto3` from the Lambda runtime only (no vendored deps).

Tests are plain `pytest` against the pure-logic modules:

```bash
cd services/serverless-ingest && python -m pytest tests
```
