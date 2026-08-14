-- DBMS_SCHEDULER jobs: the nightly batch layer nobody dares to touch.
-- Business logic runs from the database scheduler; the app just reads
-- whatever the jobs left behind.
WHENEVER SQLERROR EXIT SQL.SQLCODE

BEGIN
    -- 02:00 nightly: schedule dunning attempts for every overdue invoice.
    DBMS_SCHEDULER.CREATE_JOB(
        job_name        => 'JOB_NIGHTLY_DUNNING',
        job_type        => 'PLSQL_BLOCK',
        job_action      => 'BEGIN pkg_dunning.sp_schedule_dunning(TRUNC(SYSDATE)); pkg_dunning.sp_suspend_overdue(TRUNC(SYSDATE)); END;',
        start_date      => SYSTIMESTAMP,
        repeat_interval => 'FREQ=DAILY;BYHOUR=2;BYMINUTE=0',
        enabled         => TRUE,
        comments        => 'Nightly dunning schedule + suspension sweep (legacy batch)');

    -- 03:30 nightly: prune the autonomous-transaction audit log, keeping 90
    -- days, with the retention hardcoded in the job text.
    DBMS_SCHEDULER.CREATE_JOB(
        job_name        => 'JOB_PURGE_AUDIT_LOG',
        job_type        => 'PLSQL_BLOCK',
        job_action      => 'BEGIN DELETE FROM billing_audit_log WHERE logged_at < SYSDATE - 90; COMMIT; EXCEPTION WHEN OTHERS THEN NULL; END;',
        start_date      => SYSTIMESTAMP,
        repeat_interval => 'FREQ=DAILY;BYHOUR=3;BYMINUTE=30',
        enabled         => TRUE,
        comments        => 'Audit log retention (90 days, hardcoded)');
END;
/

EXIT;
