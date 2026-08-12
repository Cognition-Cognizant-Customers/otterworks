# Stored-Procedure Estate — `services/legacy-billing/db`

Scope: every procedure/function in `services/legacy-billing/db/procs/*.sql`, its table
reads/writes, its callers (the Flask app in `services/legacy-billing/app/app.py` and other
procedures), the actual module boundaries implied by table ownership and call graph, and a
line-level split of business rules vs plumbing. Schema: `services/legacy-billing/db/schema.sql`
(all tables in the `billing` schema; `billing.` prefix omitted below).

## 1. Procedure and function inventory

| Routine | File (lines) | Kind | Signature | Reads | Writes | Called from app (`app.py`) | Called from other procs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `fn_list_plans` | plans.sql (1–16) | function (sql) | `() → TABLE(plan_id, code, tier, monthly_fee, included_units, overage_rate)` | `plans` | — | `GET /` (app.py:56), `GET /plans` (app.py:61) | — |
| `fn_entitlement` | plans.sql (18–40) | function (sql) | `(p_tenant_id uuid, p_on date) → TABLE(tenant_id, plan_code, tier, monthly_fee, included_units, subscription_status, effective_on)` | `tenants`, `subscriptions`, `plans` | — | `GET /plans/<tenant_id>/entitlement` (app.py:66) | — |
| `sp_change_plan` | plans.sql (42–64) | procedure (plpgsql) | `(p_tenant_id uuid, p_plan_id uuid, p_effective_on date)` | `subscriptions` | `subscriptions` (UPDATE + INSERT) | `POST /plans/<tenant_id>/change` (app.py:74) | — |
| `fn_usage_rating` | rating.sql (1–77) | function (plpgsql) | `(p_tenant_id uuid, p_period_start date, p_period_end date) → TABLE(tenant_id, period_start, period_end, used_units, quota_units, rollover_units, billable_units, first_tier_units, second_tier_units, overage_amount)` | `subscriptions`, `plans`, `usage_events`, `rating_results`, `rating_periods` | — | `POST /api/rating/preview` (app.py:84) | `sp_finalize_rating` (rating.sql:121), `fn_invoice_preview` (invoicing.sql:32) |
| `fn_usage_summary` | rating.sql (79–93) | function (sql) | `(p_tenant_id uuid, p_period_start date, p_period_end date) → TABLE(kind, event_count, units)` | `usage_events` | — | **not called** (dead from the app's perspective) | — |
| `sp_finalize_rating` | rating.sql (95–139) | procedure (plpgsql) | `(p_tenant_id uuid, p_period_start date, p_period_end date)` | `subscriptions` (+ everything `fn_usage_rating` reads) | `rating_periods` (upsert), `rating_results` (upsert) | `POST /api/rating/finalize` (app.py:93) | `sp_issue_invoice` (invoicing.sql:93) |
| `fn_invoice_preview` | invoicing.sql (1–60) | function (plpgsql) | `(p_tenant_id uuid, p_period_start date, p_period_end date) → TABLE(line_no, line_type, description, amount, tax_amount, credit_applied, total)` | `subscriptions`, `plans`, `credit_notes`, `tenants` (+ `fn_usage_rating` reads) | — | `GET /api/invoices/<tenant_id>/preview` (app.py:102) | `sp_issue_invoice` (invoicing.sql:104) |
| `fn_invoice_lines` | invoicing.sql (62–75) | function (sql) | `(p_invoice_id uuid) → TABLE(line_no, line_type, description, amount)` | `invoice_lines` | — | `GET /api/invoices/<invoice_id>/lines` (app.py:127) | — |
| `sp_issue_invoice` | invoicing.sql (77–140) | procedure (plpgsql) | `(p_tenant_id uuid, p_period_start date, p_period_end date)` | `credit_notes` (+ callee reads) | `invoices` (upsert + UPDATE), `invoice_lines` (DELETE + INSERT), `credit_notes` (UPDATE) | `POST /api/invoices/<tenant_id>/issue` (app.py:114) | — |
| `fn_overdue_accounts` | dunning.sql (1–18) | function (sql) | `(p_as_of date) → TABLE(tenant_id, invoice_id, total, days_overdue, tenant_status)` | `invoices`, `tenants` | — | `GET /api/dunning/overdue` (app.py:132) | — |
| `sp_schedule_dunning` | dunning.sql (20–51) | procedure (plpgsql) | `(p_as_of date)` | `invoices`, `dunning_attempts` | `dunning_attempts` (INSERT, ON CONFLICT DO NOTHING) | `POST /api/dunning/schedule` (app.py:140) | — |
| `sp_suspend_overdue` | dunning.sql (53–88) | procedure (plpgsql) | `(p_as_of date)` | `invoices`, `tenants`, `notifications` | `tenants` (UPDATE), `subscriptions` (UPDATE), `notifications` (INSERT) | `POST /api/dunning/suspend` (app.py:146) | — |

Notes:
- `fn_usage_summary` has no caller in the app or other procs, but it is **not** dead: the
  parity suite calls it directly (`procs/scenarios/rating/007.yaml`,
  `procs/transcripts/rating/RATING-007.json`), so extraction must keep it.
- Most write procedures derive primary keys deterministically via
  `md5(...)::uuid` and rely on `ON CONFLICT` / `DO NOTHING` for idempotency; any extraction
  must preserve those identity schemes or invoice/rating/dunning replays will duplicate rows.
  Exception: `sp_change_plan`'s subscription INSERT (plans.sql:57–62) has **no** `ON CONFLICT`
  clause, so replaying the same plan change raises a primary-key violation.

## 2. Module boundaries by table ownership and call graph

Ownership assigned by who **writes** a table (writer owns), then by who reads it.

| Table | Written by (module) | Read by (modules) | Ownership verdict |
| --- | --- | --- | --- |
| `plans` | nobody (seed-only) | plans, rating, invoicing | **Shared reference data** — read by 3 modules |
| `tenants` | **dunning** (`sp_suspend_overdue`) | plans, invoicing, dunning | **Shared, cross-module write** — dunning mutates a table plans/invoicing depend on (`tax_exempt`, `status`) |
| `subscriptions` | **plans** (`sp_change_plan`) **and dunning** (`sp_suspend_overdue`) | plans, rating, invoicing, dunning | **Worst seam: two writers in different modules**, four readers. `suspended_on` is written by dunning and consumed by rating's proration rule |
| `usage_events` | nobody (ingested externally) | rating | rating-owned read model |
| `rating_periods` | rating (`sp_finalize_rating`) | rating | rating-owned |
| `rating_results` | rating (`sp_finalize_rating`) | rating (`fn_usage_rating` rollover lookback) | rating-owned, but see temporal self-dependency below |
| `invoices` | invoicing (`sp_issue_invoice`) | invoicing, **dunning** (all three dunning routines key off `invoices.status = 'overdue'`) | invoicing-owned, **dunning reads it** (nothing in the estate sets `status='overdue'` — that transition happens outside these procs) |
| `invoice_lines` | invoicing | invoicing | invoicing-owned |
| `credit_notes` | invoicing (`sp_issue_invoice` draws down `remaining_amount`) | invoicing | invoicing-owned (issuance of credit notes is external) |
| `dunning_attempts` | dunning | dunning | dunning-owned |
| `notifications` | dunning | dunning | dunning-owned |

### Cross-procedure calls (the call-graph seams)

| Caller | Callee | Cross-module? | Why it hurts |
| --- | --- | --- | --- |
| `sp_finalize_rating` (rating.sql:121) | `fn_usage_rating` | no (intra-rating) | benign, but note the callee re-reads `rating_results` — finalize output depends on prior finalized periods |
| `fn_invoice_preview` (invoicing.sql:32) | `fn_usage_rating` | **yes: invoicing → rating** | invoice math embeds the full rating algorithm; extracting rating changes invoice previews |
| `sp_issue_invoice` (invoicing.sql:93) | `sp_finalize_rating` | **yes: invoicing → rating** | issuing an invoice *persists* rating state (`rating_periods`, `rating_results`) as a side effect — a hidden write into another module's tables |
| `sp_issue_invoice` (invoicing.sql:104) | `fn_invoice_preview` | no (intra-invoicing) | preview is the pricing source of truth; issue re-executes it (which re-executes `fn_usage_rating` a second time in the same call, after finalize has already run it once) |

### Where the boundaries actually fall

- **plans** (catalog + subscription lifecycle): owns `subscriptions` writes via
  `sp_change_plan`; reads `plans`, `tenants`.
- **rating** (usage → money): owns `rating_periods`/`rating_results`; reads
  `usage_events`, `subscriptions`, `plans`. Its rollover rule makes each period's result a
  function of the previous 3 months of *persisted* results — extraction must migrate history
  or parity breaks.
- **invoicing**: owns `invoices`/`invoice_lines`/`credit_notes` drawdown, but is **not
  independent**: it calls into rating twice (compute + persist) and reads `tenants.tax_exempt`.
- **dunning**: owns `dunning_attempts`/`notifications`, but reaches across every boundary:
  reads invoicing's `invoices`, and writes `tenants.status` and
  `subscriptions.{status,suspended_on}` — the latter feeds back into rating's proration
  (rating.sql:62–72). This dunning→subscriptions→rating feedback loop is the most dangerous
  seam in the estate.
- File layout matches the module split *except* for these seams: the shared tables
  (`tenants`, `subscriptions`, `plans`, `invoices`) and the four cross-calls above are what
  couple the files, not the file boundaries themselves.

## 3. Business rules vs plumbing, per routine

"Rule" = a line encoding pricing/lifecycle/policy semantics; "plumbing" = parameter binding,
cursor/record mechanics, deterministic-ID construction, result assembly, upsert scaffolding.

### plans.sql

| Routine | Business-rule lines | Rule | Plumbing lines | Plumbing |
| --- | --- | --- | --- | --- |
| `fn_list_plans` | 14–15 | Only `active` plans are sellable; catalog ordered by `monthly_fee, code` | 1–13, 16 | signature, RETURNS TABLE, column projection |
| `fn_entitlement` | 31, 36–39 | Effective date = `GREATEST(starts_on, p_on)`; entitlement = subscription covering `p_on` (`starts_on <= p_on`, open or future `ends_on`); ties resolved to **latest** `starts_on` (`ORDER BY starts_on DESC LIMIT 1`) | 1–30, 32–35, 40 | signature, joins/projection |
| `sp_change_plan` | 51–52, 54–55, 61 | Old subscription ends the day **before** the effective date (`ends_on = p_effective_on - 1`); cancelled subscriptions keep `cancelled`, everything else forced `active`; only open subs started before the effective date are closed; new sub starts `active` | 42–50, 53, 56–60, 62–64 | signature, UPDATE/INSERT scaffolding, md5 identity (line 60) |

### rating.sql

| Routine | Business-rule lines | Rule | Plumbing lines | Plumbing |
| --- | --- | --- | --- | --- |
| `fn_usage_rating` | 34–36 | Subscription selection: any sub overlapping the period, latest `starts_on` wins | 1–30, 32–33, 37, 39–42, 74–77 | signature, DECLARE block, SELECT-INTO mechanics, RETURN QUERY assembly |
| | 46 | Usage attributed by `occurred_at::date` within the period (date, not timestamp, boundary) | 43–45 | aggregation plumbing |
| | 48, 52–54, 56 | Rollover = sum of `rollover_units` from finalized periods in the prior **3 months**, capped at **2× included_units** (cap applied twice: in SQL at line 48 and again at line 56) | 49–51 | join plumbing |
| | 57 | `billable = max(used − rollover − included, 0)` | | |
| | 58–60 | Two-tier overage: first **101** units at `overage_rate`, remainder at **1.5×** `overage_rate`; amount rounded to 2 dp | | |
| | 62–72 | Suspension proration: if the sub was suspended mid-period, billable units **and** amount are scaled by `(period_end − suspended_on + 1) / (period length)` — i.e. charged for the suspended tail, not the active head | | |
| `fn_usage_summary` | 90–92 | Same date-boundary attribution; grouped by `kind` | 79–89, 93 | signature/aggregation |
| `sp_finalize_rating` | 110–112 | Same overlapping-subscription selection as `fn_usage_rating` | 95–107, 113, 120–122 | signature, DECLARE, SELECT-INTO |
| | 103, 117–118 | Period identity is `md5(tenant || period_start)` — one period per (tenant, start); re-finalizing **extends/overwrites** `period_end` | 115–116 | INSERT scaffolding |
| | 129 | **Stored `rollover_units` = `GREATEST(quota − used, 0)`** — unused quota this period, *not* the `rollover_units` returned by `fn_usage_rating`. This is what next period's rollover lookback actually consumes; the naming mismatch is a rule, not an accident to "fix" silently | 123–128, 130–137 | md5 identity, upsert column list |

### invoicing.sql

| Routine | Business-rule lines | Rule | Plumbing lines | Plumbing |
| --- | --- | --- | --- | --- |
| `fn_invoice_preview` | 26–29 | Plan resolved from the subscription overlapping the period (latest `starts_on`) | 1–25, 30–33, 45 | signature, DECLARE, SELECT-INTO, RETURN QUERY |
| | 34–37 | Available credit = sum of all `credit_notes.remaining_amount > 0` (no per-invoice cap at this stage) | | |
| | 39–43 | Tax: 0 for `tax_exempt` tenants, else **8.25%** of (monthly fee + overage) | | |
| | 46–47, 49–50 | Line 1 = plan fee (rounded), line 2 = usage overage (rounded) | 48, 51, 53, 55 | UNION ALL glue |
| | 52, 54 | Tax split into two equal lines — "regional" and "local" — each `v_tax / 2`, **unrounded** (rounding differences vs a single tax line are semantically load-bearing) | | |
| | 56–58 | Credit line: applied credit capped at the rounded invoice total (fee + overage + tax); shown as negative `total` | | |
| `fn_invoice_lines` | 74 | Lines returned in `line_no` order | 62–73, 75 | signature, projection |
| `sp_issue_invoice` | 93 | Issuing an invoice **first finalizes rating** for the period (cross-module side effect) | 77–92 | signature, DECLARE, md5 identities (85–86: period id ⇒ invoice id) |
| | 100 | Re-issuing flips status back to `'issued'` (idempotent re-issue, header amounts recomputed) | 94–99 | INSERT scaffolding |
| | 102 | Lines are fully **replaced** on re-issue (DELETE then re-INSERT) | 103–110, 113 | loop/insert mechanics |
| | 111–112 | Credit lines are stored at their (negative) `total`; all other lines at `amount` | | |
| | 114–120 | Header aggregation policy: subtotal = plan+usage, tax = tax lines, credit taken from `credit_applied` (each addend rounded before summing) | | |
| | 122–125 | `total = subtotal + tax − credit` (rounded) | | |
| | 127–138 | Credit drawdown: notes consumed oldest-first (`ORDER BY issued_on, id`), each floored at 0; the running remainder is decremented by the note's **pre-update** `remaining_amount` (line 137 reads `v_line.remaining_amount` after the UPDATE at 134–136, so the sequencing itself is a rule to preserve) | 132–133 | loop mechanics |

### dunning.sql

| Routine | Business-rule lines | Rule | Plumbing lines | Plumbing |
| --- | --- | --- | --- | --- |
| `fn_overdue_accounts` | 11, 15–17 | Overdue = `status = 'overdue'` **and** issued strictly before `p_as_of`; `days_overdue = p_as_of − issued date`; ordered oldest-first | 1–10, 12–14, 18 | signature, join/projection |
| `sp_schedule_dunning` | 31–32 | Every `'overdue'` invoice gets an attempt, processed oldest-first | 20–30, 33 | signature, DECLARE, cursor loop |
| | 34–36 | Attempt number = max existing attempt + 1 per invoice | | |
| | 37–42 | Weekend shift: Saturday → +2 days, Sunday → +1 day (attempts always land on a business day) | | |
| | 48 | `(invoice_id, attempt_no)` conflict clause is effectively dead: `attempt_no` is recomputed as max+1 each run, so re-running the scheduler on the same day inserts an **additional** attempt per overdue invoice rather than skipping | 43–47, 49–51 | INSERT scaffolding, md5 identity |
| `sp_suspend_overdue` | 60–63 | Suspension threshold: invoice overdue **and** issued ≥ **14 days** before `p_as_of` | 53–59, 64 | signature, DECLARE, cursor loop |
| | 65–68 | Only currently-`active` tenants are suspended (already-suspended tenants untouched — no duplicate notifications, no re-stamping `suspended_on`) | | |
| | 69–71 | Tenant status → `'suspended'` | | |
| | 72–75 | All **active** subscriptions → `'suspended'` with `suspended_on = p_as_of` (this is the value rating's proration consumes) | | |
| | 79–84 | Suspension notification is idempotent per (tenant, kind, day) | 76–78, 85–88 | INSERT scaffolding, md5 identity |

## 4. Seam summary (what will hurt during extraction)

1. **`subscriptions` has two writers** (`plans.sp_change_plan`, `dunning.sp_suspend_overdue`)
   and four reader modules; `suspended_on` is a dunning-written field consumed by rating.
2. **invoicing → rating calls, twice per issue**: `sp_issue_invoice` persists rating state via
   `sp_finalize_rating`, then recomputes it via `fn_invoice_preview → fn_usage_rating`.
3. **rating's temporal self-dependency**: `fn_usage_rating` reads prior `rating_results`
   (3-month rollover window), and what `sp_finalize_rating` stores under `rollover_units`
   is unused quota, not the function's rollover output.
4. **`tenants` is dunning-written but plans/invoicing-read** (`status`, `tax_exempt`).
5. **`invoices.status = 'overdue'` is set by nothing in this estate** — dunning depends on an
   external state transition.
6. **Deterministic md5 identities + ON CONFLICT** are the idempotency contract for most
   write paths (periods, results, invoices, lines, notifications) — with two exceptions:
   `sp_schedule_dunning`, whose max+1 `attempt_no` defeats its `ON CONFLICT` clause and makes
   replays additive rather than idempotent, and `sp_change_plan`, whose subscription INSERT
   has no `ON CONFLICT` clause and errors with a primary-key violation on replay.
7. `fn_usage_summary` has no app/proc caller, but the parity suite exercises it
   (`RATING-007`) — it must be carried forward.
