# Demo Runbook — Databricks: cron/Perl/ksh ETL → Lakehouse

**Duration:** ~30 minutes standalone.
**Story:** the OtterWorks ETL box runs two generations of batch: five Python
cron scripts (`etl/`, 2014-vintage — see `etl/ETL_UPGRADE_GUIDE.md`) and an
older polyglot CUSTBILL chain (`etl/legacy-extra/`, Perl/ksh/bash, 1998–2014 —
see `etl/legacy-extra/ETL_UPGRADE_GUIDE_ADDENDUM.md`). We assess the estate,
fan out one Devin child session per script, and land everything on a
Databricks lakehouse (bronze/silver/gold + Unity Catalog), reconciling every
converted job against the legacy outputs.

All CUSTBILL numbers below are deterministic for `NS=demo` (same NS ⇒
byte-identical files), so on-screen output matches this runbook exactly.

## Pre-demo setup

```bash
sudo apt-get install -y ksh                       # the ingest job is real KornShell
export OTTERWORKS_LEGACY_ROOT=/tmp/otterworks-legacy-demo
make legacy-etl-gen-data NS=demo                  # deterministic CUSTBILL drops
```

Expected: `wrote .../sftp-drop/upload/CUSTBILL_DEMO_001.dat (50 records)` and
`CUSTBILL_DEMO_002.dat (50 records)`, 3,430 bytes each.

Optional (adds 2 min of authentic pain): `make legacy-sftp-up` for the
localhost SFTP fixture standing in for the mainframe transfer.

## Beat 1 — Assessment: tour the estate (0:00–0:10)

### 1a. The crontab is the architecture diagram (3 min)

Open `etl/legacy-extra/crontab` and read the comments verbatim — they are the
assessment:

- ingest every 15 min, parse offset by :05 — "if ingest is still copying when
  parse starts, parse reads a half-written file. known issue."
- finance report at 02:10 overlapping `analytics_daily` at 02:00 — "finance
  asked for 06:00 in 2018, ticket lost."
- `run_all.sh` Sunday 06:00 — "the 'just rerun everything' entry Jake added
  before a vacation in 2019."

### 1b. The code (4 min)

```bash
make legacy-etl-list
```

| Job | Language | Sin highlights |
|---|---|---|
| `sftp_ingest_poll.ksh` | ksh (1998) | hostname if-blocks, size-compared-twice "settle" protocol, lock files never removed |
| `parse_custbill_fixedwidth.sh` | bash+sed/awk/cut (2001) | three-pass fixed-width slicing, implied decimals by string surgery, trailer count logged but never reconciled |
| `finance_excel_report.pl` | Perl 5.005, no modules (2004) | CSV renamed to `.xls`, sendmail pipe that silently no-ops, `jake@…` still on the distribution list |
| `run_all.sh` | bash (2014) | `sleep 600` as dependency management; ends with "run_all done (probably)" |

Also open `etl/legacy-extra/ops/RESTART_PROCEDURE.doc.txt` — the tribal-knowledge
restart runbook — and the deficiency table in
`etl/legacy-extra/ETL_UPGRADE_GUIDE_ADDENDUM.md`, which doubles as the
migration acceptance checklist. Mention the five Python cron scripts from the
main guide (`analytics_daily`, `audit_archive_weekly`, `search_reindex_weekly`,
`storage_cleanup_daily`, `user_activity_daily`) as the second wave of the same
program.

### 1c. Run the chain live (3 min)

```bash
make legacy-etl-run JOB=run_all      # RUN_ALL_SLEEP=0 preset; ~6 s
```

Expected on screen (NS=demo):

```
ingested CUSTBILL_DEMO_001.dat (3430 bytes)
ingested CUSTBILL_DEMO_002.dat (3430 bytes)
parsed CUSTBILL_DEMO_001: 50 records (trailer says 50)
parsed CUSTBILL_DEMO_002: 50 records (trailer says 50)
wrote .../reports/finance_billing_YYYYMMDD.xls
run_all done (probably)
```

Show the legacy outputs — these are the reconciliation baselines:

```bash
wc -l $OTTERWORKS_LEGACY_ROOT/parsed/*.psv      # 50 + 50 = 100 rows
cat $OTTERWORKS_LEGACY_ROOT/reports/finance_billing_*.csv
```

Expected report (deterministic for NS=demo):

```
Currency,RecordType,RecordCount,TotalAmount
EUR,INVOICE,22,101554.41
EUR,CREDIT,6,33375.97
GBP,INVOICE,32,183113.58
GBP,CREDIT,5,28454.59
USD,INVOICE,28,130502.15
USD,CREDIT,7,33390.44
```

## Beat 2 — Target architecture (0:10–0:15)

Bronze/silver/gold on Delta, governed by Unity Catalog:

| Layer | Content | Replaces |
|---|---|---|
| **Bronze** | Raw CUSTBILL fixed-width files landed via Auto Loader from S3 (mainframe drop → S3 transfer family replaces the SFTP poll) | `sftp_ingest_poll.ksh` + `archive/` |
| **Silver** | Schema-validated, typed CUSTBILL records (decimal amounts, real dates, trailer-count reconciliation enforced; bad records quarantined to a rescue table) | `parse_custbill_fixedwidth.sh` |
| **Gold** | Currency × record-type billing aggregates as a Delta table + dashboard/scheduled export | `finance_excel_report.pl` |
| **Workflows/DLT** | Dependency-driven pipeline, `max_active_runs=1`, retries, alerting | crontab overlaps + `sleep 600` |
| **Unity Catalog** | `otterworks.custbill_{bronze,silver,gold}` with lineage, grants, audit | `/var/log/etl/*.log` and prayer |

The same layering absorbs the five Python scripts (their SQS/DynamoDB/S3
sources land in bronze; pandas aggregations become Spark SQL in silver/gold) —
the target-state mapping in `etl/ETL_UPGRADE_GUIDE.md` §Script-to-DAG carries
over with DAGs → Workflows.

## Beat 3 — One-child-per-script fan-out (0:15–0:22)

Show the parallel conversion plan. Each legacy job is an independent work
unit with its own acceptance checklist (the deficiency table rows that name
it):

| Child session | Converts | Acceptance criteria (from the addendum) |
|---|---|---|
| `dbx-ingest` | `sftp_ingest_poll.ksh` → Auto Loader ingest | atomic landing (no half-written reads), no hostname branching, real mutual exclusion |
| `dbx-parse` | `parse_custbill_fixedwidth.sh` → silver DLT table | schema-validated parse, trailer/record-count reconciliation, bad-record quarantine |
| `dbx-finance` | `finance_excel_report.pl` → gold aggregate + delivery | real artifact, verified delivery, managed recipients |
| `dbx-orchestrate` | `crontab` + `run_all.sh` → Databricks Workflow | event/DAG dependencies, `max_active_runs=1`, alerting |
| `dbx-python-wave` (×5) | the five Python cron scripts | per `etl/ETL_UPGRADE_GUIDE.md` migration axes 1–9 |

Talking points: the generator is deterministic per namespace, so every child
can regenerate identical test input (`make legacy-etl-gen-data NS=<ns>` with
its own `OTTERWORKS_LEGACY_ROOT`) without coordinating; the parent session
only reviews recon reports.

## Beat 4 — Reconciliation vs legacy outputs (0:22–0:28)

Recon contract: for the same input namespace, the lakehouse must reproduce
the legacy outputs exactly.

1. **Row parity (silver vs `.psv`)** — 100 records for NS=demo; every silver
   row must match a `.psv` row on all 6 fields (id, name, date, amount,
   currency, record-type). The legacy independent recompute is one awk
   one-liner (from `.agents/skills/legacy-etl-demo/SKILL.md`):

   ```bash
   cat $OTTERWORKS_LEGACY_ROOT/parsed/*.psv | awk -F'|' \
     '{k=$5","(($6=="01")?"INVOICE":"CREDIT"); c[k]++; t[k]+=$4}
      END{for(k in c) printf "%s,%d,%.2f\n",k,c[k],t[k]}'
   ```

2. **Aggregate parity (gold vs report)** — the gold table grouped by
   currency × record-type must equal the six rows of
   `finance_billing_*.csv` above, to the cent.

3. **Defect ledger** — the converted pipeline must *reject* what the legacy
   one silently passed: invalid dates, bad implied decimals, trailer
   mismatches. Zero rejects on the clean demo seed; then corrupt a record
   live (`sed` a letter into an amount field of a fresh drop) and show the
   legacy parser passing it while silver quarantines it.

## Beat 5 — Wrap (0:28–0:30)

- 1998-vintage ksh → governed Delta pipeline, with the deficiency inventory
  as a checkable acceptance list, not vibes.
- Deterministic seeds make recon exact: same NS, same bytes, same six report
  rows.
- Segue to the combined demo: `runbook-modernize-otterworks.md`.

## Cleanup

```bash
make legacy-sftp-down                    # if the SFTP fixture was started
rm -rf $OTTERWORKS_LEGACY_ROOT           # the whole estate is under this root
```
