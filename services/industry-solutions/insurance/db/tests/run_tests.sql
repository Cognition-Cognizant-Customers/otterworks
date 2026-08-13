-- Commission Pay test suite. Run as COMMISSION_PAY against FREEPDB1:
--   sqlplus commission_pay/commission_pay@localhost:1521/FREEPDB1 @run_tests.sql
-- Prints PASS/FAIL per case and exits non-zero if any case fails.
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET SERVEROUTPUT ON SIZE UNLIMITED
SET FEEDBACK OFF

DECLARE
    g_failures  PLS_INTEGER := 0;
    l_rate_id   NUMBER;
    l_prev_to   DATE;
    l_num       NUMBER;
    l_num2      NUMBER;
    l_rows      PLS_INTEGER;

    PROCEDURE check_case (p_name IN VARCHAR2, p_ok IN BOOLEAN, p_detail IN VARCHAR2 DEFAULT NULL) IS
    BEGIN
        IF p_ok THEN
            DBMS_OUTPUT.PUT_LINE('PASS ' || p_name);
        ELSE
            g_failures := g_failures + 1;
            DBMS_OUTPUT.PUT_LINE('FAIL ' || p_name
                || CASE WHEN p_detail IS NOT NULL THEN ' -- ' || p_detail END);
        END IF;
    END check_case;

    PROCEDURE expect_error (p_name IN VARCHAR2, p_expected_code IN NUMBER) IS
    BEGIN
        check_case(p_name, FALSE, 'expected ORA' || p_expected_code || ' but no error raised');
    END expect_error;
BEGIN
    ---------------------------------------------------------------
    -- T1: upserting a rate closes the previous open rate for the scope
    ---------------------------------------------------------------
    commission_pkg.upsert_commission_rate('AUTO-STD', NULL, 8.50, DATE '2026-01-01', 'tester', l_rate_id);
    SELECT effective_to INTO l_prev_to
      FROM commission_rates
     WHERE product_code = 'AUTO-STD' AND agent_id IS NULL AND rate_id <> l_rate_id
       AND effective_from = DATE '2024-01-01';
    check_case('T1 rate upsert supersedes prior open rate',
               l_prev_to = DATE '2025-12-31',
               'prior effective_to=' || TO_CHAR(l_prev_to, 'YYYY-MM-DD'));

    ---------------------------------------------------------------
    -- T1b: same-day upsert amends the open rate in place (idempotent)
    ---------------------------------------------------------------
    commission_pkg.upsert_commission_rate('AUTO-STD', NULL, 8.75, DATE '2026-01-01', 'tester', l_rate_id);
    SELECT COUNT(*), MAX(rate_pct) INTO l_num, l_num2
      FROM commission_rates
     WHERE product_code = 'AUTO-STD' AND agent_id IS NULL AND effective_to IS NULL;
    check_case('T1b same-day upsert amends in place',
               l_num = 1 AND l_num2 = 8.75,
               'open rows=' || l_num || ' pct=' || l_num2);

    ---------------------------------------------------------------
    -- T2: rate outside (0, 50] is rejected
    ---------------------------------------------------------------
    BEGIN
        commission_pkg.upsert_commission_rate('AUTO-STD', NULL, 55, DATE '2026-02-01', 'tester', l_rate_id);
        expect_error('T2 invalid rate rejected', -20001);
    EXCEPTION
        WHEN commission_pkg.e_invalid_rate THEN
            check_case('T2 invalid rate rejected', TRUE);
    END;

    ---------------------------------------------------------------
    -- T3: rate for an inactive agent is rejected
    ---------------------------------------------------------------
    BEGIN
        commission_pkg.upsert_commission_rate('AUTO-STD', 4, 9, DATE '2026-02-01', 'tester', l_rate_id);
        expect_error('T3 suspended agent rate rejected', -20003);
    EXCEPTION
        WHEN commission_pkg.e_inactive_agent THEN
            check_case('T3 suspended agent rate rejected', TRUE);
    END;

    ---------------------------------------------------------------
    -- T4: splits must total exactly 100
    ---------------------------------------------------------------
    BEGIN
        commission_pkg.set_commission_splits(3,
            split_alloc_tab(split_alloc_t(1, 70), split_alloc_t(2, 40)), 'tester');
        expect_error('T4 splits over 100 rejected', -20006);
    EXCEPTION
        WHEN commission_pkg.e_bad_split THEN
            check_case('T4 splits over 100 rejected', TRUE);
    END;

    ---------------------------------------------------------------
    -- T5: duplicate agents in a split are rejected
    ---------------------------------------------------------------
    BEGIN
        commission_pkg.set_commission_splits(3,
            split_alloc_tab(split_alloc_t(1, 50), split_alloc_t(1, 50)), 'tester');
        expect_error('T5 duplicate split agent rejected', -20006);
    EXCEPTION
        WHEN commission_pkg.e_bad_split THEN
            check_case('T5 duplicate split agent rejected', TRUE);
    END;

    ---------------------------------------------------------------
    -- T6: valid two-way split replaces the allocation
    ---------------------------------------------------------------
    commission_pkg.set_commission_splits(3,
        split_alloc_tab(split_alloc_t(2, 65), split_alloc_t(3, 35)), 'tester');
    SELECT COUNT(*), SUM(split_pct) INTO l_num, l_num2
      FROM commission_splits WHERE policy_id = 3;
    check_case('T6 two-way split stored', l_num = 2 AND l_num2 = 100,
               'rows=' || l_num || ' total=' || l_num2);

    ---------------------------------------------------------------
    -- T7: agent-specific rate wins over product default
    ---------------------------------------------------------------
    SELECT rate_pct INTO l_num
      FROM commission_rates
     WHERE rate_id = commission_pkg.resolve_rate('AUTO-STD', 1, DATE '2025-06-15');
    check_case('T7 agent override beats default', l_num = 9.50, 'pct=' || l_num);
    SELECT rate_pct INTO l_num
      FROM commission_rates
     WHERE rate_id = commission_pkg.resolve_rate('AUTO-STD', 2, DATE '2025-06-15');
    check_case('T7b default used when no override', l_num = 8.00, 'pct=' || l_num);

    ---------------------------------------------------------------
    -- T8: three-way split commission math (policy 4, 2025-06)
    -- premium 9600/12 = 800. Agent 1: 800*9.5%*50% = 38.00
    -- Agent 2: 800*8%*30% = 19.20. Agent 3: 800*8%*20% = 12.80
    ---------------------------------------------------------------
    commission_pkg.calculate_policy_commission(4, '2025-06', 'tester');
    SELECT COUNT(*) INTO l_rows FROM commission_ledger WHERE policy_id = 4 AND period_month = '2025-06';
    check_case('T8 three-way split writes 3 ledger rows', l_rows = 3, 'rows=' || l_rows);
    SELECT commission_amt INTO l_num FROM commission_ledger
     WHERE policy_id = 4 AND agent_id = 1 AND period_month = '2025-06';
    check_case('T8a agent 1 amount', l_num = 38.00, 'amt=' || l_num);
    SELECT commission_amt INTO l_num FROM commission_ledger
     WHERE policy_id = 4 AND agent_id = 2 AND period_month = '2025-06';
    check_case('T8b agent 2 amount', l_num = 19.20, 'amt=' || l_num);
    SELECT commission_amt INTO l_num FROM commission_ledger
     WHERE policy_id = 4 AND agent_id = 3 AND period_month = '2025-06';
    check_case('T8c agent 3 amount', l_num = 12.80, 'amt=' || l_num);

    ---------------------------------------------------------------
    -- T9: recalculating a period replaces rows, not duplicates
    ---------------------------------------------------------------
    commission_pkg.calculate_policy_commission(4, '2025-06', 'tester');
    SELECT COUNT(*) INTO l_rows FROM commission_ledger WHERE policy_id = 4 AND period_month = '2025-06';
    check_case('T9 recalculation is idempotent', l_rows = 3, 'rows=' || l_rows);

    ---------------------------------------------------------------
    -- T10: lapsed policy cannot be calculated
    ---------------------------------------------------------------
    BEGIN
        commission_pkg.calculate_policy_commission(5, '2025-06', 'tester');
        expect_error('T10 lapsed policy rejected', -20008);
    EXCEPTION
        WHEN commission_pkg.e_policy_not_active THEN
            check_case('T10 lapsed policy rejected', TRUE);
    END;

    ---------------------------------------------------------------
    -- T11: policy without a split allocation cannot be calculated
    ---------------------------------------------------------------
    BEGIN
        commission_pkg.set_commission_splits(3, split_alloc_tab(), 'tester');
        expect_error('T11 empty split rejected', -20006);
    EXCEPTION
        WHEN commission_pkg.e_bad_split THEN
            check_case('T11 empty split rejected', TRUE);
    END;

    ---------------------------------------------------------------
    -- T12: audit trail rows were written
    ---------------------------------------------------------------
    SELECT COUNT(*) INTO l_num FROM rate_audit_log WHERE actor = 'tester';
    check_case('T12 audit trail written', l_num >= 4, 'rows=' || l_num);

    DBMS_OUTPUT.PUT_LINE('----');
    IF g_failures = 0 THEN
        DBMS_OUTPUT.PUT_LINE('OLTP TESTS: ALL PASS');
    ELSE
        DBMS_OUTPUT.PUT_LINE('OLTP TESTS: ' || g_failures || ' FAILURE(S)');
        RAISE_APPLICATION_ERROR(-20099, g_failures || ' test failure(s)');
    END IF;
END;
/

EXIT;
