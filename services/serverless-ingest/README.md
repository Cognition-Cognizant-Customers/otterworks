# serverless-ingest — CUSTBILL pipeline Lambdas

Python Lambda source for the tech-partnerships AWS "after" state
(`infrastructure/terraform-tp-aws/`). Semantically equivalent to the legacy
chain in `etl/legacy-extra/jobs/` (fixed-width CUSTBILL parse + finance
report), parameterized by namespace (`landing/<ns>/...`) for multi-tenant
fan-out.

- `src/custbill.py` — shared parse/aggregate logic (copybook CBCUST01)
- `src/handler_trigger.py` — SQS-fed trigger, starts a Step Functions execution per landed file
- `src/handler_parse.py` — fixed-width `.dat` → `parsed/<ns>/*.psv` + DynamoDB items
- `src/handler_report.py` — regenerates `reports/<ns>/finance_billing_<YYYYMMDD>.csv` (+ `.xls` copy)

Runtime: `python3.12`, stdlib + boto3 only (no packaging step; Terraform zips
`src/` directly).

Tests (pure-python, no AWS):

```bash
cd services/serverless-ingest && python3 -m pytest tests/ -q
```

Parity is proven end-to-end by `make aws-tp-verify NS=<ns>` — byte-for-byte
diff of the serverless outputs against the legacy scripts run locally.
