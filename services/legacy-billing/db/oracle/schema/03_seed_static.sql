-- Deterministic baseline seed: the Oracle port of
-- services/legacy-billing/db/seed.sql (same IDs and values, status text
-- mapped to the magic-number *_CD codes) so a future parity harness can
-- compare entrypoints against the Postgres recordings.
WHENEVER SQLERROR EXIT SQL.SQLCODE
ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD';
ALTER SESSION SET NLS_TIMESTAMP_FORMAT = 'YYYY-MM-DD HH24:MI:SS';

INSERT INTO tenants (id, name, tax_exempt_yn, status_cd) VALUES ('00000000-0000-0000-0000-000000000001', 'Tenant One', 'N', 10);
INSERT INTO tenants (id, name, tax_exempt_yn, status_cd) VALUES ('00000000-0000-0000-0000-000000000002', 'Tenant Two', 'N', 20);
INSERT INTO tenants (id, name, tax_exempt_yn, status_cd) VALUES ('00000000-0000-0000-0000-000000000003', 'Tenant Three', 'Y', 10);
INSERT INTO tenants (id, name, tax_exempt_yn, status_cd) VALUES ('00000000-0000-0000-0000-000000000004', 'Tenant Four', 'N', 10);
INSERT INTO tenants (id, name, tax_exempt_yn, status_cd) VALUES ('00000000-0000-0000-0000-000000000005', 'Tenant Five', 'N', 10);
INSERT INTO tenants (id, name, tax_exempt_yn, status_cd) VALUES ('00000000-0000-0000-0000-000000000006', 'Tenant Six', 'N', 10);
INSERT INTO tenants (id, name, tax_exempt_yn, status_cd) VALUES ('00000000-0000-0000-0000-000000000007', 'Tenant Seven', 'N', 10);
INSERT INTO tenants (id, name, tax_exempt_yn, status_cd) VALUES ('00000000-0000-0000-0000-000000000008', 'Tenant Eight', 'N', 10);
INSERT INTO tenants (id, name, tax_exempt_yn, status_cd) VALUES ('00000000-0000-0000-0000-000000000009', 'Tenant Nine', 'N', 10);

INSERT INTO plans (id, code, tier_cd, monthly_fee, included_units, overage_rate) VALUES ('10000000-0000-0000-0000-000000000001', 'STARTER', 1, 49.00, 100, 0.055000);
INSERT INTO plans (id, code, tier_cd, monthly_fee, included_units, overage_rate) VALUES ('10000000-0000-0000-0000-000000000002', 'GROWTH', 2, 149.00, 500, 0.035000);
INSERT INTO plans (id, code, tier_cd, monthly_fee, included_units, overage_rate) VALUES ('10000000-0000-0000-0000-000000000003', 'SCALE', 3, 499.00, 2000, 0.020000);

INSERT INTO subscriptions (id, tenant_id, plan_id, starts_on, ends_on, status_cd, suspended_on) VALUES ('20000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-000000000001', DATE '2026-01-01', NULL, 10, NULL);
INSERT INTO subscriptions (id, tenant_id, plan_id, starts_on, ends_on, status_cd, suspended_on) VALUES ('20000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000002', DATE '2026-01-01', NULL, 20, DATE '2026-02-15');
INSERT INTO subscriptions (id, tenant_id, plan_id, starts_on, ends_on, status_cd, suspended_on) VALUES ('20000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000003', '10000000-0000-0000-0000-000000000003', DATE '2026-01-01', NULL, 10, NULL);
INSERT INTO subscriptions (id, tenant_id, plan_id, starts_on, ends_on, status_cd, suspended_on) VALUES ('20000000-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000004', '10000000-0000-0000-0000-000000000001', DATE '2026-01-01', NULL, 10, NULL);
INSERT INTO subscriptions (id, tenant_id, plan_id, starts_on, ends_on, status_cd, suspended_on) VALUES ('20000000-0000-0000-0000-000000000005', '00000000-0000-0000-0000-000000000005', '10000000-0000-0000-0000-000000000002', DATE '2026-01-01', NULL, 10, NULL);
INSERT INTO subscriptions (id, tenant_id, plan_id, starts_on, ends_on, status_cd, suspended_on) VALUES ('20000000-0000-0000-0000-000000000006', '00000000-0000-0000-0000-000000000006', '10000000-0000-0000-0000-000000000001', DATE '2026-01-01', NULL, 10, NULL);
INSERT INTO subscriptions (id, tenant_id, plan_id, starts_on, ends_on, status_cd, suspended_on) VALUES ('20000000-0000-0000-0000-000000000007', '00000000-0000-0000-0000-000000000007', '10000000-0000-0000-0000-000000000001', DATE '2026-01-01', NULL, 10, NULL);
INSERT INTO subscriptions (id, tenant_id, plan_id, starts_on, ends_on, status_cd, suspended_on) VALUES ('20000000-0000-0000-0000-000000000008', '00000000-0000-0000-0000-000000000008', '10000000-0000-0000-0000-000000000001', DATE '2026-01-01', NULL, 10, NULL);
INSERT INTO subscriptions (id, tenant_id, plan_id, starts_on, ends_on, status_cd, suspended_on) VALUES ('20000000-0000-0000-0000-000000000009', '00000000-0000-0000-0000-000000000009', '10000000-0000-0000-0000-000000000001', DATE '2026-01-01', NULL, 10, NULL);

INSERT INTO usage_events (id, tenant_id, occurred_at, units, kind_cd) VALUES ('30000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', TIMESTAMP '2026-02-10 10:00:00', 260, 1);
INSERT INTO usage_events (id, tenant_id, occurred_at, units, kind_cd) VALUES ('30000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000002', TIMESTAMP '2026-02-10 10:00:00', 700, 1);
INSERT INTO usage_events (id, tenant_id, occurred_at, units, kind_cd) VALUES ('30000000-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000003', TIMESTAMP '2026-02-10 10:00:00', 2201, 3);
INSERT INTO usage_events (id, tenant_id, occurred_at, units, kind_cd) VALUES ('30000000-0000-0000-0000-000000000005', '00000000-0000-0000-0000-000000000004', TIMESTAMP '2026-02-05 10:00:00', 20, 1);
INSERT INTO usage_events (id, tenant_id, occurred_at, units, kind_cd) VALUES ('30000000-0000-0000-0000-000000000008', '00000000-0000-0000-0000-000000000004', TIMESTAMP '2026-02-06 10:00:00', 30, 2);
INSERT INTO usage_events (id, tenant_id, occurred_at, units, kind_cd) VALUES ('30000000-0000-0000-0000-000000000006', '00000000-0000-0000-0000-000000000005', TIMESTAMP '2026-02-01 10:00:00', 610, 1);
INSERT INTO usage_events (id, tenant_id, occurred_at, units, kind_cd) VALUES ('30000000-0000-0000-0000-000000000007', '00000000-0000-0000-0000-000000000006', TIMESTAMP '2026-02-28 10:00:00', 201, 1);
INSERT INTO usage_events (id, tenant_id, occurred_at, units, kind_cd) VALUES ('30000000-0000-0000-0000-000000000009', '00000000-0000-0000-0000-000000000007', TIMESTAMP '2026-02-10 10:00:00', 260, 1);
INSERT INTO usage_events (id, tenant_id, occurred_at, units, kind_cd) VALUES ('30000000-0000-0000-0000-000000000010', '00000000-0000-0000-0000-000000000008', TIMESTAMP '2026-02-28 10:00:00', 202, 1);
INSERT INTO usage_events (id, tenant_id, occurred_at, units, kind_cd) VALUES ('30000000-0000-0000-0000-000000000011', '00000000-0000-0000-0000-000000000009', TIMESTAMP '2026-02-10 10:00:00', 1, 1);

INSERT INTO rating_periods (id, tenant_id, period_start, period_end) VALUES ('40000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001', DATE '2025-11-01', DATE '2025-11-30');
INSERT INTO rating_periods (id, tenant_id, period_start, period_end) VALUES ('40000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', DATE '2025-12-01', DATE '2025-12-31');
INSERT INTO rating_periods (id, tenant_id, period_start, period_end) VALUES ('40000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', DATE '2026-01-01', DATE '2026-01-31');

INSERT INTO rating_results (id, period_id, subscription_id, used_units, quota_units, rollover_units, billable_units, overage_amount, created_at) VALUES ('50000000-0000-0000-0000-000000000003', '40000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000001', 0, 100, 100, 0, 0.00, TIMESTAMP '2025-11-30 00:00:00');
INSERT INTO rating_results (id, period_id, subscription_id, used_units, quota_units, rollover_units, billable_units, overage_amount, created_at) VALUES ('50000000-0000-0000-0000-000000000001', '40000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 0, 100, 100, 0, 0.00, TIMESTAMP '2025-12-31 00:00:00');
INSERT INTO rating_results (id, period_id, subscription_id, used_units, quota_units, rollover_units, billable_units, overage_amount, created_at) VALUES ('50000000-0000-0000-0000-000000000002', '40000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', 0, 100, 100, 0, 0.00, TIMESTAMP '2026-01-31 00:00:00');

INSERT INTO invoices (id, tenant_id, period_id, issued_at, subtotal, tax, total, status_cd) VALUES ('60000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002', '40000000-0000-0000-0000-000000000001', TIMESTAMP '2026-02-01 00:00:00', 149.00, 12.29, 161.29, 40);
INSERT INTO invoices (id, tenant_id, period_id, issued_at, subtotal, tax, total, status_cd) VALUES ('60000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000005', '40000000-0000-0000-0000-000000000001', TIMESTAMP '2026-02-13 00:00:00', 149.00, 12.29, 161.29, 40);
INSERT INTO invoices (id, tenant_id, period_id, issued_at, subtotal, tax, total, status_cd) VALUES ('60000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000006', '40000000-0000-0000-0000-000000000002', TIMESTAMP '2026-02-28 00:00:00', 49.00, 4.04, 53.04, 20);

INSERT INTO credit_notes (id, tenant_id, issued_on, amount, remaining_amount) VALUES ('70000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000004', DATE '2026-02-01', 30.00, 30.00);
INSERT INTO credit_notes (id, tenant_id, issued_on, amount, remaining_amount) VALUES ('70000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000004', DATE '2026-02-01', 30.00, 30.00);
INSERT INTO credit_notes (id, tenant_id, issued_on, amount, remaining_amount) VALUES ('70000000-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000003', DATE '2026-02-02', 25.00, 25.00);
INSERT INTO credit_notes (id, tenant_id, issued_on, amount, remaining_amount) VALUES ('70000000-0000-0000-0000-000000000005', '00000000-0000-0000-0000-000000000009', DATE '2026-01-31', 5.00, 5.00);
INSERT INTO credit_notes (id, tenant_id, issued_on, amount, remaining_amount) VALUES ('70000000-0000-0000-0000-000000000006', '00000000-0000-0000-0000-000000000009', DATE '2026-02-01', 55.00, 55.00);

INSERT INTO dunning_attempts (id, tenant_id, invoice_id, attempt_no, scheduled_for, status_cd) VALUES ('80000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000005', '60000000-0000-0000-0000-000000000002', 1, DATE '2026-02-16', 20);

INSERT INTO notifications (id, tenant_id, kind_cd, sent_at) VALUES ('90000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000005', 2, TIMESTAMP '2026-02-16 09:00:00');

INSERT INTO invoice_lines (id, invoice_id, line_no, line_type, description, amount) VALUES ('a0000000-0000-0000-0000-000000000001', '60000000-0000-0000-0000-000000000001', 1, 'plan', 'GROWTH', 149.00);
INSERT INTO invoice_lines (id, invoice_id, line_no, line_type, description, amount) VALUES ('a0000000-0000-0000-0000-000000000002', '60000000-0000-0000-0000-000000000001', 2, 'usage', 'usage overage', 12.29);

COMMIT;
EXIT;
