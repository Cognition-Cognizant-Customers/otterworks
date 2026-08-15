"""Unit tests for the pure invoice transforms (no Oracle, no Atlas)."""

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from bson.decimal128 import Decimal128

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import transform  # noqa: E402

NS = "demo"
MIGRATED_AT = datetime(2026, 8, 1, tzinfo=timezone.utc)
STATUS_CODES = {10: "draft", 20: "issued", 30: "paid", 40: "overdue"}


def line_row(**overrides) -> dict:
    row = {
        "LINE_ID": "11111111-1111-1111-1111-111111111111",
        "INVOICE_NO": "DEMO-000000001",
        "INVOICE_ID": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "CUST_ID": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "CUST_NO": "DEMO-00000001",
        "CUST_NAME": "Alex Otter",
        "TENANT_ID": "tttttttt-tttt-tttt-tttt-tttttttttttt",
        "LINE_NO": Decimal("7"),
        "LINE_TYPE_CD": Decimal("2"),
        "ITEM_DESC": "API overage",
        "QTY": Decimal("12.000"),
        "UNIT_PRICE": Decimal("3.5000"),
        "AMOUNT": Decimal("42.00"),
        "TAX_AMT": Decimal("3.47"),
        "INVOICE_DT": "14-MAR-21",
        "SERVICE_PERIOD": "012019-032019",
        "POSTED_YN": "Y",
        "GL_ACCT_CSV": "40001,40237",
        "BATCH_NO": Decimal("85559852"),
        "SRC_SYSTEM": "MAINFRAME",
    }
    row.update(overrides)
    return row


def header_row(**overrides) -> dict:
    row = {
        "INVOICE_ID": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "INVOICE_NO": "DEMO-000000001",
        "CUST_ID": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "TENANT_ID": "tttttttt-tttt-tttt-tttt-tttttttttttt",
        "INVOICE_DT": "03-JAN-19",
        "DUE_DT": "02-FEB-19",
        "STATUS_CD": Decimal("20"),
        "TOTAL_AMT": Decimal("1234.56"),
        "BATCH_NO": Decimal("85559852"),
    }
    row.update(overrides)
    return row


# ── scalar parsers ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("03-JAN-19", datetime(2019, 1, 3)),
    ("28-dec-25", datetime(2025, 12, 28)),
    ("31-FEB-24", None),
    ("N/A", None),
    ("1/1/1900", None),
    ("  -   -  ", None),
    ("12-13-201", None),
    (None, None),
])
def test_parse_legacy_date(text, expected):
    assert transform.parse_legacy_date(text) == expected


def test_parse_service_period_spans_whole_months():
    assert transform.parse_service_period("012019-032019") == {
        "from": datetime(2019, 1, 1),
        "to": datetime(2019, 3, 31),
    }
    assert transform.parse_service_period("022024-022024")["to"] == \
        datetime(2024, 2, 29)


@pytest.mark.parametrize("text", ["", None, "012019", "0119-0319",
                                  "132019-142019", "AB2019-CD2019",
                                  "012019-032019-042019"])
def test_parse_service_period_rejects_junk(text):
    assert transform.parse_service_period(text) is None


@pytest.mark.parametrize("csv,expected,unparsed", [
    ("40001", [40001], []),
    ("40001,40237,49999", [40001, 40237, 49999], []),
    (" 40001 , 40237 ", [40001, 40237], []),
    ("40001,,40237,", [40001, 40237], []),
    ("40001,GL-BAD", [40001], ["GL-BAD"]),
    (None, [], []),
])
def test_parse_gl_accounts(csv, expected, unparsed):
    assert transform.parse_gl_accounts(csv) == (expected, unparsed)


@pytest.mark.parametrize("flag,expected", [
    ("Y", True), ("y", True), ("N", False), ("n", False),
    (None, None), ("", None), ("X", None),
])
def test_parse_posted(flag, expected):
    assert transform.parse_posted(flag) is expected


def test_decimal128_refuses_binary_floats():
    with pytest.raises(TypeError):
        transform.decimal128(42.01)
    assert transform.decimal128(Decimal("42.01")) == Decimal128("42.01")
    assert transform.decimal128(None) is None


# ── embedded lines ────────────────────────────────────────────────────────────


def test_transform_line_shape_and_dropped_customer_copies():
    findings = transform.Findings()
    line = transform.transform_line(line_row(), findings)

    assert line == {
        "lineId": "11111111-1111-1111-1111-111111111111",
        "lineNo": 7,
        "type": 2,
        "description": "API overage",
        "qty": Decimal128("12.000"),
        "unitPrice": Decimal128("3.5000"),
        "amount": Decimal128("42.00"),
        "taxAmount": Decimal128("3.47"),
        "glAccounts": [40001, 40237],
        "srcSystem": "MAINFRAME",
        "servicePeriod": {"from": datetime(2019, 1, 1), "to": datetime(2019, 3, 31)},
        "posted": True,
    }
    assert findings.counts == {}
    assert transform.DROPPED_LINE_COLUMNS == ("CUST_ID", "CUST_NO", "CUST_NAME")


def test_null_posted_yn_leaves_the_field_absent():
    line = transform.transform_line(line_row(POSTED_YN=None), transform.Findings())
    assert "posted" not in line

    line = transform.transform_line(line_row(POSTED_YN="N"), transform.Findings())
    assert line["posted"] is False


def test_unparseable_service_period_is_reported_not_repaired():
    findings = transform.Findings()
    line = transform.transform_line(line_row(SERVICE_PERIOD="0119-0319"), findings)

    assert "servicePeriod" not in line
    assert findings.counts == {"unparseable_service_period": 1}
    assert findings.samples["unparseable_service_period"][0]["detail"] == "0119-0319"


def test_reversed_service_period_is_kept_and_reported():
    findings = transform.Findings()
    line = transform.transform_line(line_row(SERVICE_PERIOD="032025-032015"),
                                    findings)

    assert line["servicePeriod"]["from"] > line["servicePeriod"]["to"]
    assert findings.counts == {"reversed_service_period": 1}


def test_non_numeric_gl_account_is_preserved_alongside_the_parsed_ones():
    findings = transform.Findings()
    line = transform.transform_line(line_row(GL_ACCT_CSV="40001,GL-BAD"), findings)

    assert line["glAccounts"] == [40001]
    assert line["glAccountsUnparsed"] == ["GL-BAD"]
    assert findings.counts == {"unparsed_gl_account": 1}


# ── invoices ──────────────────────────────────────────────────────────────────


def test_transform_invoice_embeds_lines_and_keeps_both_totals():
    lines = [line_row(LINE_ID=f"line-{i}", AMOUNT=Decimal("10.01"))
             for i in range(3)]
    doc, findings = transform.transform_invoice(
        header_row(), lines, STATUS_CODES, NS, MIGRATED_AT)

    assert doc["_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert doc["status"] == "issued"
    assert doc["invoiceDate"] == datetime(2019, 1, 3)
    assert doc["dueDate"] == datetime(2019, 2, 2)
    assert doc["lineCount"] == 3
    assert doc["lineTotal"] == Decimal128("30.03")
    # header total and line total disagree in the estate: both survive as-is
    assert doc["totalAmount"] == Decimal128("1234.56")
    assert [line["lineId"] for line in doc["lines"]] == ["line-0", "line-1", "line-2"]
    assert doc["legacy"] == {"batchNo": 85559852}
    assert doc["_migration"] == {"ns": NS,
                                 "sourceTable": "OW_BILLING.INVOICE_HEADER",
                                 "migratedAt": MIGRATED_AT}
    assert findings.counts == {}


def test_invoice_with_no_lines_is_still_migrated():
    doc, findings = transform.transform_invoice(
        header_row(), [], STATUS_CODES, NS, MIGRATED_AT)

    assert doc["lines"] == []
    assert doc["lineCount"] == 0
    assert doc["lineTotal"] == Decimal128("0.00")
    assert findings.counts == {}


def test_line_customer_disagreement_is_a_reported_finding():
    doc, findings = transform.transform_invoice(
        header_row(), [line_row(CUST_ID="dddddddd-dead-beef-dead-beefdeadbeef")],
        STATUS_CODES, NS, MIGRATED_AT)

    assert findings.counts == {"line_customer_mismatch": 1}
    assert findings.samples["line_customer_mismatch"][0]["detail"] == {
        "lineCustId": "dddddddd-dead-beef-dead-beefdeadbeef",
        "headerCustId": "cccccccc-cccc-cccc-cccc-cccccccccccc",
    }
    # the line keeps no customer copy either way
    assert "customerId" not in doc["lines"][0]


def test_unmapped_status_code_falls_back_to_the_raw_code():
    doc, findings = transform.transform_invoice(
        header_row(STATUS_CD=Decimal("99")), [], STATUS_CODES, NS, MIGRATED_AT)

    assert doc["status"] == "99"
    assert findings.counts == {"unmapped_status_code": 1}


def test_dirty_header_date_is_reported_and_left_null():
    doc, findings = transform.transform_invoice(
        header_row(INVOICE_DT="31-FEB-24"), [], STATUS_CODES, NS, MIGRATED_AT)

    assert doc["invoiceDate"] is None
    assert findings.counts == {"unparseable_invoice_date": 1}


# ── orphans ───────────────────────────────────────────────────────────────────


def test_transform_orphan_quarantines_raw_fields():
    row = line_row(INVOICE_ID="ghost-invoice-1")
    doc = transform.transform_orphan(row, NS, MIGRATED_AT)

    assert doc["_id"] == doc["lineId"] == row["LINE_ID"]
    assert doc["quarantine_reason"] == "missing_header"
    assert doc["amount"] == Decimal128("42.00")
    assert doc["raw"]["INVOICE_ID"] == "ghost-invoice-1"
    assert doc["raw"]["CUST_NAME"] == "Alex Otter"
    assert doc["raw"]["AMOUNT"] == Decimal128("42.00")
    assert doc["raw"]["LINE_NO"] == Decimal128("7")
    assert doc["_migration"]["sourceTable"] == "OW_BILLING.INVOICE_LINE"


def test_orphan_keeps_null_posted_yn_as_null():
    doc = transform.transform_orphan(line_row(POSTED_YN=None), NS, MIGRATED_AT)
    assert doc["raw"]["POSTED_YN"] is None


# ── findings ledger ───────────────────────────────────────────────────────────


def test_findings_merge_counts_and_caps_samples():
    total = transform.Findings(sample_size=2)
    for i in range(5):
        part = transform.Findings()
        part.add("orphaned_rows", f"line-{i}")
        total.merge(part)

    assert total.as_dict()["orphaned_rows"]["count"] == 5
    assert total.as_dict()["orphaned_rows"]["sample"] == [{"id": "line-0"},
                                                          {"id": "line-1"}]


def test_findings_keeps_falsy_offending_values():
    findings = transform.Findings()
    findings.add("unmapped_status_code", "inv-1", 0)
    findings.add("unparseable_service_period", "line-1", "")
    findings.add("no_detail", "line-2")

    samples = findings.as_dict()
    assert samples["unmapped_status_code"]["sample"] == [{"id": "inv-1", "detail": 0}]
    assert samples["unparseable_service_period"]["sample"] == [
        {"id": "line-1", "detail": ""}]
    assert samples["no_detail"]["sample"] == [{"id": "line-2"}]
