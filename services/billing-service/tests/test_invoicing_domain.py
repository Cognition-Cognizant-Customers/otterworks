from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from app.domain import (
    CreditNote,
    NullAmountError,
    NullUsageUnitsError,
    RatedPlan,
    RatedSubscription,
    SubscriptionNotFoundError,
    UsageEvent,
    consume_credits,
    invoice_preview,
    issue_invoice,
)

TENANT = UUID("00000000-0000-0000-0000-000000000006")
SUBSCRIPTION = UUID("20000000-0000-0000-0000-000000000006")
PERIOD_START = date(2026, 2, 1)
PERIOD_END = date(2026, 2, 28)

STARTER = RatedPlan(
    included_units=100,
    overage_rate=Decimal("0.055000"),
    code="STARTER",
    monthly_fee=Decimal("49.00"),
)
GROWTH = RatedPlan(
    included_units=500,
    overage_rate=Decimal("0.035000"),
    code="GROWTH",
    monthly_fee=Decimal("149.00"),
)


def subscription(
    plan: RatedPlan = STARTER,
    starts_on: date = date(2026, 1, 1),
    ends_on: date | None = None,
    subscription_id: UUID = SUBSCRIPTION,
) -> RatedSubscription:
    return RatedSubscription(
        subscription_id=subscription_id,
        plan=plan,
        starts_on=starts_on,
        ends_on=ends_on,
        status="active",
        suspended_on=None,
    )


def event(units: int | None, occurred_on: date = date(2026, 2, 10)) -> UsageEvent:
    return UsageEvent(occurred_on=occurred_on, units=units, kind="api")


def note(
    credit_note_id: str,
    issued_on: date,
    remaining: str | None,
) -> CreditNote:
    return CreditNote(
        credit_note_id=UUID(credit_note_id),
        issued_on=issued_on,
        remaining_amount=Decimal(remaining) if remaining is not None else None,
    )


def preview(
    subscriptions=None,
    events=(),
    credit_notes=(),
    tax_exempt=False,
):
    return invoice_preview(
        TENANT,
        list(subscriptions) if subscriptions is not None else [subscription()],
        list(events),
        [],
        list(credit_notes),
        tax_exempt,
        PERIOD_START,
        PERIOD_END,
    )


@pytest.mark.rule("INVOICE-PLAN-LINE")
def test_plan_line_uses_latest_overlapping_subscription_fee():
    older = subscription(
        plan=GROWTH,
        starts_on=date(2025, 1, 1),
        ends_on=date(2026, 2, 10),
        subscription_id=UUID("20000000-0000-0000-0000-000000000009"),
    )
    newer = subscription(starts_on=date(2026, 2, 5))
    lines = preview(subscriptions=[older, newer])
    assert lines[0].line_no == 1
    assert lines[0].line_type == "plan"
    assert lines[0].description == "STARTER"
    assert lines[0].amount == Decimal("49.00")
    assert lines[0].total == Decimal("49.00")


@pytest.mark.rule("INVOICE-PLAN-LINE")
def test_no_overlapping_subscription_fails_closed():
    ended = subscription(ends_on=date(2026, 1, 31))
    with pytest.raises(SubscriptionNotFoundError):
        preview(subscriptions=[ended])


@pytest.mark.rule("INVOICE-USAGE-LINE")
def test_usage_line_reuses_the_extracted_rating_overage():
    lines = preview(events=[event(201)])
    assert lines[1].line_no == 2
    assert lines[1].line_type == "usage"
    assert lines[1].description == "usage overage"
    assert lines[1].amount == Decimal("5.56")
    assert lines[1].total == Decimal("5.56")


@pytest.mark.rule("INVOICE-USAGE-LINE")
def test_null_usage_units_fail_closed():
    with pytest.raises(NullUsageUnitsError):
        preview(events=[event(None)])


@pytest.mark.rule("INVOICE-TAX-SPLIT")
def test_tax_is_split_into_equal_regional_and_local_halves():
    lines = preview(events=[event(201)])
    expected_half = (Decimal("49.00") + Decimal("5.56")) * Decimal("0.0825") / 2
    assert [lines[2].line_type, lines[3].line_type] == ["tax", "tax"]
    assert lines[2].description == "regional tax"
    assert lines[3].description == "local tax"
    assert lines[2].amount == expected_half
    assert lines[3].amount == expected_half


@pytest.mark.rule("INVOICE-TAX-SPLIT")
def test_tax_exempt_tenant_pays_zero_tax():
    lines = preview(events=[event(201)], tax_exempt=True)
    assert lines[2].amount == Decimal("0")
    assert lines[3].amount == Decimal("0")


@pytest.mark.rule("INVOICE-CREDIT-LINE")
def test_credit_line_caps_applied_credit_at_the_invoice_value():
    notes = [
        note("70000000-0000-0000-0000-000000000001", date(2026, 1, 1), "40.00"),
        note("70000000-0000-0000-0000-000000000002", date(2026, 1, 2), "40.00"),
        note("70000000-0000-0000-0000-000000000003", date(2026, 1, 3), "-5.00"),
    ]
    lines = preview(credit_notes=notes)
    invoice_value = (Decimal("49.00") * (1 + Decimal("0.0825"))).quantize(Decimal("0.01"))
    assert lines[4].line_no == 5
    assert lines[4].line_type == "credit"
    assert lines[4].amount == Decimal("0")
    assert lines[4].credit_applied == invoice_value
    assert lines[4].total == -invoice_value


@pytest.mark.rule("INVOICE-CREDIT-LINE")
def test_null_credit_remaining_amount_fails_closed():
    notes = [note("70000000-0000-0000-0000-000000000001", date(2026, 1, 1), None)]
    with pytest.raises(NullAmountError):
        preview(credit_notes=notes)


@pytest.mark.rule("INVOICE-ISSUE-IDEMPOTENT")
def test_issue_derives_deterministic_period_and_invoice_ids():
    first = issue_invoice(TENANT, preview(), PERIOD_START, PERIOD_END)
    second = issue_invoice(TENANT, preview(), PERIOD_START, PERIOD_END)
    assert first.period_id == second.period_id
    assert first.invoice_id == second.invoice_id
    assert first.status == "issued"


@pytest.mark.rule("INVOICE-ISSUE-LINES")
def test_issue_stores_the_credit_line_total_as_its_amount():
    notes = [note("70000000-0000-0000-0000-000000000001", date(2026, 1, 1), "10.00")]
    invoice = issue_invoice(TENANT, preview(credit_notes=notes), PERIOD_START, PERIOD_END)
    assert [line.line_no for line in invoice.lines] == [1, 2, 3, 4, 5]
    assert invoice.lines[0].amount == Decimal("49.00")
    assert invoice.lines[4].amount == Decimal("-10.00")
    assert invoice.lines[0].line_id != invoice.lines[1].line_id
    again = issue_invoice(TENANT, preview(credit_notes=notes), PERIOD_START, PERIOD_END)
    assert [line.line_id for line in again.lines] == [line.line_id for line in invoice.lines]


@pytest.mark.rule("INVOICE-ISSUE-TOTALS")
def test_issue_totals_sum_rounded_lines_minus_applied_credit():
    notes = [note("70000000-0000-0000-0000-000000000001", date(2026, 1, 1), "10.00")]
    invoice = issue_invoice(
        TENANT, preview(events=[event(201)], credit_notes=notes), PERIOD_START, PERIOD_END
    )
    assert invoice.subtotal == Decimal("54.56")
    assert invoice.tax == Decimal("4.50")
    assert invoice.total == Decimal("49.06")


@pytest.mark.rule("INVOICE-CREDIT-CONSUME")
def test_credits_are_consumed_oldest_first():
    notes = [
        note("70000000-0000-0000-0000-000000000002", date(2026, 2, 1), "55.00"),
        note("70000000-0000-0000-0000-000000000001", date(2026, 1, 31), "5.00"),
    ]
    consumptions = consume_credits(notes, Decimal("53.04"))
    assert [str(item.credit_note_id) for item in consumptions] == [
        "70000000-0000-0000-0000-000000000001",
        "70000000-0000-0000-0000-000000000002",
    ]
    assert [item.remaining_amount for item in consumptions] == [
        Decimal("0"),
        Decimal("6.96"),
    ]


@pytest.mark.rule("INVOICE-CREDIT-CONSUME")
def test_equal_credit_dates_break_ties_by_credit_note_id():
    notes = [
        note("70000000-0000-0000-0000-000000000002", date(2026, 2, 1), "55.00"),
        note("70000000-0000-0000-0000-000000000001", date(2026, 2, 1), "5.00"),
    ]
    consumptions = consume_credits(notes, Decimal("53.04"))
    assert [str(item.credit_note_id) for item in consumptions] == [
        "70000000-0000-0000-0000-000000000001",
        "70000000-0000-0000-0000-000000000002",
    ]
    assert [item.remaining_amount for item in consumptions] == [
        Decimal("0"),
        Decimal("6.96"),
    ]


@pytest.mark.rule("INVOICE-CREDIT-CONSUME")
def test_credit_notes_without_issue_dates_are_consumed_last():
    notes = [
        note("70000000-0000-0000-0000-000000000002", None, "55.00"),
        note("70000000-0000-0000-0000-000000000001", date(2026, 2, 1), "5.00"),
    ]
    consumptions = consume_credits(notes, Decimal("53.04"))
    assert [str(item.credit_note_id) for item in consumptions] == [
        "70000000-0000-0000-0000-000000000001",
        "70000000-0000-0000-0000-000000000002",
    ]


@pytest.mark.rule("INVOICE-LINES-READ")
def test_embedded_invoice_lines_are_returned_sorted_by_line_no():
    from app.repository import MongoInvoicesRepository

    class FakeCollection:
        def __init__(self, document):
            self.document = document

        def find_one(self, query):
            if query["invoice_id"] == self.document["invoice_id"]:
                return self.document
            return None

    class FakeDatabase(dict):
        pass

    invoice_id = UUID("60000000-0000-0000-0000-000000000001")
    document = {
        "invoice_id": str(invoice_id),
        "lines": [
            {
                "line_id": "a0000000-0000-0000-0000-000000000002",
                "line_no": 2,
                "line_type": "usage",
                "description": "usage overage",
                "amount": Decimal("12.29"),
            },
            {
                "line_id": "a0000000-0000-0000-0000-000000000001",
                "line_no": 1,
                "line_type": "plan",
                "description": "GROWTH",
                "amount": Decimal("149.00"),
            },
        ],
    }
    database = FakeDatabase(invoices=FakeCollection(document))
    repository = MongoInvoicesRepository.__new__(MongoInvoicesRepository)
    repository.collection = database["invoices"]
    repository.ns = "demo"
    lines = repository.find_lines(invoice_id)
    assert [line.line_no for line in lines] == [1, 2]
    assert [line.line_type for line in lines] == ["plan", "usage"]
    assert repository.find_lines(UUID("60000000-0000-0000-0000-000000000009")) == []
