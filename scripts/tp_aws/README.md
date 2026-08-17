# `scripts/tp_aws` — cron-cleanup live validation (parent-owned)

The cron-cleanup unit replaces `etl/scripts/storage_cleanup_daily.py` with the
event-driven `ow-tp-orphan-quarantine` path. These scripts belong to the
parent's single live validation window; a child session never runs them against
real AWS.

Order, after `terraform apply` of `infrastructure/terraform/tp-cronbox/`:

```bash
# 1. load the deterministic estate (72 referenced objects, 4 object-only
#    orphans, 1 reverse metadata orphan) — shapes come from the immutable
#    golden baseline, never from the target
uv run --no-project --with boto3==1.35.99 python3 \
    scripts/tp_aws/seed_cron_cleanup_estate.py --ns demo

# 2. reconcile: recompute every value from the deployed AWS APIs
uv run --no-project --with boto3==1.35.99 python3 \
    scripts/tp_aws/cron_cleanup_recon.py --ns demo \
    --out docs/tech-partnerships/recon/cron-cleanup-demo.recon.json

# 3. gate on the schema
make tp-validate-recon FILE=docs/tech-partnerships/recon/cron-cleanup-demo.recon.json
```

`cron_cleanup_recon.py` exits non-zero when any check fails or the planted
anomaly sets drift (`missing` or `unexpected` non-empty). It reads S3 listings
and object bodies, DynamoDB items, and the notification / lifecycle /
EventBridge / Lambda configuration through the AWS APIs — never Terraform
state, a log line, or the local fixture estate.

Writes it performs, all required to exercise the event-driven path: a probe
object pair under `files/<ns>/recon-<run-id>/` (one orphan, one with a
multibyte/space key), a replayed invocation of the same event, and one
on-demand `{"mode": "reconcile"}` sweep. Probe objects, their quarantine
copies, and their audit rows are deleted before exit. `--skip-probe` drops the
live event path and records the omission in `unverified_paths`.

Local fixture proof for the handler itself lives in
`scripts/tp_aws/tests/test_cron_cleanup_fixture.py` and runs against
LocalStack (`make infra-up`), never real AWS.

## Child self-check: `--mode fixture`

The same recon runs against the LocalStack estate as the child's committed
evidence. It uses `-fixture`-suffixed stand-in buckets and tables, seeds and
resets that estate itself, and drives the packaged handler in-process because
the fixture has no EventBridge or Lambda:

```bash
make infra-up
uv run --no-project --with boto3==1.35.99 python3 \
    scripts/tp_aws/cron_cleanup_recon.py --mode fixture --ns demo \
    --out docs/tech-partnerships/recon/cron-cleanup-demo.fixture.recon.json
make tp-validate-recon FILE=docs/tech-partnerships/recon/cron-cleanup-demo.fixture.recon.json
```

Every deployed-only fact — bucket EventBridge notification, quarantine
lifecycle expiry, rule schedule/state/pattern, EventBridge target and Lambda
DLQ, provisioned concurrency, resource tags — is reported `skipped` with its
reason in `unverified_paths`, along with the fact that the values come from the
fixture rather than the deployed target. It is evidence about the logic; only
the parent's `--mode live` run satisfies the contract's read-back requirement.

## Cron-archive live validation

The parent-owned archive recon re-anchors only the live TTL horizon to a
whole-second wall-clock reference one hour in the future; fixture mode keeps the
frozen-date corpus. The compared identities, payloads, and expected sets remain
from the immutable golden baseline. After the two reconciliation sweeps, live
mode seeds two clearly marked TTL-removal probes (ASCII and multibyte payloads)
and waits up to ten minutes for both DynamoDB TTL removals to appear in S3 and
disappear from DynamoDB. A timeout is reported as skipped rather than failed;
`--skip-ttl-probe` records an explicit omission. Unless `--keep` is supplied,
the run removes its corpus, probes, and archive objects without touching
pre-existing objects or records.
