# Cron Box modernization — capability preflight go/no-go

Run branch: `tp-run/modernize-20260817T043437Z`  ·  namespace: `demo`  ·  cron-search scope: Option A (documents/files metadata extended into Atlas Search)

Machine-readable manifests in this directory are the verbatim preflight output and are carried into every child prompt.

| Platform | Probes | Verified | Informational | Denied | Credential identity | Verdict |
|---|---:|---:|---:|---:|---|---|
| atlas | 8 | 8 | 0 | 0 | `unavailable` | GO |
| aws | 26 | 20 | 6 | 0 | `arn:aws:iam::[REDACTED-ACCOUNT]:user/Devin-PartnerWorkshops-Demo` | GO |
| databricks | 11 | 11 | 0 | 0 | `d***@cognition.ai` | GO |

## Denials

None. Every capability probe on all three platforms returned `verified`. The six AWS `informational` probes are the `ow-tp-` leftover scans, not capability checks: they report the pre-existing `ow-tp-portal-demo*` resources owned by a different demo.

## Notes carried into child prompts

- Databricks: existing serverless SQL warehouse `565cd2fd713738c4` (`Serverless Starter Warehouse`, `enable_serverless_compute: true`) is the only compute; no cluster creation. Catalog `ow_tp` with `bronze`/`silver`/`gold` and volume `/Volumes/ow_tp/bronze/landing` are bootstrapped by the parent.
- AWS: region `us-east-1`, caller `arn:aws:iam::[REDACTED-ACCOUNT]:user/Devin-PartnerWorkshops-Demo`. Everything prefixed `ow-tp-` and tagged `Project=otterworks-tp`; serverless/on-demand only.
- AWS leftovers at preflight time: `ow-tp-portal-demo*` S3/DynamoDB/Lambda/EventBridge resources belong to a different demo and were deliberately left in place; they are excluded from this run's teardown scan.
- Atlas: free-tier M0 in the existing project; database `ow_tp_demo` with collections `documents` and `files` bootstrapped by the parent. Search indexes are owned by the cron-search unit.
