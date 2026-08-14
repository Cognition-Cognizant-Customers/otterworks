-- OW_BILLING "data-model horror" schema: the denormalized customer/invoice
-- estate that partner modernization demos migrate away from. Deliberately
-- awful on purpose: a 155-column CUSTOMER_MASTER with repeating groups
-- (ADDR_LINE_1..6, PHONE1..4), dates stored as VARCHAR2 'DD-MON-YY',
-- comma-separated ID lists in VARCHAR2, an EAV table, magic-number status
-- codes, and version-history-as-full-row-copies maintained by triggers.
WHENEVER SQLERROR EXIT SQL.SQLCODE

INSERT INTO codes VALUES ('CUST_STATUS',  1, 'active');
INSERT INTO codes VALUES ('CUST_STATUS',  2, 'suspended');
INSERT INTO codes VALUES ('CUST_STATUS',  3, 'closed');
INSERT INTO codes VALUES ('CUST_STATUS', 99, 'conversion-limbo');
INSERT INTO codes VALUES ('CUST_TYPE',    1, 'individual');
INSERT INTO codes VALUES ('CUST_TYPE',    2, 'business');
INSERT INTO codes VALUES ('CUST_TYPE',    3, 'government');
INSERT INTO codes VALUES ('PHONE_TYPE',   1, 'main');
INSERT INTO codes VALUES ('PHONE_TYPE',   2, 'billing');
INSERT INTO codes VALUES ('PHONE_TYPE',   3, 'fax');
INSERT INTO codes VALUES ('PHONE_TYPE',   4, 'after-hours');
COMMIT;

CREATE TABLE customer_master (
    cust_id                VARCHAR2(36) NOT NULL,
    cust_seq_no            NUMBER(12),
    tenant_id              VARCHAR2(36),
    cust_no                VARCHAR2(20),
    cust_name              VARCHAR2(200),
    cust_name_upper        VARCHAR2(200),
    legal_name             VARCHAR2(200),
    dba_name               VARCHAR2(200),
    addr_line_1            VARCHAR2(120),
    addr_line_2            VARCHAR2(120),
    addr_line_3            VARCHAR2(120),
    addr_line_4            VARCHAR2(120),
    addr_line_5            VARCHAR2(120),
    addr_line_6            VARCHAR2(120),
    city                   VARCHAR2(80),
    state_cd               VARCHAR2(4),
    zip                    VARCHAR2(12),
    zip4                   VARCHAR2(6),
    country_cd             VARCHAR2(4),
    mail_addr_line_1       VARCHAR2(120),
    mail_addr_line_2       VARCHAR2(120),
    mail_addr_line_3       VARCHAR2(120),
    mail_addr_line_4       VARCHAR2(120),
    mail_addr_line_5       VARCHAR2(120),
    mail_addr_line_6       VARCHAR2(120),
    mail_city              VARCHAR2(80),
    mail_state_cd          VARCHAR2(4),
    mail_zip               VARCHAR2(12),
    phone1                 VARCHAR2(25),
    phone2                 VARCHAR2(25),
    phone3                 VARCHAR2(25),
    phone4                 VARCHAR2(25),
    phone1_type_cd         NUMBER(4),
    phone2_type_cd         NUMBER(4),
    phone3_type_cd         NUMBER(4),
    phone4_type_cd         NUMBER(4),
    fax                    VARCHAR2(25),
    email_1                VARCHAR2(200),
    email_2                VARCHAR2(200),
    email_3                VARCHAR2(200),
    signup_dt              VARCHAR2(9),
    last_activity_dt       VARCHAR2(9),
    last_invoice_dt        VARCHAR2(9),
    last_payment_dt        VARCHAR2(9),
    terminate_dt           VARCHAR2(9),
    status_cd              NUMBER(4),
    sub_status_cd          NUMBER(4),
    cust_type_cd           NUMBER(4),
    segment_cd             NUMBER(4),
    region_cd              NUMBER(4),
    territory_cd           NUMBER(4),
    channel_cd             NUMBER(4),
    rate_class_cd          NUMBER(4),
    tax_exempt_yn          CHAR(1),
    credit_hold_yn         CHAR(1),
    dunning_exempt_yn      CHAR(1),
    vip_yn                 CHAR(1),
    cur_bal_amt            NUMBER(14,2),
    past_due_amt           NUMBER(14,2),
    ytd_billed_amt         NUMBER(14,2),
    ltd_billed_amt         NUMBER(14,2),
    ytd_paid_amt           NUMBER(14,2),
    credit_limit_amt       NUMBER(14,2),
    related_acct_ids       VARCHAR2(2000),
    child_acct_ids         VARCHAR2(2000),
    promo_codes_csv        VARCHAR2(1000),
    contact_notes          VARCHAR2(4000),
    legacy_sys_key         VARCHAR2(50),
    mainframe_acct_no      VARCHAR2(30),
    conversion_batch_no    NUMBER(8),
    flag_01                CHAR(1),
    flag_02                CHAR(1),
    flag_03                CHAR(1),
    flag_04                CHAR(1),
    flag_05                CHAR(1),
    flag_06                CHAR(1),
    flag_07                CHAR(1),
    flag_08                CHAR(1),
    flag_09                CHAR(1),
    flag_10                CHAR(1),
    flag_11                CHAR(1),
    flag_12                CHAR(1),
    flag_13                CHAR(1),
    flag_14                CHAR(1),
    flag_15                CHAR(1),
    flag_16                CHAR(1),
    flag_17                CHAR(1),
    flag_18                CHAR(1),
    flag_19                CHAR(1),
    flag_20                CHAR(1),
    udf_01                 VARCHAR2(100),
    udf_02                 VARCHAR2(100),
    udf_03                 VARCHAR2(100),
    udf_04                 VARCHAR2(100),
    udf_05                 VARCHAR2(100),
    udf_06                 VARCHAR2(100),
    udf_07                 VARCHAR2(100),
    udf_08                 VARCHAR2(100),
    udf_09                 VARCHAR2(100),
    udf_10                 VARCHAR2(100),
    udf_11                 VARCHAR2(100),
    udf_12                 VARCHAR2(100),
    udf_13                 VARCHAR2(100),
    udf_14                 VARCHAR2(100),
    udf_15                 VARCHAR2(100),
    udf_16                 VARCHAR2(100),
    udf_17                 VARCHAR2(100),
    udf_18                 VARCHAR2(100),
    udf_19                 VARCHAR2(100),
    udf_20                 VARCHAR2(100),
    udf_21                 VARCHAR2(100),
    udf_22                 VARCHAR2(100),
    udf_23                 VARCHAR2(100),
    udf_24                 VARCHAR2(100),
    udf_25                 VARCHAR2(100),
    udf_26                 VARCHAR2(100),
    udf_27                 VARCHAR2(100),
    udf_28                 VARCHAR2(100),
    udf_29                 VARCHAR2(100),
    udf_30                 VARCHAR2(100),
    udf_31                 VARCHAR2(100),
    udf_32                 VARCHAR2(100),
    udf_33                 VARCHAR2(100),
    udf_34                 VARCHAR2(100),
    udf_35                 VARCHAR2(100),
    udf_36                 VARCHAR2(100),
    udf_37                 VARCHAR2(100),
    udf_38                 VARCHAR2(100),
    udf_39                 VARCHAR2(100),
    udf_40                 VARCHAR2(100),
    udf_amt_01             NUMBER(14,2),
    udf_amt_02             NUMBER(14,2),
    udf_amt_03             NUMBER(14,2),
    udf_amt_04             NUMBER(14,2),
    udf_amt_05             NUMBER(14,2),
    udf_amt_06             NUMBER(14,2),
    udf_amt_07             NUMBER(14,2),
    udf_amt_08             NUMBER(14,2),
    udf_amt_09             NUMBER(14,2),
    udf_amt_10             NUMBER(14,2),
    udf_dt_01              VARCHAR2(9),
    udf_dt_02              VARCHAR2(9),
    udf_dt_03              VARCHAR2(9),
    udf_dt_04              VARCHAR2(9),
    udf_dt_05              VARCHAR2(9),
    udf_dt_06              VARCHAR2(9),
    udf_dt_07              VARCHAR2(9),
    udf_dt_08              VARCHAR2(9),
    udf_dt_09              VARCHAR2(9),
    udf_dt_10              VARCHAR2(9),
    created_by             VARCHAR2(30),
    created_dt             DATE,
    updated_by             VARCHAR2(30),
    updated_dt             DATE,
    row_version_no         NUMBER(8),
    CONSTRAINT pk_customer_master PRIMARY KEY (cust_id)
);

CREATE TABLE customer_master_hist (
    hist_id                NUMBER(12) NOT NULL,
    hist_dt                VARCHAR2(20),
    hist_op                VARCHAR2(3),
    cust_id                VARCHAR2(36),
    cust_seq_no            NUMBER(12),
    tenant_id              VARCHAR2(36),
    cust_no                VARCHAR2(20),
    cust_name              VARCHAR2(200),
    cust_name_upper        VARCHAR2(200),
    legal_name             VARCHAR2(200),
    dba_name               VARCHAR2(200),
    addr_line_1            VARCHAR2(120),
    addr_line_2            VARCHAR2(120),
    addr_line_3            VARCHAR2(120),
    addr_line_4            VARCHAR2(120),
    addr_line_5            VARCHAR2(120),
    addr_line_6            VARCHAR2(120),
    city                   VARCHAR2(80),
    state_cd               VARCHAR2(4),
    zip                    VARCHAR2(12),
    zip4                   VARCHAR2(6),
    country_cd             VARCHAR2(4),
    mail_addr_line_1       VARCHAR2(120),
    mail_addr_line_2       VARCHAR2(120),
    mail_addr_line_3       VARCHAR2(120),
    mail_addr_line_4       VARCHAR2(120),
    mail_addr_line_5       VARCHAR2(120),
    mail_addr_line_6       VARCHAR2(120),
    mail_city              VARCHAR2(80),
    mail_state_cd          VARCHAR2(4),
    mail_zip               VARCHAR2(12),
    phone1                 VARCHAR2(25),
    phone2                 VARCHAR2(25),
    phone3                 VARCHAR2(25),
    phone4                 VARCHAR2(25),
    phone1_type_cd         NUMBER(4),
    phone2_type_cd         NUMBER(4),
    phone3_type_cd         NUMBER(4),
    phone4_type_cd         NUMBER(4),
    fax                    VARCHAR2(25),
    email_1                VARCHAR2(200),
    email_2                VARCHAR2(200),
    email_3                VARCHAR2(200),
    signup_dt              VARCHAR2(9),
    last_activity_dt       VARCHAR2(9),
    last_invoice_dt        VARCHAR2(9),
    last_payment_dt        VARCHAR2(9),
    terminate_dt           VARCHAR2(9),
    status_cd              NUMBER(4),
    sub_status_cd          NUMBER(4),
    cust_type_cd           NUMBER(4),
    segment_cd             NUMBER(4),
    region_cd              NUMBER(4),
    territory_cd           NUMBER(4),
    channel_cd             NUMBER(4),
    rate_class_cd          NUMBER(4),
    tax_exempt_yn          CHAR(1),
    credit_hold_yn         CHAR(1),
    dunning_exempt_yn      CHAR(1),
    vip_yn                 CHAR(1),
    cur_bal_amt            NUMBER(14,2),
    past_due_amt           NUMBER(14,2),
    ytd_billed_amt         NUMBER(14,2),
    ltd_billed_amt         NUMBER(14,2),
    ytd_paid_amt           NUMBER(14,2),
    credit_limit_amt       NUMBER(14,2),
    related_acct_ids       VARCHAR2(2000),
    child_acct_ids         VARCHAR2(2000),
    promo_codes_csv        VARCHAR2(1000),
    contact_notes          VARCHAR2(4000),
    legacy_sys_key         VARCHAR2(50),
    mainframe_acct_no      VARCHAR2(30),
    conversion_batch_no    NUMBER(8),
    flag_01                CHAR(1),
    flag_02                CHAR(1),
    flag_03                CHAR(1),
    flag_04                CHAR(1),
    flag_05                CHAR(1),
    flag_06                CHAR(1),
    flag_07                CHAR(1),
    flag_08                CHAR(1),
    flag_09                CHAR(1),
    flag_10                CHAR(1),
    flag_11                CHAR(1),
    flag_12                CHAR(1),
    flag_13                CHAR(1),
    flag_14                CHAR(1),
    flag_15                CHAR(1),
    flag_16                CHAR(1),
    flag_17                CHAR(1),
    flag_18                CHAR(1),
    flag_19                CHAR(1),
    flag_20                CHAR(1),
    udf_01                 VARCHAR2(100),
    udf_02                 VARCHAR2(100),
    udf_03                 VARCHAR2(100),
    udf_04                 VARCHAR2(100),
    udf_05                 VARCHAR2(100),
    udf_06                 VARCHAR2(100),
    udf_07                 VARCHAR2(100),
    udf_08                 VARCHAR2(100),
    udf_09                 VARCHAR2(100),
    udf_10                 VARCHAR2(100),
    udf_11                 VARCHAR2(100),
    udf_12                 VARCHAR2(100),
    udf_13                 VARCHAR2(100),
    udf_14                 VARCHAR2(100),
    udf_15                 VARCHAR2(100),
    udf_16                 VARCHAR2(100),
    udf_17                 VARCHAR2(100),
    udf_18                 VARCHAR2(100),
    udf_19                 VARCHAR2(100),
    udf_20                 VARCHAR2(100),
    udf_21                 VARCHAR2(100),
    udf_22                 VARCHAR2(100),
    udf_23                 VARCHAR2(100),
    udf_24                 VARCHAR2(100),
    udf_25                 VARCHAR2(100),
    udf_26                 VARCHAR2(100),
    udf_27                 VARCHAR2(100),
    udf_28                 VARCHAR2(100),
    udf_29                 VARCHAR2(100),
    udf_30                 VARCHAR2(100),
    udf_31                 VARCHAR2(100),
    udf_32                 VARCHAR2(100),
    udf_33                 VARCHAR2(100),
    udf_34                 VARCHAR2(100),
    udf_35                 VARCHAR2(100),
    udf_36                 VARCHAR2(100),
    udf_37                 VARCHAR2(100),
    udf_38                 VARCHAR2(100),
    udf_39                 VARCHAR2(100),
    udf_40                 VARCHAR2(100),
    udf_amt_01             NUMBER(14,2),
    udf_amt_02             NUMBER(14,2),
    udf_amt_03             NUMBER(14,2),
    udf_amt_04             NUMBER(14,2),
    udf_amt_05             NUMBER(14,2),
    udf_amt_06             NUMBER(14,2),
    udf_amt_07             NUMBER(14,2),
    udf_amt_08             NUMBER(14,2),
    udf_amt_09             NUMBER(14,2),
    udf_amt_10             NUMBER(14,2),
    udf_dt_01              VARCHAR2(9),
    udf_dt_02              VARCHAR2(9),
    udf_dt_03              VARCHAR2(9),
    udf_dt_04              VARCHAR2(9),
    udf_dt_05              VARCHAR2(9),
    udf_dt_06              VARCHAR2(9),
    udf_dt_07              VARCHAR2(9),
    udf_dt_08              VARCHAR2(9),
    udf_dt_09              VARCHAR2(9),
    udf_dt_10              VARCHAR2(9),
    created_by             VARCHAR2(30),
    created_dt             DATE,
    updated_by             VARCHAR2(30),
    updated_dt             DATE,
    row_version_no         NUMBER(8),
    CONSTRAINT pk_customer_master_hist PRIMARY KEY (hist_id)
);

CREATE SEQUENCE seq_customer_master START WITH 100000 INCREMENT BY 1 NOCACHE;
CREATE SEQUENCE seq_customer_master_hist START WITH 1 INCREMENT BY 1 NOCACHE;

CREATE OR REPLACE TRIGGER trg_customer_master_seq
BEFORE INSERT ON customer_master
FOR EACH ROW
BEGIN
    IF :NEW.cust_seq_no IS NULL THEN
        SELECT seq_customer_master.NEXTVAL INTO :NEW.cust_seq_no FROM dual;
    END IF;
    :NEW.cust_name_upper := UPPER(:NEW.cust_name);
    :NEW.row_version_no := NVL(:NEW.row_version_no, 1);
END;
/

CREATE OR REPLACE TRIGGER trg_customer_master_hist
AFTER UPDATE OR DELETE ON customer_master
FOR EACH ROW
DECLARE
    v_op VARCHAR2(3);
BEGIN
    IF UPDATING THEN v_op := 'UPD'; ELSE v_op := 'DEL'; END IF;
    INSERT INTO customer_master_hist (
        hist_id, hist_dt, hist_op,
        cust_id, cust_seq_no, tenant_id, cust_no, cust_name, cust_name_upper, legal_name, dba_name, addr_line_1, addr_line_2, addr_line_3, addr_line_4, addr_line_5, addr_line_6, city, state_cd, zip, zip4, country_cd, mail_addr_line_1, mail_addr_line_2, mail_addr_line_3, mail_addr_line_4, mail_addr_line_5, mail_addr_line_6, mail_city, mail_state_cd, mail_zip, phone1, phone2, phone3, phone4, phone1_type_cd, phone2_type_cd, phone3_type_cd, phone4_type_cd, fax, email_1, email_2, email_3, signup_dt, last_activity_dt, last_invoice_dt, last_payment_dt, terminate_dt, status_cd, sub_status_cd, cust_type_cd, segment_cd, region_cd, territory_cd, channel_cd, rate_class_cd, tax_exempt_yn, credit_hold_yn, dunning_exempt_yn, vip_yn, cur_bal_amt, past_due_amt, ytd_billed_amt, ltd_billed_amt, ytd_paid_amt, credit_limit_amt, related_acct_ids, child_acct_ids, promo_codes_csv, contact_notes, legacy_sys_key, mainframe_acct_no, conversion_batch_no, flag_01, flag_02, flag_03, flag_04, flag_05, flag_06, flag_07, flag_08, flag_09, flag_10, flag_11, flag_12, flag_13, flag_14, flag_15, flag_16, flag_17, flag_18, flag_19, flag_20, udf_01, udf_02, udf_03, udf_04, udf_05, udf_06, udf_07, udf_08, udf_09, udf_10, udf_11, udf_12, udf_13, udf_14, udf_15, udf_16, udf_17, udf_18, udf_19, udf_20, udf_21, udf_22, udf_23, udf_24, udf_25, udf_26, udf_27, udf_28, udf_29, udf_30, udf_31, udf_32, udf_33, udf_34, udf_35, udf_36, udf_37, udf_38, udf_39, udf_40, udf_amt_01, udf_amt_02, udf_amt_03, udf_amt_04, udf_amt_05, udf_amt_06, udf_amt_07, udf_amt_08, udf_amt_09, udf_amt_10, udf_dt_01, udf_dt_02, udf_dt_03, udf_dt_04, udf_dt_05, udf_dt_06, udf_dt_07, udf_dt_08, udf_dt_09, udf_dt_10, created_by, created_dt, updated_by, updated_dt, row_version_no
    ) VALUES (
        seq_customer_master_hist.NEXTVAL,
        TO_CHAR(SYSDATE, 'DD-MON-YY HH24:MI:SS'), v_op,
        :OLD.cust_id, :OLD.cust_seq_no, :OLD.tenant_id, :OLD.cust_no, :OLD.cust_name, :OLD.cust_name_upper, :OLD.legal_name, :OLD.dba_name, :OLD.addr_line_1, :OLD.addr_line_2, :OLD.addr_line_3, :OLD.addr_line_4, :OLD.addr_line_5, :OLD.addr_line_6, :OLD.city, :OLD.state_cd, :OLD.zip, :OLD.zip4, :OLD.country_cd, :OLD.mail_addr_line_1, :OLD.mail_addr_line_2, :OLD.mail_addr_line_3, :OLD.mail_addr_line_4, :OLD.mail_addr_line_5, :OLD.mail_addr_line_6, :OLD.mail_city, :OLD.mail_state_cd, :OLD.mail_zip, :OLD.phone1, :OLD.phone2, :OLD.phone3, :OLD.phone4, :OLD.phone1_type_cd, :OLD.phone2_type_cd, :OLD.phone3_type_cd, :OLD.phone4_type_cd, :OLD.fax, :OLD.email_1, :OLD.email_2, :OLD.email_3, :OLD.signup_dt, :OLD.last_activity_dt, :OLD.last_invoice_dt, :OLD.last_payment_dt, :OLD.terminate_dt, :OLD.status_cd, :OLD.sub_status_cd, :OLD.cust_type_cd, :OLD.segment_cd, :OLD.region_cd, :OLD.territory_cd, :OLD.channel_cd, :OLD.rate_class_cd, :OLD.tax_exempt_yn, :OLD.credit_hold_yn, :OLD.dunning_exempt_yn, :OLD.vip_yn, :OLD.cur_bal_amt, :OLD.past_due_amt, :OLD.ytd_billed_amt, :OLD.ltd_billed_amt, :OLD.ytd_paid_amt, :OLD.credit_limit_amt, :OLD.related_acct_ids, :OLD.child_acct_ids, :OLD.promo_codes_csv, :OLD.contact_notes, :OLD.legacy_sys_key, :OLD.mainframe_acct_no, :OLD.conversion_batch_no, :OLD.flag_01, :OLD.flag_02, :OLD.flag_03, :OLD.flag_04, :OLD.flag_05, :OLD.flag_06, :OLD.flag_07, :OLD.flag_08, :OLD.flag_09, :OLD.flag_10, :OLD.flag_11, :OLD.flag_12, :OLD.flag_13, :OLD.flag_14, :OLD.flag_15, :OLD.flag_16, :OLD.flag_17, :OLD.flag_18, :OLD.flag_19, :OLD.flag_20, :OLD.udf_01, :OLD.udf_02, :OLD.udf_03, :OLD.udf_04, :OLD.udf_05, :OLD.udf_06, :OLD.udf_07, :OLD.udf_08, :OLD.udf_09, :OLD.udf_10, :OLD.udf_11, :OLD.udf_12, :OLD.udf_13, :OLD.udf_14, :OLD.udf_15, :OLD.udf_16, :OLD.udf_17, :OLD.udf_18, :OLD.udf_19, :OLD.udf_20, :OLD.udf_21, :OLD.udf_22, :OLD.udf_23, :OLD.udf_24, :OLD.udf_25, :OLD.udf_26, :OLD.udf_27, :OLD.udf_28, :OLD.udf_29, :OLD.udf_30, :OLD.udf_31, :OLD.udf_32, :OLD.udf_33, :OLD.udf_34, :OLD.udf_35, :OLD.udf_36, :OLD.udf_37, :OLD.udf_38, :OLD.udf_39, :OLD.udf_40, :OLD.udf_amt_01, :OLD.udf_amt_02, :OLD.udf_amt_03, :OLD.udf_amt_04, :OLD.udf_amt_05, :OLD.udf_amt_06, :OLD.udf_amt_07, :OLD.udf_amt_08, :OLD.udf_amt_09, :OLD.udf_amt_10, :OLD.udf_dt_01, :OLD.udf_dt_02, :OLD.udf_dt_03, :OLD.udf_dt_04, :OLD.udf_dt_05, :OLD.udf_dt_06, :OLD.udf_dt_07, :OLD.udf_dt_08, :OLD.udf_dt_09, :OLD.udf_dt_10, :OLD.created_by, :OLD.created_dt, :OLD.updated_by, :OLD.updated_dt, :OLD.row_version_no
    );
END;
/

-- Entity-attribute-value dumping ground. Everything that never got a real
-- column ends up here, typed as strings.
CREATE TABLE entity_attr_value (
    eav_id      NUMBER(14) NOT NULL,
    entity_type VARCHAR2(30) NOT NULL,   -- 'CUSTOMER', 'INVOICE', ...
    entity_id   VARCHAR2(36) NOT NULL,
    attr_name   VARCHAR2(100) NOT NULL,
    attr_value  VARCHAR2(4000),
    attr_type   VARCHAR2(10) DEFAULT 'STR',
    created_dt  VARCHAR2(9),             -- DD-MON-YY, naturally
    CONSTRAINT pk_entity_attr_value PRIMARY KEY (eav_id)
);

CREATE SEQUENCE seq_entity_attr_value START WITH 1 INCREMENT BY 1 CACHE 1000;

CREATE OR REPLACE TRIGGER trg_entity_attr_value_seq
BEFORE INSERT ON entity_attr_value
FOR EACH ROW
BEGIN
    IF :NEW.eav_id IS NULL THEN
        SELECT seq_entity_attr_value.NEXTVAL INTO :NEW.eav_id FROM dual;
    END IF;
END;
/

-- Bulk denormalized invoice-line estate (distinct from the transactional
-- INVOICE_LINES used by the packages): the mainframe-conversion feed the
-- seeder fills at scale. No FKs, customer fields copied onto every row,
-- dates as strings, amounts recomputed nowhere.
CREATE TABLE invoice_line (
    line_id        VARCHAR2(36) NOT NULL,
    invoice_no     VARCHAR2(30),
    invoice_id     VARCHAR2(36),          -- "references" INVOICE_HEADER... usually
    cust_id        VARCHAR2(36),          -- unenforced pointer at CUSTOMER_MASTER
    cust_no        VARCHAR2(20),
    cust_name      VARCHAR2(200),         -- denormalized copy
    tenant_id      VARCHAR2(36),
    line_no        NUMBER(6),
    line_type_cd   NUMBER(4),
    item_desc      VARCHAR2(400),
    qty            NUMBER(12,3),
    unit_price     NUMBER(14,4),
    amount         NUMBER(14,2),
    tax_amt        NUMBER(14,2),
    invoice_dt     VARCHAR2(9),           -- DD-MON-YY text
    service_period VARCHAR2(20),          -- 'MMYYYY-MMYYYY' text
    posted_yn      CHAR(1),
    gl_acct_csv    VARCHAR2(200),         -- comma-separated GL account splits
    batch_no       NUMBER(8),
    src_system     VARCHAR2(10),
    CONSTRAINT pk_invoice_line PRIMARY KEY (line_id)
);

CREATE TABLE invoice_header (
    invoice_id  VARCHAR2(36) NOT NULL,
    invoice_no  VARCHAR2(30),
    cust_id     VARCHAR2(36),
    tenant_id   VARCHAR2(36),
    invoice_dt  VARCHAR2(9),              -- DD-MON-YY text
    due_dt      VARCHAR2(9),
    status_cd   NUMBER(4),                -- CODES('INV_STATUS')
    total_amt   NUMBER(14,2),
    CONSTRAINT pk_invoice_header PRIMARY KEY (invoice_id)
);

EXIT;
