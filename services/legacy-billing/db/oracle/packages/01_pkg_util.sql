-- Shared "utilities" package. Package-state globals, dynamic SQL for a
-- simple lookup, and an autonomous-transaction logger that swallows every
-- error it ever meets.
WHENEVER SQLERROR EXIT SQL.SQLCODE

CREATE OR REPLACE PACKAGE pkg_ow_util AS
    -- Package-state globals: mutated by every caller, reset never.
    g_call_count   NUMBER := 0;
    g_last_module  VARCHAR2(30);
    g_last_uuid    VARCHAR2(36);

    FUNCTION f_md5_uuid(p_input IN VARCHAR2) RETURN VARCHAR2;
    FUNCTION f_code_desc(p_type IN VARCHAR2, p_val IN NUMBER) RETURN VARCHAR2;
    FUNCTION f_dt2str(p_dt IN DATE) RETURN VARCHAR2;
    FUNCTION f_str2dt(p_str IN VARCHAR2) RETURN DATE;
    PROCEDURE log_msg(p_module IN VARCHAR2, p_message IN VARCHAR2);
END pkg_ow_util;
/

CREATE OR REPLACE PACKAGE BODY pkg_ow_util AS

    FUNCTION f_md5_uuid(p_input IN VARCHAR2) RETURN VARCHAR2 IS
        v_hex VARCHAR2(32);
    BEGIN
        g_call_count := g_call_count + 1;
        SELECT LOWER(RAWTOHEX(STANDARD_HASH(UTL_RAW.CAST_TO_RAW(p_input), 'MD5')))
          INTO v_hex FROM dual;
        g_last_uuid := SUBSTR(v_hex, 1, 8) || '-' || SUBSTR(v_hex, 9, 4) || '-' ||
                       SUBSTR(v_hex, 13, 4) || '-' || SUBSTR(v_hex, 17, 4) || '-' ||
                       SUBSTR(v_hex, 21, 12);
        RETURN g_last_uuid;
    END f_md5_uuid;

    -- A static lookup done through EXECUTE IMMEDIATE, because someone in
    -- 2009 wanted it to be "generic".
    FUNCTION f_code_desc(p_type IN VARCHAR2, p_val IN NUMBER) RETURN VARCHAR2 IS
        v_desc VARCHAR2(80);
        v_sql  VARCHAR2(400);
    BEGIN
        v_sql := 'SELECT code_desc FROM codes WHERE code_type = :1 AND code_val = :2';
        EXECUTE IMMEDIATE v_sql INTO v_desc USING p_type, p_val;
        RETURN v_desc;
    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            RETURN 'UNKNOWN(' || TO_CHAR(NVL(p_val, -1)) || ')';
    END f_code_desc;

    -- Date/text gymnastics: dates travel as 'DD-MON-YY' strings through the
    -- horror tables and get re-parsed on the way back in.
    FUNCTION f_dt2str(p_dt IN DATE) RETURN VARCHAR2 IS
    BEGIN
        RETURN TO_CHAR(p_dt, 'DD-MON-YY', 'NLS_DATE_LANGUAGE=ENGLISH');
    END f_dt2str;

    FUNCTION f_str2dt(p_str IN VARCHAR2) RETURN DATE IS
    BEGIN
        RETURN TO_DATE(p_str, 'DD-MON-YY', 'NLS_DATE_LANGUAGE=ENGLISH');
    EXCEPTION
        WHEN OTHERS THEN
            -- Dirty dates come back as NULL and nobody is ever told.
            RETURN NULL;
    END f_str2dt;

    -- Autonomous-transaction "logging". Commits on its own, and if logging
    -- itself fails the error is silently discarded.
    PROCEDURE log_msg(p_module IN VARCHAR2, p_message IN VARCHAR2) IS
        PRAGMA AUTONOMOUS_TRANSACTION;
    BEGIN
        g_last_module := p_module;
        INSERT INTO billing_audit_log (module, message)
        VALUES (SUBSTR(p_module, 1, 30), SUBSTR(p_message, 1, 4000));
        COMMIT;
    EXCEPTION
        -- Still swallowed, but the autonomous transaction must be closed or
        -- Oracle raises ORA-06519 to the caller.
        WHEN OTHERS THEN ROLLBACK;
    END log_msg;

END pkg_ow_util;
/

SHOW ERRORS
EXIT;
