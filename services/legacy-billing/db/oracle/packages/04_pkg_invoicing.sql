-- Invoicing module: PL/SQL port of services/legacy-billing/db/procs/invoicing.sql.
-- Entrypoints: fn_invoice_preview, fn_invoice_lines, sp_issue_invoice.
WHENEVER SQLERROR EXIT SQL.SQLCODE

CREATE OR REPLACE PACKAGE pkg_invoicing AS
    -- The preview computation parks its intermediate numbers here so
    -- sp_issue_invoice can re-read them. Global mutable state as an API.
    g_plan_code  VARCHAR2(50);
    g_plan_fee   NUMBER;
    g_overage    NUMBER;
    g_tax        NUMBER;
    g_credit     NUMBER;

    PROCEDURE compute_preview(p_tenant_id IN VARCHAR2, p_period_start IN DATE,
                              p_period_end IN DATE);
    FUNCTION fn_invoice_preview(p_tenant_id IN VARCHAR2, p_period_start IN DATE,
                                p_period_end IN DATE) RETURN SYS_REFCURSOR;
    FUNCTION fn_invoice_lines(p_invoice_id IN VARCHAR2) RETURN SYS_REFCURSOR;
    PROCEDURE sp_issue_invoice(p_tenant_id IN VARCHAR2, p_period_start IN DATE,
                               p_period_end IN DATE);
END pkg_invoicing;
/

CREATE OR REPLACE PACKAGE BODY pkg_invoicing AS

    TAX_RATE CONSTANT NUMBER := 0.0825;   -- hardcoded 2011 combined rate

    PROCEDURE compute_preview(p_tenant_id IN VARCHAR2, p_period_start IN DATE,
                              p_period_end IN DATE) IS
        v_exempt CHAR(1) := 'N';
    BEGIN
        g_plan_code := NULL;
        g_plan_fee := NULL;
        BEGIN
            SELECT code, monthly_fee INTO g_plan_code, g_plan_fee
              FROM (SELECT p.code, p.monthly_fee
                      FROM subscriptions s, plans p
                     WHERE p.id = s.plan_id
                       AND s.tenant_id = p_tenant_id
                       AND s.starts_on <= p_period_end
                       AND (s.ends_on IS NULL OR s.ends_on >= p_period_start)
                     ORDER BY s.starts_on DESC)
             WHERE ROWNUM <= 1;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN NULL;
        END;

        pkg_rating.compute_rating(p_tenant_id, p_period_start, p_period_end);
        g_overage := pkg_rating.g_overage_amount;

        -- Sum open credit notes one row at a time.
        g_credit := 0;
        FOR r IN (SELECT remaining_amount FROM credit_notes
                   WHERE tenant_id = p_tenant_id AND remaining_amount > 0) LOOP
            g_credit := g_credit + NVL(r.remaining_amount, 0);
        END LOOP;

        BEGIN
            SELECT NVL(tax_exempt_yn, 'N') INTO v_exempt
              FROM tenants WHERE id = p_tenant_id;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN v_exempt := 'N';
        END;
        SELECT DECODE(v_exempt, 'Y', 0,
                      (NVL(g_plan_fee, 0) + NVL(g_overage, 0)) * TAX_RATE)
          INTO g_tax FROM dual;
    END compute_preview;

    FUNCTION fn_invoice_preview(p_tenant_id IN VARCHAR2, p_period_start IN DATE,
                                p_period_end IN DATE) RETURN SYS_REFCURSOR IS
        v_cur        SYS_REFCURSOR;
        v_credit_app NUMBER;
    BEGIN
        compute_preview(p_tenant_id, p_period_start, p_period_end);
        v_credit_app := LEAST(g_credit,
            ROUND(NVL(g_plan_fee, 0) + NVL(g_overage, 0) + NVL(g_tax, 0), 2));
        OPEN v_cur FOR
            SELECT 1 AS line_no, 'plan' AS line_type, g_plan_code AS description,
                   ROUND(g_plan_fee, 2) AS amount, 0 AS tax_amount,
                   0 AS credit_applied, ROUND(g_plan_fee, 2) AS total
              FROM dual
            UNION ALL
            SELECT 2, 'usage', 'usage overage', ROUND(g_overage, 2), 0, 0,
                   ROUND(g_overage, 2)
              FROM dual
            UNION ALL
            SELECT 3, 'tax', 'regional tax', g_tax / 2, 0, 0, g_tax / 2 FROM dual
            UNION ALL
            SELECT 4, 'tax', 'local tax', g_tax / 2, 0, 0, g_tax / 2 FROM dual
            UNION ALL
            SELECT 5, 'credit', 'credit notes', 0, 0, v_credit_app, -v_credit_app
              FROM dual;
        RETURN v_cur;
    END fn_invoice_preview;

    FUNCTION fn_invoice_lines(p_invoice_id IN VARCHAR2) RETURN SYS_REFCURSOR IS
        v_cur SYS_REFCURSOR;
    BEGIN
        OPEN v_cur FOR
            SELECT line_no, line_type, description, amount
              FROM invoice_lines
             WHERE invoice_id = p_invoice_id
             ORDER BY line_no;
        RETURN v_cur;
    END fn_invoice_lines;

    PROCEDURE sp_issue_invoice(p_tenant_id IN VARCHAR2, p_period_start IN DATE,
                               p_period_end IN DATE) IS
        v_period_id  VARCHAR2(36);
        v_invoice_id VARCHAR2(36);
        v_lines_cur  SYS_REFCURSOR;
        v_line_no    NUMBER;
        v_line_type  VARCHAR2(10);
        v_descr      VARCHAR2(400);
        v_amount     NUMBER;
        v_tax_amt    NUMBER;
        v_credit_app NUMBER;
        v_line_total NUMBER;
        v_subtotal   NUMBER := 0;
        v_tax        NUMBER := 0;
        v_total      NUMBER := 0;
        v_credit     NUMBER := 0;
    BEGIN
        v_period_id := pkg_ow_util.f_md5_uuid(
            p_tenant_id || TO_CHAR(p_period_start, 'YYYY-MM-DD'));
        v_invoice_id := pkg_ow_util.f_md5_uuid(v_period_id || 'invoice');

        pkg_rating.sp_finalize_rating(p_tenant_id, p_period_start, p_period_end);

        BEGIN
            INSERT INTO invoices (
                id, tenant_id, period_id, issued_at, subtotal, tax, total, status_cd
            ) VALUES (
                v_invoice_id, p_tenant_id, v_period_id,
                CAST(p_period_end AS TIMESTAMP), 0, 0, 0, 20
            );
        EXCEPTION
            WHEN DUP_VAL_ON_INDEX THEN
                UPDATE invoices SET status_cd = 20 WHERE id = v_invoice_id;
        END;

        -- Rebuild the lines from scratch on every issue.
        EXECUTE IMMEDIATE
            'DELETE FROM invoice_lines WHERE invoice_id = :1' USING v_invoice_id;

        v_lines_cur := fn_invoice_preview(p_tenant_id, p_period_start, p_period_end);
        LOOP
            FETCH v_lines_cur INTO v_line_no, v_line_type, v_descr, v_amount,
                                   v_tax_amt, v_credit_app, v_line_total;
            EXIT WHEN v_lines_cur%NOTFOUND;
            INSERT INTO invoice_lines (
                id, invoice_id, line_no, line_type, description, amount
            ) VALUES (
                pkg_ow_util.f_md5_uuid(v_invoice_id || TO_CHAR(v_line_no)),
                v_invoice_id, v_line_no, v_line_type, v_descr,
                DECODE(v_line_type, 'credit', v_line_total, v_amount)
            );
            IF v_line_type = 'plan' OR v_line_type = 'usage' THEN
                v_subtotal := v_subtotal + ROUND(v_amount, 2);
            ELSIF v_line_type = 'tax' THEN
                v_tax := v_tax + ROUND(v_amount, 2);
            ELSIF v_line_type = 'credit' THEN
                v_credit := v_credit_app;
            END IF;
        END LOOP;
        CLOSE v_lines_cur;

        v_total := ROUND(v_subtotal + v_tax - v_credit, 2);
        UPDATE invoices
           SET subtotal = ROUND(v_subtotal, 2), tax = ROUND(v_tax, 2),
               total = v_total
         WHERE id = v_invoice_id;

        -- Burn down credit notes oldest-first, decrementing the same running
        -- counter the Postgres original does (quirks preserved verbatim).
        FOR r IN (SELECT id, remaining_amount FROM credit_notes
                   WHERE tenant_id = p_tenant_id AND remaining_amount > 0
                   ORDER BY issued_on, id) LOOP
            EXIT WHEN v_credit <= 0;
            UPDATE credit_notes
               SET remaining_amount = GREATEST(remaining_amount - v_credit, 0)
             WHERE id = r.id;
            v_credit := GREATEST(v_credit - r.remaining_amount, 0);
        END LOOP;

        pkg_ow_util.log_msg('INVOICING', 'issued invoice=' || v_invoice_id ||
            ' total=' || TO_CHAR(NVL(v_total, 0)));
    END sp_issue_invoice;

END pkg_invoicing;
/

SHOW ERRORS
EXIT;
