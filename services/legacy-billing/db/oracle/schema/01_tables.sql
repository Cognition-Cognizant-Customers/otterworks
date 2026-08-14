-- OW_BILLING core billing tables: the Oracle port of
-- services/legacy-billing/db/schema.sql, degraded in the classic ways —
-- VARCHAR2(36) surrogate keys, magic-number status codes resolved through a
-- generic CODES lookup, sequence+trigger "identity" columns, and _HIST
-- full-row-copy version history maintained by triggers.
WHENEVER SQLERROR EXIT SQL.SQLCODE

-- Generic magic-number lookup. Every *_CD column joins here by convention;
-- nothing enforces it.
CREATE TABLE codes (
    code_type   VARCHAR2(30) NOT NULL,
    code_val    NUMBER(4)    NOT NULL,
    code_desc   VARCHAR2(80) NOT NULL,
    CONSTRAINT pk_codes PRIMARY KEY (code_type, code_val)
);

INSERT INTO codes VALUES ('TENANT_STATUS',  10, 'active');
INSERT INTO codes VALUES ('TENANT_STATUS',  20, 'suspended');
INSERT INTO codes VALUES ('SUB_STATUS',     10, 'active');
INSERT INTO codes VALUES ('SUB_STATUS',     20, 'suspended');
INSERT INTO codes VALUES ('SUB_STATUS',     30, 'cancelled');
INSERT INTO codes VALUES ('PLAN_TIER',       1, 'starter');
INSERT INTO codes VALUES ('PLAN_TIER',       2, 'growth');
INSERT INTO codes VALUES ('PLAN_TIER',       3, 'scale');
INSERT INTO codes VALUES ('USAGE_KIND',      1, 'api');
INSERT INTO codes VALUES ('USAGE_KIND',      2, 'storage');
INSERT INTO codes VALUES ('USAGE_KIND',      3, 'compute');
INSERT INTO codes VALUES ('INV_STATUS',     10, 'draft');
INSERT INTO codes VALUES ('INV_STATUS',     20, 'issued');
INSERT INTO codes VALUES ('INV_STATUS',     30, 'paid');
INSERT INTO codes VALUES ('INV_STATUS',     40, 'overdue');
INSERT INTO codes VALUES ('DUN_STATUS',     10, 'scheduled');
INSERT INTO codes VALUES ('DUN_STATUS',     20, 'sent');
INSERT INTO codes VALUES ('DUN_STATUS',     30, 'skipped');
INSERT INTO codes VALUES ('NOTIF_KIND',      1, 'invoice');
INSERT INTO codes VALUES ('NOTIF_KIND',      2, 'dunning');
INSERT INTO codes VALUES ('NOTIF_KIND',      3, 'suspension');
COMMIT;

CREATE TABLE tenants (
    id            VARCHAR2(36) NOT NULL,
    name          VARCHAR2(200) NOT NULL,
    tax_exempt_yn CHAR(1) DEFAULT 'N' NOT NULL,
    status_cd     NUMBER(4) NOT NULL,   -- CODES('TENANT_STATUS')
    CONSTRAINT pk_tenants PRIMARY KEY (id),
    CONSTRAINT uq_tenants_name UNIQUE (name)
);

CREATE TABLE plans (
    id             VARCHAR2(36) NOT NULL,
    code           VARCHAR2(50) NOT NULL,
    tier_cd        NUMBER(4) NOT NULL,  -- CODES('PLAN_TIER')
    monthly_fee    NUMBER(12,2) NOT NULL,
    included_units NUMBER(10) NOT NULL,
    overage_rate   NUMBER(12,6) NOT NULL,
    active_yn      CHAR(1) DEFAULT 'Y' NOT NULL,
    CONSTRAINT pk_plans PRIMARY KEY (id),
    CONSTRAINT uq_plans_code UNIQUE (code)
);

CREATE TABLE subscriptions (
    id           VARCHAR2(36) NOT NULL,
    tenant_id    VARCHAR2(36) NOT NULL,
    plan_id      VARCHAR2(36) NOT NULL,
    starts_on    DATE NOT NULL,
    ends_on      DATE,
    status_cd    NUMBER(4) NOT NULL,    -- CODES('SUB_STATUS')
    suspended_on DATE,
    CONSTRAINT pk_subscriptions PRIMARY KEY (id),
    CONSTRAINT fk_sub_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
    CONSTRAINT fk_sub_plan FOREIGN KEY (plan_id) REFERENCES plans (id)
);

CREATE TABLE usage_events (
    id          VARCHAR2(36) NOT NULL,
    tenant_id   VARCHAR2(36) NOT NULL,
    occurred_at TIMESTAMP NOT NULL,
    units       NUMBER(10) NOT NULL,
    kind_cd     NUMBER(4) NOT NULL,     -- CODES('USAGE_KIND')
    CONSTRAINT pk_usage_events PRIMARY KEY (id),
    CONSTRAINT fk_usage_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
);

CREATE TABLE rating_periods (
    id           VARCHAR2(36) NOT NULL,
    tenant_id    VARCHAR2(36) NOT NULL,
    period_start DATE NOT NULL,
    period_end   DATE NOT NULL,
    CONSTRAINT pk_rating_periods PRIMARY KEY (id),
    CONSTRAINT uq_rating_periods UNIQUE (tenant_id, period_start),
    CONSTRAINT fk_rp_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
);

CREATE TABLE rating_results (
    id              VARCHAR2(36) NOT NULL,
    period_id       VARCHAR2(36) NOT NULL,
    subscription_id VARCHAR2(36) NOT NULL,
    used_units      NUMBER(10) NOT NULL,
    quota_units     NUMBER(10) NOT NULL,
    rollover_units  NUMBER(10) NOT NULL,
    billable_units  NUMBER(10) NOT NULL,
    overage_amount  NUMBER(12,2) NOT NULL,
    created_at      TIMESTAMP NOT NULL,
    CONSTRAINT pk_rating_results PRIMARY KEY (id),
    CONSTRAINT fk_rr_period FOREIGN KEY (period_id) REFERENCES rating_periods (id),
    CONSTRAINT fk_rr_sub FOREIGN KEY (subscription_id) REFERENCES subscriptions (id)
);

CREATE TABLE invoices (
    id        VARCHAR2(36) NOT NULL,
    tenant_id VARCHAR2(36) NOT NULL,
    period_id VARCHAR2(36) NOT NULL,
    issued_at TIMESTAMP NOT NULL,
    subtotal  NUMBER(12,2) NOT NULL,
    tax       NUMBER(12,2) NOT NULL,
    total     NUMBER(12,2) NOT NULL,
    status_cd NUMBER(4) NOT NULL,       -- CODES('INV_STATUS')
    CONSTRAINT pk_invoices PRIMARY KEY (id),
    CONSTRAINT fk_inv_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
    CONSTRAINT fk_inv_period FOREIGN KEY (period_id) REFERENCES rating_periods (id)
);

CREATE TABLE invoice_lines (
    id          VARCHAR2(36) NOT NULL,
    invoice_id  VARCHAR2(36) NOT NULL,
    line_no     NUMBER(6) NOT NULL,
    line_type   VARCHAR2(10) NOT NULL,
    description VARCHAR2(400) NOT NULL,
    amount      NUMBER(12,2) NOT NULL,
    CONSTRAINT pk_invoice_lines PRIMARY KEY (id),
    CONSTRAINT uq_invoice_lines UNIQUE (invoice_id, line_no),
    CONSTRAINT fk_il_invoice FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE CASCADE
);

CREATE TABLE credit_notes (
    id               VARCHAR2(36) NOT NULL,
    tenant_id        VARCHAR2(36) NOT NULL,
    issued_on        DATE NOT NULL,
    amount           NUMBER(12,2) NOT NULL,
    remaining_amount NUMBER(12,2) NOT NULL,
    CONSTRAINT pk_credit_notes PRIMARY KEY (id),
    CONSTRAINT fk_cn_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
);

CREATE TABLE dunning_attempts (
    id            VARCHAR2(36) NOT NULL,
    tenant_id     VARCHAR2(36) NOT NULL,
    invoice_id    VARCHAR2(36) NOT NULL,
    attempt_no    NUMBER(4) NOT NULL,
    scheduled_for DATE NOT NULL,
    status_cd     NUMBER(4) NOT NULL,   -- CODES('DUN_STATUS')
    CONSTRAINT pk_dunning_attempts PRIMARY KEY (id),
    CONSTRAINT uq_dunning_attempts UNIQUE (invoice_id, attempt_no),
    CONSTRAINT fk_da_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
    CONSTRAINT fk_da_invoice FOREIGN KEY (invoice_id) REFERENCES invoices (id)
);

CREATE TABLE notifications (
    id        VARCHAR2(36) NOT NULL,
    tenant_id VARCHAR2(36) NOT NULL,
    kind_cd   NUMBER(4) NOT NULL,       -- CODES('NOTIF_KIND')
    sent_at   TIMESTAMP NOT NULL,
    CONSTRAINT pk_notifications PRIMARY KEY (id),
    CONSTRAINT uq_notifications UNIQUE (tenant_id, kind_cd, sent_at),
    CONSTRAINT fk_notif_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
);

-- Autonomous-transaction audit log, sequence+trigger "identity" style.
CREATE TABLE billing_audit_log (
    log_id    NUMBER(12) NOT NULL,
    logged_at DATE DEFAULT SYSDATE NOT NULL,
    module    VARCHAR2(30),
    message   VARCHAR2(4000),
    CONSTRAINT pk_billing_audit_log PRIMARY KEY (log_id)
);

CREATE SEQUENCE seq_billing_audit_log START WITH 1 INCREMENT BY 1 NOCACHE;

CREATE OR REPLACE TRIGGER trg_billing_audit_log_id
BEFORE INSERT ON billing_audit_log
FOR EACH ROW
BEGIN
    IF :NEW.log_id IS NULL THEN
        SELECT seq_billing_audit_log.NEXTVAL INTO :NEW.log_id FROM dual;
    END IF;
END;
/

-- Full-row-copy version history for subscriptions, maintained by trigger.
CREATE TABLE subscriptions_hist (
    hist_id      NUMBER(12) NOT NULL,
    hist_dt      VARCHAR2(20),          -- yes, the history timestamp is a string
    hist_op      VARCHAR2(3),
    id           VARCHAR2(36),
    tenant_id    VARCHAR2(36),
    plan_id      VARCHAR2(36),
    starts_on    DATE,
    ends_on      DATE,
    status_cd    NUMBER(4),
    suspended_on DATE,
    CONSTRAINT pk_subscriptions_hist PRIMARY KEY (hist_id)
);

CREATE SEQUENCE seq_subscriptions_hist START WITH 1 INCREMENT BY 1 NOCACHE;

CREATE OR REPLACE TRIGGER trg_subscriptions_hist
AFTER UPDATE OR DELETE ON subscriptions
FOR EACH ROW
DECLARE
    v_op VARCHAR2(3);
BEGIN
    IF UPDATING THEN v_op := 'UPD'; ELSE v_op := 'DEL'; END IF;
    INSERT INTO subscriptions_hist (
        hist_id, hist_dt, hist_op, id, tenant_id, plan_id,
        starts_on, ends_on, status_cd, suspended_on
    ) VALUES (
        seq_subscriptions_hist.NEXTVAL,
        TO_CHAR(SYSDATE, 'DD-MON-YY HH24:MI:SS'),
        v_op,
        :OLD.id, :OLD.tenant_id, :OLD.plan_id,
        :OLD.starts_on, :OLD.ends_on, :OLD.status_cd, :OLD.suspended_on
    );
END;
/

-- Business rule in a trigger: a cancelled subscription can never leave the
-- cancelled state (mirrors the CASE in the Postgres sp_change_plan).
CREATE OR REPLACE TRIGGER trg_sub_no_uncancel
BEFORE UPDATE OF status_cd ON subscriptions
FOR EACH ROW
BEGIN
    IF :OLD.status_cd = 30 THEN
        :NEW.status_cd := 30;
    END IF;
END;
/

-- Business rule in a trigger: usage must be positive and of a known kind.
CREATE OR REPLACE TRIGGER trg_usage_events_check
BEFORE INSERT ON usage_events
FOR EACH ROW
DECLARE
    v_cnt NUMBER;
BEGIN
    IF NVL(:NEW.units, 0) <= 0 THEN
        RAISE_APPLICATION_ERROR(-20001, 'units must be > 0');
    END IF;
    SELECT COUNT(*) INTO v_cnt FROM codes
     WHERE code_type = 'USAGE_KIND' AND code_val = :NEW.kind_cd;
    IF v_cnt = 0 THEN
        RAISE_APPLICATION_ERROR(-20002, 'unknown usage kind ' || :NEW.kind_cd);
    END IF;
END;
/

EXIT;
