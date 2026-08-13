-- ETL from the Commission Pay OLTP schema into the star schema, plus the
-- reporting materialized view analysts query.
WHENEVER SQLERROR EXIT SQL.SQLCODE

CREATE OR REPLACE PACKAGE dw_etl_pkg AS
    -- Idempotent load: upserts dimensions from OLTP, then merges the ledger
    -- rows for p_period_month (NULL = all periods) into fact_commission.
    PROCEDURE load_commission_facts (
        p_period_month IN VARCHAR2 DEFAULT NULL,
        o_rows_merged  OUT PLS_INTEGER
    );
END dw_etl_pkg;
/

CREATE OR REPLACE PACKAGE BODY dw_etl_pkg AS

    PROCEDURE load_commission_facts (
        p_period_month IN VARCHAR2 DEFAULT NULL,
        o_rows_merged  OUT PLS_INTEGER
    ) IS
    BEGIN
        MERGE INTO dim_agent d
        USING (SELECT agent_id, agent_code, full_name, status
                 FROM commission_pay.agents) s
           ON (d.agent_id = s.agent_id)
         WHEN MATCHED THEN UPDATE SET
              d.agent_code = s.agent_code,
              d.full_name  = s.full_name,
              d.status     = s.status
         WHEN NOT MATCHED THEN
              INSERT (agent_id, agent_code, full_name, status)
              VALUES (s.agent_id, s.agent_code, s.full_name, s.status);

        MERGE INTO dim_product d
        USING (SELECT product_code, product_name, line_of_business
                 FROM commission_pay.products) s
           ON (d.product_code = s.product_code)
         WHEN MATCHED THEN UPDATE SET
              d.product_name     = s.product_name,
              d.line_of_business = s.line_of_business
         WHEN NOT MATCHED THEN
              INSERT (product_code, product_name, line_of_business)
              VALUES (s.product_code, s.product_name, s.line_of_business);

        MERGE INTO dim_period d
        USING (SELECT DISTINCT
                      period_month,
                      TO_NUMBER(SUBSTR(period_month, 1, 4)) AS year_num,
                      TO_NUMBER(SUBSTR(period_month, 6, 2)) AS month_num,
                      CEIL(TO_NUMBER(SUBSTR(period_month, 6, 2)) / 3) AS quarter_num
                 FROM commission_pay.commission_ledger
                WHERE p_period_month IS NULL OR period_month = p_period_month) s
           ON (d.period_month = s.period_month)
         WHEN NOT MATCHED THEN
              INSERT (period_month, year_num, month_num, quarter_num)
              VALUES (s.period_month, s.year_num, s.month_num, s.quarter_num);

        MERGE INTO fact_commission f
        USING (SELECT da.agent_key,
                      dp.product_key,
                      dd.period_key,
                      cl.policy_id,
                      cl.split_pct,
                      cl.base_premium,
                      cl.commission_amt
                 FROM commission_pay.commission_ledger cl
                 JOIN commission_pay.policies po ON po.policy_id = cl.policy_id
                 JOIN dim_agent   da ON da.agent_id     = cl.agent_id
                 JOIN dim_product dp ON dp.product_code = po.product_code
                 JOIN dim_period  dd ON dd.period_month = cl.period_month
                WHERE p_period_month IS NULL OR cl.period_month = p_period_month) s
           ON (f.policy_id = s.policy_id
               AND f.agent_key = s.agent_key
               AND f.period_key = s.period_key)
         WHEN MATCHED THEN UPDATE SET
              f.split_pct      = s.split_pct,
              f.base_premium   = s.base_premium,
              f.commission_amt = s.commission_amt,
              f.loaded_at      = SYSTIMESTAMP
         WHEN NOT MATCHED THEN
              INSERT (agent_key, product_key, period_key, policy_id,
                      split_pct, base_premium, commission_amt)
              VALUES (s.agent_key, s.product_key, s.period_key, s.policy_id,
                      s.split_pct, s.base_premium, s.commission_amt);

        o_rows_merged := SQL%ROWCOUNT;
        COMMIT;
    EXCEPTION
        WHEN OTHERS THEN
            ROLLBACK;
            RAISE;
    END load_commission_facts;

END dw_etl_pkg;
/

-- Reporting rollup: commission earned per agent per period.
CREATE MATERIALIZED VIEW mv_agent_commission_summary
BUILD IMMEDIATE
REFRESH COMPLETE ON DEMAND
AS
SELECT da.agent_code,
       da.full_name,
       dd.period_month,
       dp.line_of_business,
       COUNT(*)              AS policy_rows,
       SUM(f.commission_amt) AS total_commission
  FROM fact_commission f
  JOIN dim_agent   da ON da.agent_key   = f.agent_key
  JOIN dim_product dp ON dp.product_key = f.product_key
  JOIN dim_period  dd ON dd.period_key  = f.period_key
 GROUP BY da.agent_code, da.full_name, dd.period_month, dp.line_of_business;

EXIT;
