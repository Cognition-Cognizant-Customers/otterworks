#!/bin/bash
# This directory (db/oracle/startup) is mounted into /opt/oracle/scripts/startup,
# which the Oracle Free image runs on every boot once the database is open.
# (The image ships a prebuilt DB, so /opt/oracle/scripts/setup never fires —
# startup is the reliable hook.) Only this orchestrator lives here: anything
# else in the mounted directory would be auto-executed as SYSDBA in the CDB
# root, which is the wrong container for our schema.
#
# Idempotent and self-repairing: the skip is gated on the FIXTURE_META
# completion marker, written only after every script has run and all objects
# compiled VALID. A boot that failed part-way leaves no marker, so the next
# boot re-runs the initialization.
#
# The image *sources* startup scripts, so all work happens in a subshell to
# keep `set -e` and any failure from tearing down the container entrypoint.
(
  set -euo pipefail

  SQL_DIR=/opt/oracle/scripts/oracle-billing

  marker=$(sqlplus -s "system/${ORACLE_PWD}@localhost:1521/FREEPDB1" <<'SQL'
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET HEADING OFF FEEDBACK OFF PAGESIZE 0
SELECT COUNT(*) FROM all_tables
 WHERE owner = 'OW_BILLING' AND table_name = 'FIXTURE_META';
EXIT;
SQL
  )
  marker=$(echo "${marker}" | tr -d '[:space:]')
  case "${marker}" in
    0) ;;
    1) echo "== oracle-billing fixture already initialized, skipping"; exit 0 ;;
    *) echo "== could not determine fixture state: ${marker}" >&2; exit 1 ;;
  esac

  run_sql() {
    local conn="$1" file="$2"
    echo "== ${file} (${conn%%/*})"
    sqlplus -s "${conn}@localhost:1521/FREEPDB1" @"${file}"
  }

  user_exists=$(sqlplus -s "system/${ORACLE_PWD}@localhost:1521/FREEPDB1" <<'SQL'
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET HEADING OFF FEEDBACK OFF PAGESIZE 0
SELECT COUNT(*) FROM all_users WHERE username = 'OW_BILLING';
EXIT;
SQL
  )
  if [ "$(echo "${user_exists}" | tr -d '[:space:]')" = "0" ]; then
    run_sql "system/${ORACLE_PWD}" "${SQL_DIR}/setup/01_users.sql"
  else
    echo "== user exists without completion marker: repairing a partial init"
    sqlplus -s "system/${ORACLE_PWD}@localhost:1521/FREEPDB1" <<'SQL'
WHENEVER SQLERROR EXIT SQL.SQLCODE
DROP USER ow_billing CASCADE;
EXIT;
SQL
    run_sql "system/${ORACLE_PWD}" "${SQL_DIR}/setup/01_users.sql"
  fi

  run_sql "ow_billing/ow_billing" "${SQL_DIR}/schema/01_tables.sql"
  run_sql "ow_billing/ow_billing" "${SQL_DIR}/schema/02_horror.sql"
  run_sql "ow_billing/ow_billing" "${SQL_DIR}/packages/01_pkg_util.sql"
  run_sql "ow_billing/ow_billing" "${SQL_DIR}/packages/02_pkg_plans.sql"
  run_sql "ow_billing/ow_billing" "${SQL_DIR}/packages/03_pkg_rating.sql"
  run_sql "ow_billing/ow_billing" "${SQL_DIR}/packages/04_pkg_invoicing.sql"
  run_sql "ow_billing/ow_billing" "${SQL_DIR}/schema/03_seed_static.sql"
  run_sql "ow_billing/ow_billing" "${SQL_DIR}/packages/05_pkg_dunning.sql"
  run_sql "ow_billing/ow_billing" "${SQL_DIR}/schema/04_jobs.sql"

  # PL/SQL compilation errors do not trip WHENEVER SQLERROR, so assert every
  # object in the schema is VALID before declaring success.
  invalid=$(sqlplus -s "system/${ORACLE_PWD}@localhost:1521/FREEPDB1" <<'SQL'
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET HEADING OFF FEEDBACK OFF PAGESIZE 0
SELECT COUNT(*) FROM all_objects
 WHERE owner = 'OW_BILLING' AND status <> 'VALID';
EXIT;
SQL
  )
  invalid=$(echo "${invalid}" | tr -d '[:space:]')
  if [ "${invalid}" != "0" ]; then
    echo "== ${invalid} invalid object(s) after initialization" >&2
    sqlplus -s "system/${ORACLE_PWD}@localhost:1521/FREEPDB1" <<'SQL'
SET HEADING OFF FEEDBACK OFF PAGESIZE 0
SELECT owner || '.' || object_name || ' (' || object_type || ')'
  FROM all_objects
 WHERE owner = 'OW_BILLING' AND status <> 'VALID';
EXIT;
SQL
    exit 1
  fi

  # Completion marker: written last; the health check and the skip guard
  # both key off it.
  sqlplus -s "ow_billing/ow_billing@localhost:1521/FREEPDB1" <<'SQL'
WHENEVER SQLERROR EXIT SQL.SQLCODE
CREATE TABLE fixture_meta (initialized_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL);
INSERT INTO fixture_meta (initialized_at) VALUES (SYSTIMESTAMP);
COMMIT;
EXIT;
SQL

  echo "== oracle-billing fixture ready"
) || echo "== oracle-billing fixture initialization FAILED (will retry on next boot; see errors above)"
