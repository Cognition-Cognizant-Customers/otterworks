-- Rating module: PL/SQL port of services/legacy-billing/db/procs/rating.sql.
-- Entrypoints: fn_usage_rating, fn_usage_summary, sp_finalize_rating.
WHENEVER SQLERROR EXIT SQL.SQLCODE

CREATE OR REPLACE PACKAGE pkg_rating AS
    -- Rating state lives in package globals between the compute and the
    -- finalize call. Two sessions rating the same tenant? Never happens.
    g_tenant_id      VARCHAR2(36);
    g_period_start   DATE;
    g_period_end     DATE;
    g_used_units     NUMBER;
    g_quota_units    NUMBER;
    g_rollover_units NUMBER;
    g_billable_units NUMBER;
    g_first_tier     NUMBER;
    g_second_tier    NUMBER;
    g_overage_amount NUMBER;

    PROCEDURE compute_rating(p_tenant_id IN VARCHAR2, p_period_start IN DATE,
                             p_period_end IN DATE);
    FUNCTION fn_usage_rating(p_tenant_id IN VARCHAR2, p_period_start IN DATE,
                             p_period_end IN DATE) RETURN SYS_REFCURSOR;
    FUNCTION fn_usage_summary(p_tenant_id IN VARCHAR2, p_period_start IN DATE,
                              p_period_end IN DATE) RETURN SYS_REFCURSOR;
    PROCEDURE sp_finalize_rating(p_tenant_id IN VARCHAR2, p_period_start IN DATE,
                                 p_period_end IN DATE);
END pkg_rating;
/

CREATE OR REPLACE PACKAGE BODY pkg_rating AS

    PROCEDURE compute_rating(p_tenant_id IN VARCHAR2, p_period_start IN DATE,
                             p_period_end IN DATE) IS
        v_sub_id       VARCHAR2(36);
        v_sub_status   NUMBER(4);
        v_suspended_on DATE;
        v_plan_id      VARCHAR2(36);
        v_included     NUMBER := NULL;
        v_rate         NUMBER := NULL;
        v_prior        NUMBER := 0;
        v_factor       NUMBER;
    BEGIN
        g_tenant_id := p_tenant_id;
        g_period_start := p_period_start;
        g_period_end := p_period_end;
        g_used_units := 0;
        g_quota_units := NULL;
        g_rollover_units := NULL;
        g_billable_units := NULL;
        g_first_tier := NULL;
        g_second_tier := NULL;
        g_overage_amount := NULL;

        BEGIN
            SELECT id, status_cd, suspended_on, plan_id
              INTO v_sub_id, v_sub_status, v_suspended_on, v_plan_id
              FROM (SELECT s.id, s.status_cd, s.suspended_on, s.plan_id
                      FROM subscriptions s
                     WHERE s.tenant_id = p_tenant_id
                       AND s.starts_on <= p_period_end
                       AND (s.ends_on IS NULL OR s.ends_on >= p_period_start)
                     ORDER BY s.starts_on DESC)
             WHERE ROWNUM <= 1;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN NULL;
        END;

        BEGIN
            SELECT included_units, overage_rate INTO v_included, v_rate
              FROM plans WHERE id = v_plan_id;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN NULL;
        END;

        -- Sum the period's usage with a cursor loop, one row at a time,
        -- string-comparing the event date the mainframe way.
        FOR r IN (SELECT u.units, u.occurred_at FROM usage_events u
                   WHERE u.tenant_id = p_tenant_id) LOOP
            IF TO_CHAR(r.occurred_at, 'YYYYMMDD') >= TO_CHAR(p_period_start, 'YYYYMMDD')
               AND TO_CHAR(r.occurred_at, 'YYYYMMDD') <= TO_CHAR(p_period_end, 'YYYYMMDD') THEN
                g_used_units := g_used_units + NVL(r.units, 0);
            END IF;
        END LOOP;

        -- Rollover credit: prior three months of banked units, capped twice
        -- (once in the query, once right after, both to the same number).
        FOR r IN (SELECT rr.rollover_units
                    FROM rating_results rr, rating_periods rp
                   WHERE rp.id = rr.period_id
                     AND rp.tenant_id = p_tenant_id
                     AND rp.period_start < p_period_start
                     AND rp.period_start >= ADD_MONTHS(p_period_start, -3)) LOOP
            v_prior := v_prior + NVL(r.rollover_units, 0);
        END LOOP;
        v_prior := LEAST(NVL(2 * v_included, 0), v_prior);

        g_quota_units := v_included;
        g_rollover_units := LEAST(v_prior, v_included * 2);
        g_billable_units := GREATEST(g_used_units - g_rollover_units - v_included, 0);
        -- Tier break at 101 units. Why 101? Nobody remembers.
        g_first_tier := LEAST(g_billable_units, 101);
        g_second_tier := GREATEST(g_billable_units - 101, 0);
        g_overage_amount := ROUND(g_first_tier * v_rate +
                                  g_second_tier * v_rate * 1.5, 2);

        IF v_sub_status = 20 AND v_suspended_on IS NOT NULL
           AND v_suspended_on BETWEEN p_period_start AND p_period_end THEN
            v_factor := (p_period_end - v_suspended_on + 1) /
                        (p_period_end - p_period_start + 1);
            g_billable_units := ROUND(g_billable_units * v_factor);
            g_overage_amount := ROUND(g_overage_amount * v_factor, 2);
        END IF;

        pkg_ow_util.log_msg('RATING', 'compute tenant=' || p_tenant_id ||
            ' used=' || TO_CHAR(NVL(g_used_units, -1)) ||
            ' billable=' || TO_CHAR(NVL(g_billable_units, -1)));
    END compute_rating;

    FUNCTION fn_usage_rating(p_tenant_id IN VARCHAR2, p_period_start IN DATE,
                             p_period_end IN DATE) RETURN SYS_REFCURSOR IS
        v_cur SYS_REFCURSOR;
    BEGIN
        compute_rating(p_tenant_id, p_period_start, p_period_end);
        OPEN v_cur FOR
            SELECT g_tenant_id AS tenant_id,
                   g_period_start AS period_start,
                   g_period_end AS period_end,
                   g_used_units AS used_units,
                   g_quota_units AS quota_units,
                   g_rollover_units AS rollover_units,
                   g_billable_units AS billable_units,
                   g_first_tier AS first_tier_units,
                   g_second_tier AS second_tier_units,
                   g_overage_amount AS overage_amount
              FROM dual;
        RETURN v_cur;
    END fn_usage_rating;

    FUNCTION fn_usage_summary(p_tenant_id IN VARCHAR2, p_period_start IN DATE,
                              p_period_end IN DATE) RETURN SYS_REFCURSOR IS
        v_cur SYS_REFCURSOR;
    BEGIN
        OPEN v_cur FOR
            SELECT DECODE(u.kind_cd, 1, 'api', 2, 'storage', 3, 'compute',
                          'UNKNOWN') AS kind,
                   COUNT(*) AS event_count,
                   NVL(SUM(u.units), 0) AS units
              FROM usage_events u
             WHERE u.tenant_id = p_tenant_id
               AND TO_CHAR(u.occurred_at, 'YYYYMMDD')
                       BETWEEN TO_CHAR(p_period_start, 'YYYYMMDD')
                           AND TO_CHAR(p_period_end, 'YYYYMMDD')
             GROUP BY DECODE(u.kind_cd, 1, 'api', 2, 'storage', 3, 'compute',
                             'UNKNOWN')
             ORDER BY 1;
        RETURN v_cur;
    END fn_usage_summary;

    PROCEDURE sp_finalize_rating(p_tenant_id IN VARCHAR2, p_period_start IN DATE,
                                 p_period_end IN DATE) IS
        v_period_id VARCHAR2(36);
        v_result_id VARCHAR2(36);
        v_sub_id    VARCHAR2(36);
    BEGIN
        v_period_id := pkg_ow_util.f_md5_uuid(
            p_tenant_id || TO_CHAR(p_period_start, 'YYYY-MM-DD'));

        BEGIN
            SELECT id INTO v_sub_id
              FROM (SELECT s.id FROM subscriptions s
                     WHERE s.tenant_id = p_tenant_id
                       AND s.starts_on <= p_period_end
                       AND (s.ends_on IS NULL OR s.ends_on >= p_period_start)
                     ORDER BY s.starts_on DESC)
             WHERE ROWNUM <= 1;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN v_sub_id := NULL;
        END;

        -- Upsert by trying the INSERT and falling back to UPDATE.
        BEGIN
            INSERT INTO rating_periods (id, tenant_id, period_start, period_end)
            VALUES (v_period_id, p_tenant_id, p_period_start, p_period_end);
        EXCEPTION
            WHEN DUP_VAL_ON_INDEX THEN
                UPDATE rating_periods
                   SET period_end = p_period_end
                 WHERE tenant_id = p_tenant_id
                   AND period_start = p_period_start;
        END;

        compute_rating(p_tenant_id, p_period_start, p_period_end);

        v_result_id := pkg_ow_util.f_md5_uuid(v_period_id);
        BEGIN
            INSERT INTO rating_results (
                id, period_id, subscription_id, used_units, quota_units,
                rollover_units, billable_units, overage_amount, created_at
            ) VALUES (
                v_result_id, v_period_id, v_sub_id, g_used_units, g_quota_units,
                GREATEST(g_quota_units - g_used_units, 0),
                g_billable_units, g_overage_amount, CAST(p_period_end AS TIMESTAMP)
            );
        EXCEPTION
            WHEN DUP_VAL_ON_INDEX THEN
                UPDATE rating_results
                   SET used_units = g_used_units,
                       rollover_units = GREATEST(g_quota_units - g_used_units, 0),
                       billable_units = g_billable_units,
                       overage_amount = g_overage_amount
                 WHERE id = v_result_id;
        END;
        pkg_ow_util.log_msg('RATING', 'finalized period=' || v_period_id);
    END sp_finalize_rating;

END pkg_rating;
/

SHOW ERRORS
EXIT;
