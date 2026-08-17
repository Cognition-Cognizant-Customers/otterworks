from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from app.domain import (
    CreditNoteRow,
    PlanRow,
    consume_credits,
    deterministic_uuid,
    format_money,
    invoice_ids,
    invoice_totals,
    line_amount_for_storage,
    ordered_lines,
    preview,
    stored_line_amount,
)
from app.repository import MongoInvoicingRepository

TENANT = UUID("00000000-0000-0000-0000-000000000006")
PLAN = PlanRow(
    UUID("10000000-0000-0000-0000-000000000001"),
    "STARTER",
    "starter",
    Decimal("49.00"),
    100,
    Decimal("0.055"),
    True,
)
START = date(2026, 2, 1)
END = date(2026, 2, 28)


@pytest.mark.rule("INVOICING-001")
def test_preview_uses_plan_code_and_rounded_fee() -> None:
    result = preview(TENANT, START, END, PLAN, Decimal("5.56"), False, Decimal("0"))
    assert result.lines[0].description == "STARTER"
    assert result.lines[0].amount == Decimal("49.00")


@pytest.mark.rule("INVOICING-002")
def test_preview_consumes_rated_overage_without_recomputing() -> None:
    result = preview(TENANT, START, END, PLAN, Decimal("5.56"), False, Decimal("0"))
    assert result.lines[1].description == "usage overage"
    assert result.lines[1].amount == Decimal("5.56")


@pytest.mark.rule("INVOICING-003")
def test_preview_splits_unrounded_tax_and_keeps_tax_amount_zero() -> None:
    result = preview(TENANT, START, END, PLAN, Decimal("5.56"), False, Decimal("0"))
    assert result.lines[2].amount == Decimal("2.250600")
    assert result.lines[3].amount == Decimal("2.250600")
    assert all(line.tax_amount == 0 for line in result.lines)


@pytest.mark.rule("INVOICING-004")
def test_preview_caps_credit_at_rounded_pre_tax_plus_tax_total() -> None:
    result = preview(TENANT, START, END, PLAN, Decimal("5.56"), False, Decimal("100"))
    assert result.lines[4].credit_applied == Decimal("59.06")
    assert result.lines[4].total == Decimal("-59.06")


@pytest.mark.rule("INVOICING-005")
def test_invoice_lines_are_ordered_and_unknown_invoice_is_empty() -> None:
    result = preview(TENANT, START, END, PLAN, Decimal("5.56"), False, Decimal("0"))
    lines = ordered_lines([result.lines[1], result.lines[0]])
    assert [(line.line_no, line.line_type, line.description, line.amount) for line in lines] == [
        (1, "plan", "STARTER", Decimal("49.00")),
        (2, "usage", "usage overage", Decimal("5.56")),
    ]
    assert ordered_lines([]) == []

    class EmptyInvoices:
        def find_one(self, *_args, **_kwargs):
            return None

    class EmptyDatabase:
        billing_invoices = EmptyInvoices()

    class EmptyClient:
        def __getitem__(self, _name):
            return EmptyDatabase()

    assert MongoInvoicingRepository(EmptyClient()).invoice_lines(UUID(int=0)) == []


@pytest.mark.rule("INVOICING-006")
def test_invoice_ids_and_line_ids_are_md5_uuid_text() -> None:
    period_id, invoice_id = invoice_ids(TENANT, START)
    assert str(period_id) == "1b886a8c-7ca6-751b-6c6f-d9435d323eb1"
    assert str(invoice_id) == "ec9434d5-6b88-cc45-051d-2187049adc12"
    assert str(deterministic_uuid(f"{invoice_id}1")) == ("fdcfba40-b3a1-f198-fcd8-51e75ae397a8")
    tenant_9 = UUID("00000000-0000-0000-0000-000000000009")
    period_id, invoice_id = invoice_ids(tenant_9, START)
    assert str(period_id) == "5dc02199-0345-1f48-ab13-8eeefeba5910"
    assert str(invoice_id) == "f947416b-6478-ac32-911a-12ca7f03a6fb"
    credit_line = preview(TENANT, START, END, PLAN, Decimal("5.56"), False, Decimal("100")).lines[4]
    assert line_amount_for_storage(credit_line) == credit_line.total
    assert line_amount_for_storage(credit_line) == Decimal("-59.06")
    tax_lines = preview(TENANT, START, END, PLAN, Decimal("5.56"), False, Decimal("0")).lines[2:4]
    assert [stored_line_amount(tax_line) for tax_line in tax_lines] == [
        Decimal("2.25"),
        Decimal("2.25"),
    ]


@pytest.mark.rule("INVOICING-007")
def test_negative_total_is_preserved_for_rejection() -> None:
    normal = preview(TENANT, START, END, PLAN, Decimal("5.56"), False, Decimal("0"))
    assert invoice_totals(normal) == (
        Decimal("54.56"),
        Decimal("4.50"),
        Decimal("0"),
        Decimal("59.06"),
    )
    tiny_plan = PlanRow(
        PLAN.plan_id, PLAN.code, PLAN.tier, Decimal("0.01"), 100, PLAN.overage_rate, True
    )
    result = preview(TENANT, START, END, tiny_plan, Decimal("0.06"), False, Decimal("0.08"))
    with pytest.raises(ValueError, match="invoice total cannot be negative"):
        invoice_totals(result)


@pytest.mark.rule("INVOICING-008")
def test_credit_consumption_arithmetic_uses_pre_update_remaining() -> None:
    notes = [
        CreditNoteRow(
            UUID("70000000-0000-0000-0000-000000000006"),
            TENANT,
            date(2026, 1, 31),
            Decimal("55.00"),
            Decimal("55.00"),
        ),
        CreditNoteRow(
            UUID("70000000-0000-0000-0000-000000000005"),
            TENANT,
            date(2026, 1, 31),
            Decimal("5.00"),
            Decimal("5.00"),
        ),
    ]
    assert consume_credits(notes, Decimal("53.04")) == [
        (UUID("70000000-0000-0000-0000-000000000005"), Decimal("0")),
        (UUID("70000000-0000-0000-0000-000000000006"), Decimal("6.96")),
    ]
    equal_date = [
        CreditNoteRow(
            UUID("70000000-0000-0000-0000-000000000001"),
            TENANT,
            date(2026, 1, 31),
            Decimal("30.00"),
            Decimal("30.00"),
        ),
        CreditNoteRow(
            UUID("70000000-0000-0000-0000-000000000002"),
            TENANT,
            date(2026, 1, 31),
            Decimal("30.00"),
            Decimal("30.00"),
        ),
    ]
    assert consume_credits(equal_date, Decimal("53.04")) == [
        (UUID("70000000-0000-0000-0000-000000000001"), Decimal("0")),
        (UUID("70000000-0000-0000-0000-000000000002"), Decimal("6.96")),
    ]


def test_seeded_preview_values_and_negative_zero_formatting() -> None:
    result = preview(TENANT, START, END, PLAN, Decimal("5.56"), False, Decimal("0"))
    assert [format_money(line.amount) for line in result.lines] == [
        "49.00",
        "5.56",
        "2.25",
        "2.25",
        "0.00",
    ]
    assert format_money(Decimal("-0")) == "0.00"


def test_tax_exempt_preview_has_zero_tax() -> None:
    result = preview(TENANT, START, END, PLAN, Decimal("5.56"), True, Decimal("0"))
    assert result.lines[2].amount == 0
    assert result.lines[3].amount == 0
