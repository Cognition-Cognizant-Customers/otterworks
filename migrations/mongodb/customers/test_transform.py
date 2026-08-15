"""Unit tests for the pure transformer.

    make mongo-tp-customers-test

Fixture rows mirror what `testdata/legacy/oracle_billing_seed.py` plants,
including the two anomaly kinds the recon report must account for.
"""

from datetime import datetime, timezone

import pytest

import transform

NS = "demo"
MIGRATED_AT = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

CODES = {
    ("CUST_STATUS", 1): "active",
    ("CUST_STATUS", 99): "conversion-limbo",
    ("CUST_TYPE", 2): "business",
    ("PHONE_TYPE", 1): "main",
    ("PHONE_TYPE", 2): "billing",
}


def row(**overrides):
    base = {
        "CUST_ID": "0000c0ffee",
        "TENANT_ID": "tenant-1",
        "CUST_NO": "DEMO-00000001",
        "CUST_NAME": "Ada Lovelace",
        "CUST_NAME_UPPER": "ADA LOVELACE",
        "LEGAL_NAME": "Ada Lovelace LLC",
        "DBA_NAME": None,
        "ADDR_LINE_1": "12 Analytical Way",
        "ADDR_LINE_2": None,
        "ADDR_LINE_3": "ATTN: BILLING",
        "CITY": "Austin",
        "STATE_CD": "TX",
        "ZIP": "78701",
        "ZIP4": None,
        "COUNTRY_CD": "US",
        "MAIL_ADDR_LINE_1": "PO Box 9",
        "MAIL_CITY": "Austin",
        "MAIL_STATE_CD": "TX",
        "MAIL_ZIP": "78702",
        "PHONE1": "512-555-0100",
        "PHONE1_TYPE_CD": 1,
        "PHONE2": None,
        "PHONE2_TYPE_CD": 2,
        "FAX": None,
        "EMAIL_1": "ada@demo.example.com",
        "EMAIL_2": None,
        "EMAIL_3": None,
        "SIGNUP_DT": "04-JUL-11",
        "LAST_ACTIVITY_DT": "12-JAN-24",
        "LAST_INVOICE_DT": None,
        "STATUS_CD": 1,
        "SUB_STATUS_CD": 2,
        "CUST_TYPE_CD": 2,
        "SEGMENT_CD": 3,
        "REGION_CD": 7,
        "TAX_EXEMPT_YN": "N",
        "CREDIT_HOLD_YN": None,
        "VIP_YN": "Y",
        "CUR_BAL_AMT": 1234.5,
        "PAST_DUE_AMT": 0,
        "RELATED_ACCT_IDS": "12345,67890",
        "CHILD_ACCT_IDS": None,
        "PROMO_CODES_CSV": "SPRING24,VIP",
        "LEGACY_SYS_KEY": "SYS1-123456",
        "MAINFRAME_ACCT_NO": "000123456",
        "CONVERSION_BATCH_NO": 85559852,
        "ROW_VERSION_NO": 3,
        "FLAG_01": None,
        "FLAG_13": "Y",
        "UDF_07": "legacy-note",
        "UDF_08": None,
    }
    base.update(overrides)
    return base


def eav(name, value, attr_type="STR", created="01-FEB-24"):
    return {"ENTITY_ID": "0000c0ffee", "ATTR_NAME": name, "ATTR_VALUE": value,
            "ATTR_TYPE": attr_type, "CREATED_DT": created}


def run(source_row, eav_rows=()):
    return transform.transform_customer(source_row, list(eav_rows), CODES, NS,
                                        MIGRATED_AT)


def test_maps_the_core_customer_shape():
    doc = run(row()).doc

    assert doc["_id"] == "0000c0ffee"
    assert doc["tenantId"] == "tenant-1"
    assert doc["customerNo"] == "DEMO-00000001"
    assert doc["name"] == {"display": "Ada Lovelace",
                           "legal": "Ada Lovelace LLC"}
    assert "CUST_NAME_UPPER" not in doc and "dba" not in doc["name"]
    assert doc["status"] == "active"
    assert doc["customerType"] == "business"
    assert doc["subStatusCode"] == 2  # no 1:1 label: raw code retained
    assert doc["classification"] == {"segment": 3, "region": 7}
    assert doc["emails"] == ["ada@demo.example.com"]
    assert doc["balances"] == {"current": 1234.5, "pastDue": 0.0}
    assert doc["_migration"] == {"ns": "demo",
                                 "sourceTable": "OW_BILLING.CUSTOMER_MASTER",
                                 "migratedAt": MIGRATED_AT}


def test_repeating_groups_collapse_and_skip_empty_slots():
    doc = run(row()).doc

    assert doc["addresses"] == [
        {"type": "primary", "lines": ["12 Analytical Way", "ATTN: BILLING"],
         "city": "Austin", "state": "TX", "postalCode": "78701",
         "country": "US"},
        {"type": "mailing", "lines": ["PO Box 9"], "city": "Austin",
         "state": "TX", "postalCode": "78702"},
    ]
    # PHONE2 is empty, so its populated PHONE2_TYPE_CD produces no entry.
    assert doc["phones"] == [{"type": "main", "number": "512-555-0100"}]


def test_flags_become_booleans_and_nulls_are_omitted():
    doc = run(row()).doc

    assert doc["flags"] == {"taxExempt": False, "vip": True}
    assert "creditHold" not in doc["flags"]


def test_csv_lists_become_arrays_and_dates_become_bson_dates():
    doc = run(row()).doc

    assert doc["relatedAccountIds"] == ["12345", "67890"]
    assert doc["promoCodes"] == ["SPRING24", "VIP"]
    assert "childAccountIds" not in doc
    assert doc["dates"] == {"signup": datetime(2011, 7, 4, tzinfo=timezone.utc),
                            "lastActivity": datetime(2024, 1, 12,
                                                     tzinfo=timezone.utc)}


@pytest.mark.parametrize("raw,year", [("04-JUL-11", 2011), ("01-JAN-00", 2000),
                                      ("01-JAN-49", 2049), ("01-JAN-50", 1950),
                                      ("01-JAN-99", 1999)])
def test_two_digit_years_follow_oracle_rr_semantics(raw, year):
    assert transform.parse_legacy_date(raw).year == year


def test_sparse_udf_and_flag_values_only_when_populated():
    legacy = run(row()).doc["legacy"]

    assert legacy["sparse"] == {"FLAG_13": "Y", "UDF_07": "legacy-note"}
    assert legacy["sysKey"] == "SYS1-123456"
    assert legacy["conversionBatchNo"] == 85559852
    assert legacy["rowVersionNo"] == 3


@pytest.mark.parametrize("raw", ["31-FEB-24", "00-XXX-00", "99-999-99",
                                 "1/1/1900", "N/A", "29-FEB-23", "  -   -  ",
                                 "12-13-201"])
def test_dirty_signup_dates_are_quarantined_not_repaired(raw):
    result = run(row(SIGNUP_DT=raw))

    assert [(q.field, q.kind, q.raw) for q in result.quarantine] == [
        ("SIGNUP_DT", transform.KIND_DIRTY_DATE, raw)]
    assert result.doc["_quarantine"] == {"SIGNUP_DT": raw}
    assert "signup" not in result.doc["dates"]  # parsed field omitted
    # The customer is still migrated, so counts/checksums match the source.
    assert result.doc["_id"] == "0000c0ffee"
    assert result.doc["balances"]["current"] == 1234.5


@pytest.mark.parametrize("raw", [",,", "12345,,67890,", "A;B;C", " , 99 ,",
                                 "NULL,NONE,", "0000000000000000000000,"])
def test_malformed_related_acct_ids_are_quarantined_raw(raw):
    result = run(row(RELATED_ACCT_IDS=raw))

    assert [(q.field, q.kind) for q in result.quarantine] == [
        ("RELATED_ACCT_IDS", transform.KIND_MALFORMED_CSV)]
    assert result.doc["_quarantine"]["RELATED_ACCT_IDS"] == raw
    assert "relatedAccountIds" not in result.doc


def test_clean_edge_cases_are_not_flagged():
    assert transform.parse_csv_list(None) == ([], True)
    assert transform.parse_csv_list("") == ([], True)
    assert transform.parse_csv_list("42") == (["42"], True)


def test_eav_rows_fold_in_typed_by_attr_type():
    result = run(row(), [
        eav("PORTAL_THEME", "blue"),
        eav("Y2K_VERIFIED", "Y", "BOOL"),
        eav("LEGACY_TIER", "3.14", "NUM"),
        eav("CONVERTED_ON", "04-JUL-11", "DATE"),
        eav("COLLECTIONS_NOTE", "see ticket 48213"),
    ])

    assert result.doc["attributes"] == {
        "COLLECTIONS_NOTE": "see ticket 48213",
        "CONVERTED_ON": datetime(2011, 7, 4, tzinfo=timezone.utc),
        "LEGACY_TIER": 3.14,
        "PORTAL_THEME": "blue",
        "Y2K_VERIFIED": True,
    }
    assert result.eav_rows_consumed == 5
    assert result.attr_keys_folded == 5
    assert result.attr_conflicts == 0


def test_unparseable_typed_eav_values_stay_raw():
    attributes = run(row(), [eav("LEGACY_TIER", "gold", "NUM"),
                             eav("FAX_OPTOUT", "maybe", "BOOL"),
                             eav("CONVERTED_ON", "N/A", "DATE")]).doc["attributes"]

    assert attributes == {"LEGACY_TIER": "gold", "FAX_OPTOUT": "maybe",
                          "CONVERTED_ON": "N/A"}


def test_duplicate_attr_names_keep_newest_and_preserve_losers():
    result = run(row(), [
        eav("PORTAL_THEME", "blue", created="01-FEB-24"),
        eav("PORTAL_THEME", "green", created="09-MAR-24"),
        eav("PORTAL_THEME", "amber", created="03-JAN-24"),
    ])

    assert result.doc["attributes"] == {"PORTAL_THEME": "green"}
    assert result.doc["legacy"]["attributeConflicts"] == [
        {"name": "PORTAL_THEME", "value": "amber", "type": "STR",
         "createdAt": datetime(2024, 1, 3, tzinfo=timezone.utc)},
        {"name": "PORTAL_THEME", "value": "blue", "type": "STR",
         "createdAt": datetime(2024, 2, 1, tzinfo=timezone.utc)},
    ]
    # every source row is accounted for: folded keys + conflicts
    assert result.eav_rows_consumed == 3
    assert result.attr_keys_folded + result.attr_conflicts == 3


def test_same_created_date_ties_break_on_greatest_value():
    result = run(row(), [eav("LEGACY_TIER", "aaa", created="01-FEB-24"),
                         eav("LEGACY_TIER", "zzz", created="01-FEB-24")])

    assert result.doc["attributes"] == {"LEGACY_TIER": "zzz"}
    assert [c["value"] for c in
            result.doc["legacy"]["attributeConflicts"]] == ["aaa"]


def test_eav_name_colliding_with_a_modelled_field_stays_in_attributes():
    result = run(row(), [eav("status", "not-a-status"),
                         eav("balances", "999")])

    assert result.doc["status"] == "active"
    assert result.doc["balances"]["current"] == 1234.5
    assert result.doc["attributes"] == {"status": "not-a-status",
                                        "balances": "999"}


def test_attribute_keys_are_emitted_in_a_stable_order():
    forward = run(row(), [eav("B_ATTR", "1"), eav("A_ATTR", "2")])
    reverse = run(row(), [eav("A_ATTR", "2"), eav("B_ATTR", "1")])

    assert list(forward.doc["attributes"]) == list(reverse.doc["attributes"])


def test_unknown_status_code_keeps_the_raw_code():
    doc = run(row(STATUS_CD=42)).doc

    assert "status" not in doc
    assert doc["statusCode"] == 42


def test_transform_is_pure():
    source = row()
    before = dict(source)
    run(source, [eav("PORTAL_THEME", "blue")])

    assert source == before
