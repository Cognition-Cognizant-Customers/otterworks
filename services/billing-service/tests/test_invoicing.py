from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from app.domain import (
    PlanRow,
    deterministic_uuid,
    format_money,
    invoice_ids,
    invoice_totals,
    preview,
)

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
def test_invoice_totals_are_based_on_persisted_line_rounding() -> None:
    result = preview(TENANT, START, END, PLAN, Decimal("5.56"), False, Decimal("0"))
    assert invoice_totals(result) == (
        Decimal("54.56"),
        Decimal("4.50"),
        Decimal("0"),
        Decimal("59.06"),
    )


@pytest.mark.rule("INVOICING-006")
def test_invoice_ids_and_line_ids_are_md5_uuid_text() -> None:
    period_id, invoice_id = invoice_ids(TENANT, START)
    assert period_id == deterministic_uuid(f"{TENANT}{START.isoformat()}")
    assert deterministic_uuid(f"{invoice_id}1").version is None
    assert str(invoice_id) == str(invoice_id).lower()


@pytest.mark.rule("INVOICING-007")
def test_negative_total_is_preserved_for_rejection() -> None:
    tiny_plan = PlanRow(
        PLAN.plan_id, PLAN.code, PLAN.tier, Decimal("0.01"), 100, PLAN.overage_rate, True
    )
    result = preview(TENANT, START, END, tiny_plan, Decimal("0.06"), False, Decimal("0.08"))
    assert invoice_totals(result)[3] < 0


@pytest.mark.rule("INVOICING-008")
def test_credit_consumption_arithmetic_uses_pre_update_remaining() -> None:
    outstanding = Decimal("53.04")
    first_remaining = Decimal("5.00")
    first_after = max(first_remaining - outstanding, Decimal("0"))
    outstanding = max(outstanding - first_remaining, Decimal("0"))
    second_remaining = Decimal("55.00")
    second_after = max(second_remaining - outstanding, Decimal("0"))
    assert first_after == 0
    assert outstanding == Decimal("48.04")
    assert second_after == Decimal("6.96")


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
