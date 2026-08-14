-- Dunning module: PL/SQL port of services/legacy-billing/db/procs/dunning.sql.
-- Entrypoints: fn_overdue_accounts, sp_schedule_dunning, sp_suspend_overdue.
WHENEVER SQLERROR EXIT SQL.SQLCODE

CREATE OR REPLACE PACKAGE pkg_dunning AS
    g_last_run_dt   DATE;      -- package-state "last run" marker
    g_scheduled_cnt NUMBER := 0;

    FUNCTION fn_overdue_accounts(p_as_of IN DATE) RETURN SYS_REFCURSOR;
    PROCEDURE sp_schedule_dunning(p_as_of IN DATE);
    PROCEDURE sp_suspend_overdue(p_as_of IN DATE);
END pkg_dunning;
/

CREATE OR REPLACE PACKAGE BODY pkg_dunning AS

    FUNCTION fn_overdue_accounts(p_as_of IN DATE) RETURN SYS_REFCURSOR IS
        v_cur SYS_REFCURSOR;
    BEGIN
        OPEN v_cur FOR
            SELECT i.tenant_id, i.id AS invoice_id, i.total,
                   TRUNC(p_as_of) - TRUNC(CAST(i.issued_at AS DATE)) AS days_overdue,
                   DECODE(t.status_cd, 10, 'active', 20, 'suspended',
                          'UNKNOWN') AS tenant_status
              FROM invoices i, tenants t
             WHERE t.id (+) = i.tenant_id
               AND i.status_cd = 40
               AND TO_CHAR(i.issued_at, 'YYYYMMDD') < TO_CHAR(p_as_of, 'YYYYMMDD')
             ORDER BY i.issued_at, i.id;
        RETURN v_cur;
    END fn_overdue_accounts;

    PROCEDURE sp_schedule_dunning(p_as_of IN DATE) IS
        v_attempt NUMBER;
        v_next    DATE;
        v_dow     VARCHAR2(3);
    BEGIN
        g_last_run_dt := p_as_of;
        g_scheduled_cnt := 0;
        FOR inv IN (SELECT id, tenant_id FROM invoices
                     WHERE status_cd = 40
                     ORDER BY issued_at, id) LOOP
            SELECT NVL(MAX(attempt_no), 0) + 1 INTO v_attempt
              FROM dunning_attempts WHERE invoice_id = inv.id;

            -- Weekend logic via TO_CHAR/DECODE instead of anything sane.
            v_next := TRUNC(p_as_of);
            v_dow := TO_CHAR(v_next, 'DY', 'NLS_DATE_LANGUAGE=ENGLISH');
            SELECT v_next + DECODE(v_dow, 'SAT', 2, 'SUN', 1, 0)
              INTO v_next FROM dual;

            BEGIN
                INSERT INTO dunning_attempts (
                    id, tenant_id, invoice_id, attempt_no, scheduled_for, status_cd
                ) VALUES (
                    pkg_ow_util.f_md5_uuid(inv.id || TO_CHAR(v_attempt)),
                    inv.tenant_id, inv.id, v_attempt, v_next, 10
                );
                g_scheduled_cnt := g_scheduled_cnt + 1;
            EXCEPTION
                -- ON CONFLICT DO NOTHING, the Oracle-legacy way: swallow
                -- absolutely everything and move on.
                WHEN OTHERS THEN NULL;
            END;
        END LOOP;
        pkg_ow_util.log_msg('DUNNING', 'scheduled ' ||
            TO_CHAR(g_scheduled_cnt) || ' attempts as of ' ||
            TO_CHAR(p_as_of, 'DD-MON-YY'));
    END sp_schedule_dunning;

    PROCEDURE sp_suspend_overdue(p_as_of IN DATE) IS
        v_active NUMBER;
    BEGIN
        FOR t IN (SELECT DISTINCT i.tenant_id FROM invoices i
                   WHERE i.status_cd = 40
                     AND TO_CHAR(i.issued_at, 'YYYYMMDD') <=
                         TO_CHAR(TRUNC(p_as_of) - 14, 'YYYYMMDD')) LOOP
            SELECT COUNT(*) INTO v_active
              FROM tenants WHERE id = t.tenant_id AND status_cd = 10;
            IF v_active > 0 THEN
                UPDATE tenants SET status_cd = 20 WHERE id = t.tenant_id;
                UPDATE subscriptions
                   SET status_cd = 20, suspended_on = TRUNC(p_as_of)
                 WHERE tenant_id = t.tenant_id AND status_cd = 10;

                INSERT INTO notifications (id, tenant_id, kind_cd, sent_at)
                SELECT pkg_ow_util.f_md5_uuid(t.tenant_id || 'suspension' ||
                           TO_CHAR(TRUNC(p_as_of), 'YYYY-MM-DD')),
                       t.tenant_id, 3, CAST(TRUNC(p_as_of) AS TIMESTAMP)
                  FROM dual
                 WHERE NOT EXISTS (
                       SELECT 1 FROM notifications
                        WHERE tenant_id = t.tenant_id AND kind_cd = 3
                          AND sent_at = CAST(TRUNC(p_as_of) AS TIMESTAMP));

                pkg_ow_util.log_msg('DUNNING', 'suspended tenant=' || t.tenant_id);
            END IF;
        END LOOP;
    END sp_suspend_overdue;

END pkg_dunning;
/

SHOW ERRORS
EXIT;
