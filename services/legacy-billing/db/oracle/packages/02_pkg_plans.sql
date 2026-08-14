-- Plans module: PL/SQL port of services/legacy-billing/db/procs/plans.sql.
-- Entrypoints: fn_list_plans, fn_entitlement, sp_change_plan.
WHENEVER SQLERROR EXIT SQL.SQLCODE

CREATE OR REPLACE PACKAGE pkg_plans AS
    -- Package-state cache of the last entitlement looked up. Consumers poke
    -- at these globals instead of re-querying; nothing invalidates them.
    g_last_tenant_id VARCHAR2(36);
    g_last_plan_code VARCHAR2(50);

    FUNCTION fn_list_plans RETURN SYS_REFCURSOR;
    FUNCTION fn_entitlement(p_tenant_id IN VARCHAR2, p_on IN DATE) RETURN SYS_REFCURSOR;
    PROCEDURE sp_change_plan(p_tenant_id IN VARCHAR2, p_plan_id IN VARCHAR2,
                             p_effective_on IN DATE);
END pkg_plans;
/

CREATE OR REPLACE PACKAGE BODY pkg_plans AS

    FUNCTION fn_list_plans RETURN SYS_REFCURSOR IS
        v_cur SYS_REFCURSOR;
    BEGIN
        pkg_ow_util.log_msg('PLANS', 'fn_list_plans');
        OPEN v_cur FOR
            SELECT id AS plan_id, code,
                   DECODE(tier_cd, 1, 'starter', 2, 'growth', 3, 'scale',
                          'UNKNOWN') AS tier,
                   monthly_fee, included_units, overage_rate
              FROM plans
             WHERE NVL(active_yn, 'N') = 'Y'
             ORDER BY monthly_fee, code;
        RETURN v_cur;
    END fn_list_plans;

    FUNCTION fn_entitlement(p_tenant_id IN VARCHAR2, p_on IN DATE)
        RETURN SYS_REFCURSOR IS
        v_cur SYS_REFCURSOR;
    BEGIN
        g_last_tenant_id := p_tenant_id;
        BEGIN
            SELECT p.code INTO g_last_plan_code
              FROM subscriptions s, plans p
             WHERE p.id (+) = s.plan_id
               AND s.tenant_id = p_tenant_id
               AND s.starts_on <= p_on
               AND NVL(s.ends_on, TO_DATE('31-DEC-99', 'DD-MON-YY')) >= p_on
               AND ROWNUM = 1;
        EXCEPTION
            WHEN OTHERS THEN NULL;
        END;
        -- Old-style comma joins with (+); latest covering subscription wins.
        OPEN v_cur FOR
            SELECT * FROM (
                SELECT t.id AS tenant_id, p.code AS plan_code,
                       DECODE(p.tier_cd, 1, 'starter', 2, 'growth', 3, 'scale',
                              'UNKNOWN') AS tier,
                       p.monthly_fee, p.included_units,
                       DECODE(s.status_cd, 10, 'active', 20, 'suspended',
                              30, 'cancelled', 'UNKNOWN') AS subscription_status,
                       GREATEST(s.starts_on, p_on) AS effective_on
                  FROM tenants t, subscriptions s, plans p
                 WHERE s.tenant_id = t.id
                   AND p.id (+) = s.plan_id
                   AND t.id = p_tenant_id
                   AND s.starts_on <= p_on
                   AND (s.ends_on IS NULL OR s.ends_on >= p_on)
                 ORDER BY s.starts_on DESC
            ) WHERE ROWNUM <= 1;
        RETURN v_cur;
    END fn_entitlement;

    PROCEDURE sp_change_plan(p_tenant_id IN VARCHAR2, p_plan_id IN VARCHAR2,
                             p_effective_on IN DATE) IS
        CURSOR c_open_subs IS
            SELECT id, status_cd
              FROM subscriptions
             WHERE tenant_id = p_tenant_id
               AND ends_on IS NULL
               AND starts_on < p_effective_on
               FOR UPDATE;
        v_new_id  VARCHAR2(36);
        v_sql     VARCHAR2(1000);
    BEGIN
        pkg_ow_util.log_msg('PLANS', 'sp_change_plan tenant=' || p_tenant_id ||
            ' plan=' || p_plan_id || ' eff=' ||
            TO_CHAR(p_effective_on, 'YYYY-MM-DD'));

        -- Row-by-row close-out of open subscriptions instead of one UPDATE.
        -- The "cancelled stays cancelled" rule lives in TRG_SUB_NO_UNCANCEL.
        FOR r IN c_open_subs LOOP
            UPDATE subscriptions
               SET ends_on = p_effective_on - 1,
                   status_cd = DECODE(r.status_cd, 30, 30, 10)
             WHERE CURRENT OF c_open_subs;
        END LOOP;

        v_new_id := pkg_ow_util.f_md5_uuid(
            p_tenant_id || p_plan_id || TO_CHAR(p_effective_on, 'YYYY-MM-DD'));

        -- Dynamic SQL for a perfectly static INSERT.
        v_sql := 'INSERT INTO subscriptions (id, tenant_id, plan_id, starts_on, status_cd)'
              || ' VALUES (:1, :2, :3, :4, 10)';
        EXECUTE IMMEDIATE v_sql
            USING v_new_id, p_tenant_id, p_plan_id, p_effective_on;
    END sp_change_plan;

END pkg_plans;
/

SHOW ERRORS
EXIT;
