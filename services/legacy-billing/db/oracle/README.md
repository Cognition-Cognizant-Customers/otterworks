# Oracle Billing Estate (OW_BILLING)

A deliberately-legacy Oracle before-state for partner modernization demos: the
PostgreSQL legacy-billing procedures (`services/legacy-billing/db/procs/`)
ported to authentically ugly PL/SQL, plus a denormalized "data-model horror"
estate and a deterministic scaled seeder. Everything here is **additive** —
the golden app (`make infra-up && make up`, `make test`) is untouched, and
none of this starts unless you ask for it.

## Running it

```bash
make oracle-billing-up                 # Oracle Database Free on localhost:52521, PDB FREEPDB1
make oracle-billing-seed NS=dev        # SCALE=demo (default): ~25k customers, ~150k invoice lines
make oracle-billing-seed NS=dev SCALE=full   # ~250k customers, ~2M invoice lines
make oracle-billing-down               # stop and drop all data
```

`make seed-legacy` (the contract-level target per
`docs/tech-partnerships/README.md`) seeds the postgres/dynamodb/s3 estates;
the Oracle estate is seeded separately via `oracle-billing-seed`.

- Connect: `sqlplus ow_billing/ow_billing@localhost:52521/FREEPDB1`
  (SYSTEM password defaults to `Workshop123`, override with `ORACLE_BILLING_PWD`).
- First boot initializes the schema via the mounted startup script and marks
  completion by creating `FIXTURE_META`; the compose healthcheck (and
  `--wait`) only pass once that marker exists and all objects compiled VALID.
  First boot pulls + initializes Oracle Free, so allow ~10–20 minutes.
- Seeding is deterministic per namespace (the shared `legacy_common.ns_seed`,
  recorded as `seed` in the manifest): re-running a
  namespace deletes and regenerates identical data, and distinct namespaces
  coexist in the same schema. The seed manifest is written to
  `testdata/legacy/manifests/<NS>.json` per the contract in
  `docs/tech-partnerships/README.md` (row counts, md5 checksums over ordered
  PK+amount, and exact planted-anomaly counts).

## Layout

| Path | Contents |
| --- | --- |
| `setup/01_users.sql` | Creates the `OW_BILLING` schema owner |
| `schema/01_tables.sql` | Core billing tables (Oracle port of `db/schema.sql`), `CODES` lookup, audit log, `_HIST` triggers |
| `schema/02_horror.sql` | `CUSTOMER_MASTER` (155 cols), `CUSTOMER_MASTER_HIST`, `ENTITY_ATTR_VALUE`, bulk `INVOICE_HEADER`/`INVOICE_LINE` |
| `schema/03_seed_static.sql` | Deterministic baseline rows (port of `db/seed.sql`) |
| `schema/04_jobs.sql` | `DBMS_SCHEDULER` nightly jobs |
| `packages/*.sql` | `pkg_ow_util`, `pkg_plans`, `pkg_rating`, `pkg_invoicing`, `pkg_dunning` |
| `startup/00_init.sh` | Idempotent first-boot orchestrator (runs everything above, then writes `FIXTURE_META`) |
| `../../../../testdata/legacy/oracle_billing_seed.py` | Deterministic scaled seeder (`make oracle-billing-seed`) |

## Entrypoint mapping (Postgres → Oracle)

Semantics are functionally equivalent so a parity harness can compare the two
estates. Oracle set-returning entrypoints return `SYS_REFCURSOR`.

| Module | PostgreSQL | Oracle |
| --- | --- | --- |
| plans | `billing.fn_list_plans()` | `pkg_plans.fn_list_plans` |
| plans | `billing.fn_entitlement(tenant, on)` | `pkg_plans.fn_entitlement(p_tenant_id, p_on)` |
| plans | `billing.sp_change_plan(tenant, plan, eff)` | `pkg_plans.sp_change_plan(p_tenant_id, p_plan_id, p_effective_on)` |
| rating | `billing.fn_usage_rating(tenant, start, end)` | `pkg_rating.fn_usage_rating(p_tenant_id, p_period_start, p_period_end)` |
| rating | `billing.fn_usage_summary(tenant, start, end)` | `pkg_rating.fn_usage_summary(p_tenant_id, p_period_start, p_period_end)` |
| rating | `billing.sp_finalize_rating(tenant, start, end)` | `pkg_rating.sp_finalize_rating(p_tenant_id, p_period_start, p_period_end)` |
| invoicing | `billing.fn_invoice_preview(tenant, start, end)` | `pkg_invoicing.fn_invoice_preview(p_tenant_id, p_period_start, p_period_end)` |
| invoicing | `billing.fn_invoice_lines(invoice)` | `pkg_invoicing.fn_invoice_lines(p_invoice_id)` |
| invoicing | `billing.sp_issue_invoice(tenant, start, end)` | `pkg_invoicing.sp_issue_invoice(p_tenant_id, p_period_start, p_period_end)` |
| dunning | `billing.fn_overdue_accounts(as_of)` | `pkg_dunning.fn_overdue_accounts(p_as_of)` |
| dunning | `billing.sp_schedule_dunning(as_of)` | `pkg_dunning.sp_schedule_dunning(p_as_of)` |
| dunning | `billing.sp_suspend_overdue(as_of)` | `pkg_dunning.sp_suspend_overdue(p_as_of)` |

## Ugliness inventory

This estate is legacy *on purpose*. What's planted, and where:

- **Package-state globals** — `pkg_ow_util.g_call_count / g_last_module / g_last_uuid`,
  plus per-package mutable state used across calls.
- **Business rules in triggers** — `trg_sub_no_uncancel` (cancelled subscriptions
  silently stay cancelled), `trg_usage_events_check` (validation via trigger),
  derived columns set in `trg_customer_master_seq`.
- **Cursor loops instead of set-based SQL** — rating, invoicing, and dunning all
  iterate row-by-row over explicit cursors.
- **`EXECUTE IMMEDIATE` dynamic SQL** — string-assembled statements inside the
  packages where a static statement would do.
- **Autonomous-transaction "logging"** — `pkg_ow_util.log_msg` commits into
  `BILLING_AUDIT_LOG` via `PRAGMA AUTONOMOUS_TRANSACTION`.
- **`DECODE` / `NVL` everywhere**; **`(+)` outer joins** instead of ANSI joins.
- **`TO_CHAR`/`TO_DATE` gymnastics** — dates round-trip through
  `VARCHAR2(9) 'DD-MON-YY'` columns (`SIGNUP_DT`, `INVOICE_DT`, `_HIST.HIST_DT` …).
- **Sequences + triggers instead of identity columns** — every synthetic key
  (`SEQ_* + TRG_*_SEQ`).
- **`DBMS_SCHEDULER` jobs** — `JOB_NIGHTLY_DUNNING` (02:00 dunning + suspension
  sweep) and `JOB_PURGE_AUDIT_LOG` (03:30 audit retention, hardcoded 90 days).
- **`EXCEPTION WHEN OTHERS THEN NULL`** — swallowed errors in the audit-log purge
  job and in package logging paths.
- **Data-model horror** — 155-column `CUSTOMER_MASTER` with `ADDR_LINE_1..6`,
  `PHONE1..4`, `FLAG_01..20`, `UDF_01..40`; `ENTITY_ATTR_VALUE` EAV dumping
  ground; comma-separated ID lists in `VARCHAR2` (`RELATED_ACCT_IDS`,
  `PROMO_CODES_CSV`, `GL_ACCT_CSV`); magic-number `*_CD` statuses resolved
  through the generic `CODES` table; `_HIST` full-row-copy history maintained
  by triggers; the unenforced `INVOICE_LINE → INVOICE_HEADER` "foreign key".
- **Planted data-quality anomalies (seeder, exactly enumerated in the manifest)** —
  orphaned `INVOICE_LINE` rows pointing at nonexistent invoices, dirty
  `SIGNUP_DT` strings (`31-FEB-24`, `N/A`, …), and malformed CSV lists.

## Data shape

The seeder generates realistic skew: power-law tenant sizes (zipf α=1.3) with
explicit whale accounts (`VIP_YN='Y'`, six-figure balances) that hold a
disproportionate share of invoice lines, mixed-quality contact data, and a
slice of namespace-prefixed core tenants/subscriptions/usage so the packages
have live rows to operate on.
