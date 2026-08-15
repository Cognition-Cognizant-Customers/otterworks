-- Gold tables for the converted finance report (ow_tp_finance_report), replacing
-- etl/legacy-extra/jobs/finance_excel_report.pl's CSV-renamed-to-.xls artifact and
-- its silent sendmail pipe.
--
-- The gold table is the system of record; the emitted file is a by-product.
-- Idempotent DDL: the converted job issues the same statements on every run, and
-- scripts/tp_databricks/recon_finance_report.py asserts the live schemas match this file.

CREATE TABLE IF NOT EXISTS ow_tp.gold.finance_billing_summary (
  ns STRING NOT NULL COMMENT 'Demo namespace; every run is scoped to one namespace.',
  currency STRING NOT NULL COMMENT 'ISO currency from copybook CBCUST01 CURRENCY.',
  record_type STRING NOT NULL COMMENT 'INVOICE (legacy 01) or CREDIT (legacy 02).',
  record_count BIGINT NOT NULL COMMENT 'Records aggregated for the currency/record-type cell.',
  total_amount DECIMAL(18,2) NOT NULL COMMENT 'Exact decimal total; no float accumulation.',
  report_date DATE NOT NULL COMMENT 'Business date of the report run.',
  generated_at TIMESTAMP NOT NULL COMMENT 'When this row was produced.'
)
COMMENT 'Finance billing summary by currency and record type, aggregated in SQL from ow_tp.silver.custbill_records. Replaces finance_billing_<date>.xls.';

CREATE TABLE IF NOT EXISTS ow_tp.gold.finance_report_delivery (
  ns STRING NOT NULL COMMENT 'Demo namespace.',
  report_date DATE NOT NULL COMMENT 'Business date of the report run.',
  artifact_path STRING COMMENT 'Volume path of the emitted artifact, NULL when nothing was written.',
  recipient_list STRING COMMENT 'Distribution list resolved from the ow_tp secret scope, never from code.',
  delivery_status STRING NOT NULL COMMENT 'DELIVERED, or NOT_DELIVERED_<reason> when no transport is configured.',
  delivered_at TIMESTAMP COMMENT 'Set only when delivery actually happened; NULL otherwise.'
)
COMMENT 'Delivery audit the legacy sendmail no-op never produced: what was written, to whom it was addressed, and whether it actually went out.';
