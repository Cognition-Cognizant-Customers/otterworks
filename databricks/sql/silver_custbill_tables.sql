-- Silver tables owned by the ow_tp_parse_custbill work unit
-- (docs/tech-partnerships/contracts/parse_custbill_fixedwidth.md).
--
-- The finance report unit depends on ow_tp.silver.custbill_records and that unit had
-- not landed yet, so this file plus scripts/tp_databricks/bootstrap_silver_custbill.py
-- stand the tables up with a schema-validated parse of copybook CBCUST01. When the
-- parse unit lands, its own DDL and job supersede this bootstrap; the schema here
-- follows its contract so the finance report keeps reading the same columns.
-- These are the DDL statements for the default `ow_tp` catalog. The bootstrap job issues
-- the same statements with its `catalog` parameter substituted; the recon script does
-- not assert live schema equality against this file.

CREATE TABLE IF NOT EXISTS ow_tp.silver.custbill_records (
  ns STRING NOT NULL COMMENT 'Demo namespace.',
  file_name STRING NOT NULL COMMENT 'Source CUSTBILL drop file name.',
  line_no BIGINT NOT NULL COMMENT 'Physical 1-based line number in the source file (HDR is line 1).',
  record_type STRING NOT NULL COMMENT 'CBCUST01 REC-TYPE: 01 invoice, 02 credit.',
  account_id STRING NOT NULL COMMENT 'CBCUST01 CUST-ID.',
  invoice_id STRING COMMENT 'No invoice number exists in copybook CBCUST01; NULL until a feed provides one.',
  currency STRING NOT NULL COMMENT 'CBCUST01 CURRENCY.',
  amount DECIMAL(18,2) NOT NULL COMMENT 'CBCUST01 BILL-AMT with the implied decimal applied numerically (value / 100), not by string surgery.',
  bill_date DATE NOT NULL COMMENT 'CBCUST01 BILL-DATE parsed as a real date; unparseable dates are quarantined.',
  parsed_at TIMESTAMP NOT NULL COMMENT 'When the record was parsed.'
)
COMMENT 'Typed CUSTBILL detail records. Replaces the cut/sed/awk .psv produced by parse_custbill_fixedwidth.sh.';

CREATE TABLE IF NOT EXISTS ow_tp.silver.custbill_rejects (
  ns STRING NOT NULL,
  file_name STRING NOT NULL,
  line_no BIGINT NOT NULL,
  raw_line STRING COMMENT 'The record exactly as it arrived.',
  reject_reason STRING NOT NULL COMMENT 'Which schema/validity rule the record failed.',
  rejected_at TIMESTAMP NOT NULL
)
COMMENT 'Quarantine for records failing schema or validity checks. Present even when empty: a visible quarantine is the point, the legacy parser passed bad records straight through.';

CREATE TABLE IF NOT EXISTS ow_tp.silver.custbill_file_recon (
  ns STRING NOT NULL,
  file_name STRING NOT NULL,
  declared_trailer_count BIGINT COMMENT 'Count declared by the TRL trailer record.',
  parsed_count BIGINT NOT NULL,
  rejected_count BIGINT NOT NULL,
  recon_ok BOOLEAN NOT NULL COMMENT 'declared_trailer_count = parsed_count + rejected_count.',
  reconciled_at TIMESTAMP NOT NULL
)
COMMENT 'Per-file trailer reconciliation. The legacy parser logged the trailer count and never compared it (ETL-0187, 2011).';
