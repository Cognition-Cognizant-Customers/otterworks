# ETL Upgrade Guide — Addendum: the polyglot legacy batch estate

Addendum to [`../ETL_UPGRADE_GUIDE.md`](../ETL_UPGRADE_GUIDE.md). The five Python cron
scripts documented there are not the whole story: the ETL box also runs an older,
polyglot CUSTBILL batch chain (Perl / ksh / bash, 1998–2014 vintage) that lives in
`etl/legacy-extra/`. Each job below is a parallel conversion work unit for the
Databricks lakehouse migration; the deficiency list per job is the acceptance
checklist for its converted replacement.

## Job inventory

| Job | Language | Schedule (crontab) | Description |
|---|---|---|---|
| `sftp_ingest_poll.ksh` | ksh (1998, ported 2014) | every 15 min | Polls the SFTP drop dir for mainframe CUSTBILL fixed-width files, "settle" size-check, copies to `incoming/` + timestamped `archive/` |
| `parse_custbill_fixedwidth.sh` | bash + sed/awk/cut (2001) | every 15 min, offset :05 | Slices copybook-CBCUST01 fixed-width records into pipe-delimited `.psv` (implied-decimal amounts, date reformat), renames input to `.done` |
| `finance_excel_report.pl` | Perl, no modules (2004) | daily 02:10 | Totals parsed billing by currency/record-type, writes CSV renamed to `.xls`, "emails" via a sendmail pipe that silently no-ops |
| `run_all.sh` | bash (2014) | Sunday 06:00 | Chains the three jobs with `sleep 600` between stages as dependency management |

Support tooling (demo harness, not part of the legacy estate): `tools/gen_sample_data.pl`
(deterministic seeded generator, `NS` parameter → byte-identical fixed-width drops) and
`docker-compose.sftp.yml` (optional localhost-only atmoz/sftp fixture standing in for
the mainframe transfer).

## Running locally

Prerequisite: `ksh` (the ingest job is a KornShell script — authentically). On
Ubuntu/Debian: `sudo apt-get install -y ksh`.

```bash
make legacy-etl-list                       # inventory
make legacy-etl-gen-data [NS=dev]          # seed the SFTP drop dir (deterministic)
make legacy-etl-run JOB=sftp_ingest_poll
make legacy-etl-run JOB=parse_custbill_fixedwidth
make legacy-etl-run JOB=finance_excel_report
make legacy-etl-run JOB=run_all            # full chain (RUN_ALL_SLEEP=0 preset)
```

All jobs fall back to `OTTERWORKS_LEGACY_ROOT` (default `/tmp/otterworks-legacy`) when
not on the (fictional) prod/UAT hostnames. Outputs land in `$OTTERWORKS_LEGACY_ROOT/`:
`incoming/`, `parsed/*.psv`, `reports/finance_billing_*.{csv,xls}`.

## Deficiency inventory (migration acceptance checklist)

Everything wrong with the Python estate (hardcoded creds, no retries, `print()`
logging, no tests, no idempotency — see the main guide) applies here too, plus:

| Deficiency | Where | Migration acceptance criterion |
|---|---|---|
| Hardcoded absolute paths per environment (`/data/otterworks`, `/data2/otterworks_uat`) selected by hostname if-blocks | all jobs | Config externalized; no hostname branching |
| Lock files checked but **never removed** — a crashed run poisons every later run with a warning that everyone ignores | all jobs | Real mutual exclusion (or idempotent, lock-free design) |
| No file-transfer completion protocol: ingest "settles" by comparing file size twice, 1s apart; parse can read half-written files | `sftp_ingest_poll.ksh`, cron offsets | Atomic rename-into-place or manifest/checksum handshake |
| Overlapping cron schedules with no cross-job locking (ingest :00/:15 overlaps itself; finance report overlaps `analytics_daily`) | `crontab` | Orchestrator with `max_active_runs=1` and explicit dependencies |
| `sleep 600` as dependency management — downstream runs on partial data if upstream is slow | `run_all.sh` | Event/DAG-driven dependencies, not wall-clock guesses |
| Blanket error suppression: `2>/dev/null \|\| true` on nearly every command; failed copies/parses vanish silently | all jobs | Errors surfaced, retried, and alerted on |
| Fixed-width parsing via three passes of `cut` + `sed` + `awk`; no validation, bad records pass through; trailer count logged but never reconciled | `parse_custbill_fixedwidth.sh` | Schema-validated parse with trailer/record-count reconciliation |
| Implied-decimal and date handling by string surgery, no validity checks (invalid dates pass through reformatted) | parser | Typed columns with rejection/quarantine of bad records |
| "Excel" report is a CSV renamed to `.xls`; delivery is a sendmail pipe that silently does nothing on modern boxes | `finance_excel_report.pl` | Real report artifact + verified delivery |
| Bounced/stale recipients hardcoded (`jake@…` left in 2020) | `finance_excel_report.pl` | Managed distribution lists |
| Temp files with PID suffixes left behind on failure (`/tmp/cb_body.$$`) | parser | No orphaned temp state |
| No retention policy: `archive/` grows forever, inputs renamed `.done` and never purged | ingest, parser | Retention/lifecycle rules |
| Perl 5.005-style code, no `use strict`, no modules "because the proxy" | `finance_excel_report.pl` | Maintained, testable codebase |

## Golden-path note

Nothing here is wired into `make up` / `make test` / CI. The estate is host-run via the
`legacy-etl-*` Make targets only; the SFTP fixture has its own compose file and is
localhost-bound.
