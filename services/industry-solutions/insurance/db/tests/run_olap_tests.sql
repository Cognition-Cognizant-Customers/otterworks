-- OLAP/ETL test suite. Run as COMMISSION_DW against FREEPDB1 AFTER the OLTP
-- suite (it consumes the ledger rows the OLTP tests calculated):
--   sqlplus commission_dw/commission_dw@localhost:1521/FREEPDB1 @run_olap_tests.sql
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET SERVEROUTPUT ON SIZE UNLIMITED
SET FEEDBACK OFF

DECLARE
    g_failures PLS_INTEGER := 0;
    l_merged   PLS_INTEGER;
    l_num      NUMBER;
    l_num2     NUMBER;

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
BEGIN
    ---------------------------------------------------------------
    -- E1: ETL loads the 2025-06 ledger rows into the star schema
    ---------------------------------------------------------------
    dw_etl_pkg.load_commission_facts('2025-06', l_merged);
    check_case('E1 ETL merges fact rows', l_merged >= 3, 'merged=' || l_merged);
    SELECT COUNT(*) INTO l_num FROM fact_commission;
    check_case('E1a fact rows present', l_num >= 3, 'rows=' || l_num);

    ---------------------------------------------------------------
    -- E2: fact totals reconcile with the OLTP ledger for the period
    ---------------------------------------------------------------
    SELECT NVL(SUM(commission_amt), 0) INTO l_num
      FROM fact_commission f
      JOIN dim_period d ON d.period_key = f.period_key
     WHERE d.period_month = '2025-06';
    SELECT NVL(SUM(commission_amt), 0) INTO l_num2
      FROM commission_pay.commission_ledger
     WHERE period_month = '2025-06';
    check_case('E2 fact totals reconcile with ledger', l_num = l_num2,
               'fact=' || l_num || ' ledger=' || l_num2);

    ---------------------------------------------------------------
    -- E3: re-running the ETL is idempotent
    ---------------------------------------------------------------
    SELECT COUNT(*) INTO l_num FROM fact_commission;
    dw_etl_pkg.load_commission_facts('2025-06', l_merged);
    SELECT COUNT(*) INTO l_num2 FROM fact_commission;
    check_case('E3 ETL rerun does not duplicate facts', l_num = l_num2,
               'before=' || l_num || ' after=' || l_num2);

    ---------------------------------------------------------------
    -- E4: the summary materialized view reflects loaded facts
    ---------------------------------------------------------------
    DBMS_MVIEW.REFRESH('MV_AGENT_COMMISSION_SUMMARY', 'C');
    SELECT COUNT(*) INTO l_num FROM mv_agent_commission_summary WHERE period_month = '2025-06';
    check_case('E4 summary MV populated', l_num >= 3, 'rows=' || l_num);

    DBMS_OUTPUT.PUT_LINE('----');
    IF g_failures = 0 THEN
        DBMS_OUTPUT.PUT_LINE('OLAP TESTS: ALL PASS');
    ELSE
        DBMS_OUTPUT.PUT_LINE('OLAP TESTS: ' || g_failures || ' FAILURE(S)');
        RAISE_APPLICATION_ERROR(-20099, g_failures || ' test failure(s)');
    END IF;
END;
/

EXIT;
