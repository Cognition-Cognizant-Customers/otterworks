baseline: legacy output

# Recon: `analytics_daily.py` -> `ow_tp_analytics_daily` (ns=`demo`, catalog=`ow_tp`)

- verdict: **green with 1 documented legacy-deficiency deviation (check 2)**
- baseline: captured legacy run at `/home/ubuntu/tp-golden/python/analytics_daily` (tier 1, real legacy output)
- converted output: `ow_tp.bronze.analytics_events_raw` / `ow_tp.silver.analytics_events` / `ow_tp.silver.analytics_events_rejects` / `ow_tp.gold.analytics_daily_summary`
- extract transport used for this evidence: SQL staging table `bronze.analytics_daily_stage`. The documented landing-volume upload path (`dbx.upload` into `/Volumes/ow_tp/bronze/landing/...`) is **UNVERIFIED**: the demo PAT lacks the `files` scope and every attempt returns `403: {"error_code":403,"message":"Provided access token does not have required scopes: files"}`. The staging table is an evidence-only substitute, not the production transport; the extract statement downstream of the source relation is byte-identical, and no check was weakened to accommodate it.
- converted counts: {'bronze': 5147, 'silver': 5147, 'rejects': 0, 'gold_rows': 4937, 'gold_events': 5147}

## 1. Event-count parity, zero silent drops — **PASS**

```text
total events: baseline=5147 = converted=5147
silver + rejects vs bronze: baseline=5147 = converted=5147
rejects without a reason: baseline=0 = converted=0
gold event_count sum vs silver rows: baseline=5147 = converted=5147
reject reasons: none
```

## 2. Aggregate parity on (summary_date, hour, user_id, document_id, event_type) — **DEVIATION**

```text
legacy aggregate carries no summary_date/document_id, one synthetic hour bucket '00' and a single user_id 'unknown'
group count at (hour, event_type): baseline=10 != converted=240
distinct hours: baseline=['00'] != converted=['00', '01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12', '13', '14', '15', '16', '17', '18', '19', '20', '21', '22', '23']
distinct user_id count: baseline=1 != converted=50
distinct summary_date count: baseline=0 != converted=3
exact group equality: baseline=True != converted=False
total events (dimension-free): baseline=5147 = converted=5147
per event_type totals: baseline={'quota.warning': 529, 'user.logout': 487, 'file.trashed': 540, 'document.shared': 531, 'document.created': 518, 'file.restored': 496, 'file.uploaded': 483, 'user.login': 481, 'file.downloaded': 546, 'document.updated': 536} = converted={'file.uploaded': 483, 'quota.warning': 529, 'document.created': 518, 'file.downloaded': 546, 'file.trashed': 540, 'user.logout': 487, 'file.restored': 496, 'document.shared': 531, 'document.updated': 536, 'user.login': 481}
deviation: legacy field-name defect surfaced, converted output correct -- the legacy script reads eventType/timestamp/ownerId while the events carry event_type/occurred_at/user_id, so it collapsed all 5147 events into 10 groups at hour='00'/user_id='unknown' with 0 dates; the converted job attributes them across 240 groups/24 hours/50 users/3 dates. Every dimension the baseline does carry is compared exactly above and matches.
```

## 3. Retry deficiency retired: a failing/empty source fails the run — **PASS**

```text
legacy behaviour being retired, captured at /home/ubuntu/tp-golden/python/analytics_daily/zero_event (legacy run with its sources removed): [2026-08-15 23:00:21] ERROR: Too many SQS failures, giving up / [2026-08-15 23:00:21] Extracted 0 events from SQS / [2026-08-15 23:00:21] Extracted 0 events from DynamoDB for 2026-08-15 / [2026-08-15 23:00:21] WARNING: No events found, exiting -> exit 0
legacy exit code on a zero-event extract: baseline='0' = converted='0'
unreachable source: run failed as required (DatabricksError: statement failed (FAILED): [TABLE_OR_VIEW_NOT_FOUND] The table or view `ow_tp`.`bronze`.`analytics_daily_stage_missing` cannot be found. Verify the spelling and correctness of the schema and catalog.
)
empty source: run failed as required (ZeroEventExtract: extract produced 0 events for ns=recon_probe_demo from ow_tp.bronze.analytics_daily_stage; failing the run instead of writing an empty summary)
gold rows written for the failed probe ns recon_probe_demo: baseline=0 = converted=0
```

## 4. Idempotency: a re-run replaces, never appends — **PASS**

```text
re-run source: ow_tp.bronze.analytics_daily_stage
counts: baseline={'bronze': 5147, 'silver': 5147, 'rejects': 0, 'gold_rows': 4937, 'gold_events': 5147} = converted={'bronze': 5147, 'silver': 5147, 'rejects': 0, 'gold_rows': 4937, 'gold_events': 5147}
gold fingerprint: baseline='f3a42b884a639d276b2b6d8a04e4229a' = converted='f3a42b884a639d276b2b6d8a04e4229a'
```

## 5. Baseline provenance stated verbatim — **PASS**

```text
report line 1: 'baseline: legacy output'
captured legacy run: /home/ubuntu/tp-golden/python/analytics_daily (exit 0)
```
