from __future__ import annotations

import calendar
import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from uuid import UUID, uuid5

PLAN_CHANGE_NAMESPACE = UUID("d8e9df63-6e46-4d6a-b9c2-2ef6e99cb5ee")

FIRST_TIER_CAP = 101
SECOND_TIER_MULTIPLIER = Decimal("1.5")
ROLLOVER_WINDOW_MONTHS = 3
ROLLOVER_CAP_MULTIPLIER = 2

TAX_RATE = Decimal("0.0825")

TWO_PLACES = Decimal("0.01")
WHOLE = Decimal("1")


class NullUsageUnitsError(ValueError):
    """A usage event has no units value; rating must fail closed."""


class NullAmountError(ValueError):
    """A stored money amount is missing; invoicing must fail closed."""


class SubscriptionNotFoundError(LookupError):
    """No subscription overlaps the requested rating period."""


@dataclass(frozen=True)
class PlanRow:
    plan_id: UUID
    code: str
    tier: str
    monthly_fee: Decimal
    included_units: int
    overage_rate: Decimal
    active: bool


@dataclass(frozen=True)
class SubscriptionRow:
    subscription_id: UUID
    tenant_id: UUID
    plan_id: UUID
    starts_on: date
    ends_on: date | None
    status: str
    suspended_on: date | None


@dataclass(frozen=True)
class EntitlementRow:
    tenant_id: UUID
    plan_code: str
    tier: str
    monthly_fee: Decimal
    included_units: int
    subscription_status: str
    ends_on: date | None
    starts_on: date


class PlansRepository(Protocol):
    def list_plans(self) -> list[PlanRow]: ...

    def find_entitlements(self, tenant_id: UUID) -> list[EntitlementRow]: ...

    def list_subscriptions(self, tenant_id: UUID) -> list[SubscriptionRow]: ...

    def update_subscription(self, subscription_id: UUID, ends_on: date, status: str) -> None: ...

    def insert_subscription(
        self,
        subscription_id: UUID,
        tenant_id: UUID,
        plan_id: UUID,
        starts_on: date,
        status: str,
    ) -> None: ...


def catalog(plans: list[PlanRow]) -> list[PlanRow]:
    return sorted(
        (plan for plan in plans if plan.active),
        key=lambda plan: (plan.monthly_fee, plan.code),
    )


def entitlement(rows: list[EntitlementRow], tenant_id: UUID, on: date) -> EntitlementRow | None:
    eligible = [
        row
        for row in rows
        if row.tenant_id == tenant_id
        and row.starts_on <= on
        and (row.ends_on is None or row.ends_on >= on)
    ]
    return max(eligible, key=lambda row: row.starts_on, default=None)


def change_plan(
    repository: PlansRepository,
    tenant_id: UUID,
    plan_id: UUID,
    effective_on: date,
) -> tuple[list[SubscriptionRow], SubscriptionRow]:
    subscriptions = repository.list_subscriptions(tenant_id)
    for subscription in subscriptions:
        if subscription.ends_on is None and subscription.starts_on < effective_on:
            next_status = (
                subscription.status if subscription.status == "cancelled" else "active"
            )
            repository.update_subscription(
                subscription.subscription_id,
                effective_on - timedelta(days=1),
                next_status,
            )
    created_id = uuid5(PLAN_CHANGE_NAMESPACE, f"{tenant_id}{plan_id}{effective_on.isoformat()}")
    repository.insert_subscription(
        created_id,
        tenant_id,
        plan_id,
        effective_on,
        "active",
    )
    subscriptions = sorted(
        repository.list_subscriptions(tenant_id), key=lambda item: item.starts_on
    )
    created = next(item for item in subscriptions if item.subscription_id == created_id)
    return subscriptions, created


@dataclass(frozen=True)
class RatedPlan:
    included_units: int
    overage_rate: Decimal
    code: str = ""
    monthly_fee: Decimal = Decimal("0")


@dataclass(frozen=True)
class RatedSubscription:
    subscription_id: UUID
    plan: RatedPlan
    starts_on: date
    ends_on: date | None
    status: str
    suspended_on: date | None


@dataclass(frozen=True)
class UsageEvent:
    occurred_on: date
    units: int | None
    kind: str


@dataclass(frozen=True)
class RatingHistoryEntry:
    period_start: date
    rollover_units: int


@dataclass(frozen=True)
class UsageRating:
    tenant_id: UUID
    period_start: date
    period_end: date
    used_units: int
    quota_units: int
    rollover_units: int
    billable_units: int
    first_tier_units: int
    second_tier_units: int
    overage_amount: Decimal


@dataclass(frozen=True)
class UsageSummaryRow:
    kind: str
    event_count: int
    units: int


@dataclass(frozen=True)
class FinalizedRating:
    period_id: UUID
    result_id: UUID
    subscription_id: UUID
    period_start: date
    period_end: date
    used_units: int
    quota_units: int
    rollover_units: int
    billable_units: int
    overage_amount: Decimal


def _round_half_up(value: Decimal, exponent: Decimal) -> Decimal:
    return value.quantize(exponent, rounding=ROUND_HALF_UP)


def _months_before(day: date, months: int) -> date:
    month_index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(month_index, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def current_subscription(
    subscriptions: list[RatedSubscription], period_start: date, period_end: date
) -> RatedSubscription:
    eligible = [
        subscription
        for subscription in subscriptions
        if subscription.starts_on <= period_end
        and (subscription.ends_on is None or subscription.ends_on >= period_start)
    ]
    if not eligible:
        raise SubscriptionNotFoundError(
            f"no subscription overlaps {period_start}..{period_end}"
        )
    return max(eligible, key=lambda subscription: subscription.starts_on)


def _events_in_period(
    events: list[UsageEvent], period_start: date, period_end: date
) -> list[UsageEvent]:
    selected = [
        event for event in events if period_start <= event.occurred_on <= period_end
    ]
    for event in selected:
        if event.units is None:
            raise NullUsageUnitsError(
                f"usage event of kind {event.kind!r} on {event.occurred_on} has no units"
            )
    return selected


def usage_rating(
    tenant_id: UUID,
    subscriptions: list[RatedSubscription],
    events: list[UsageEvent],
    history: list[RatingHistoryEntry],
    period_start: date,
    period_end: date,
) -> UsageRating:
    subscription = current_subscription(subscriptions, period_start, period_end)
    plan = subscription.plan
    used = sum(event.units or 0 for event in _events_in_period(events, period_start, period_end))
    window_start = _months_before(period_start, ROLLOVER_WINDOW_MONTHS)
    prior = sum(
        entry.rollover_units
        for entry in history
        if window_start <= entry.period_start < period_start
    )
    rollover_cap = plan.included_units * ROLLOVER_CAP_MULTIPLIER
    rollover = min(prior, rollover_cap)
    billable = max(used - rollover - plan.included_units, 0)
    first_tier = min(billable, FIRST_TIER_CAP)
    second_tier = max(billable - FIRST_TIER_CAP, 0)
    amount = _round_half_up(
        first_tier * plan.overage_rate
        + second_tier * plan.overage_rate * SECOND_TIER_MULTIPLIER,
        TWO_PLACES,
    )
    if (
        subscription.status == "suspended"
        and subscription.suspended_on is not None
        and period_start <= subscription.suspended_on <= period_end
    ):
        fraction = Decimal((period_end - subscription.suspended_on).days + 1) / Decimal(
            (period_end - period_start).days + 1
        )
        billable = int(_round_half_up(billable * fraction, WHOLE))
        amount = _round_half_up(amount * fraction, TWO_PLACES)
    return UsageRating(
        tenant_id=tenant_id,
        period_start=period_start,
        period_end=period_end,
        used_units=used,
        quota_units=plan.included_units,
        rollover_units=rollover,
        billable_units=billable,
        first_tier_units=first_tier,
        second_tier_units=second_tier,
        overage_amount=amount,
    )


def usage_summary(
    events: list[UsageEvent], period_start: date, period_end: date
) -> list[UsageSummaryRow]:
    grouped: dict[str, list[UsageEvent]] = {}
    for event in _events_in_period(events, period_start, period_end):
        grouped.setdefault(event.kind, []).append(event)
    return [
        UsageSummaryRow(
            kind=kind,
            event_count=len(group),
            units=sum(event.units or 0 for event in group),
        )
        for kind, group in sorted(grouped.items())
    ]


def _md5_uuid(value: str) -> UUID:
    return UUID(hex=hashlib.md5(value.encode()).hexdigest())


@dataclass(frozen=True)
class CreditNote:
    credit_note_id: UUID
    issued_on: date | None
    remaining_amount: Decimal | None


def credit_note_order(note: CreditNote) -> tuple[bool, date, UUID]:
    return (note.issued_on is None, note.issued_on or date.min, note.credit_note_id)


@dataclass(frozen=True)
class InvoicePreviewLine:
    line_no: int
    line_type: str
    description: str
    amount: Decimal
    tax_amount: Decimal
    credit_applied: Decimal
    total: Decimal


@dataclass(frozen=True)
class InvoiceLineRecord:
    line_id: UUID
    line_no: int
    line_type: str
    description: str
    amount: Decimal


@dataclass(frozen=True)
class CreditConsumption:
    credit_note_id: UUID
    remaining_amount: Decimal


@dataclass(frozen=True)
class IssuedInvoice:
    invoice_id: UUID
    period_id: UUID
    tenant_id: UUID
    issued_at: date
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    status: str
    lines: list[InvoiceLineRecord]
    credit_applied: Decimal


def _available_credit(credit_notes: list[CreditNote]) -> Decimal:
    total = Decimal("0")
    for note in credit_notes:
        if note.remaining_amount is None:
            raise NullAmountError(
                f"credit note {note.credit_note_id} has no remaining amount"
            )
        if note.remaining_amount > 0:
            total += note.remaining_amount
    return total


def invoice_preview(
    tenant_id: UUID,
    subscriptions: list[RatedSubscription],
    events: list[UsageEvent],
    history: list[RatingHistoryEntry],
    credit_notes: list[CreditNote],
    tax_exempt: bool,
    period_start: date,
    period_end: date,
) -> list[InvoicePreviewLine]:
    subscription = current_subscription(subscriptions, period_start, period_end)
    plan = subscription.plan
    rating = usage_rating(
        tenant_id, subscriptions, events, history, period_start, period_end
    )
    credit = _available_credit(credit_notes)
    tax = (
        Decimal("0")
        if tax_exempt
        else (plan.monthly_fee + rating.overage_amount) * TAX_RATE
    )
    fee = _round_half_up(plan.monthly_fee, TWO_PLACES)
    overage = _round_half_up(rating.overage_amount, TWO_PLACES)
    applied = min(
        credit,
        _round_half_up(plan.monthly_fee + rating.overage_amount + tax, TWO_PLACES),
    )
    zero = Decimal("0")
    return [
        InvoicePreviewLine(1, "plan", plan.code, fee, zero, zero, fee),
        InvoicePreviewLine(2, "usage", "usage overage", overage, zero, zero, overage),
        InvoicePreviewLine(3, "tax", "regional tax", tax / 2, zero, zero, tax / 2),
        InvoicePreviewLine(4, "tax", "local tax", tax / 2, zero, zero, tax / 2),
        InvoicePreviewLine(5, "credit", "credit notes", zero, zero, applied, -applied),
    ]


def issue_invoice(
    tenant_id: UUID,
    preview: list[InvoicePreviewLine],
    period_start: date,
    period_end: date,
) -> IssuedInvoice:
    period_id = _md5_uuid(f"{tenant_id}{period_start.isoformat()}")
    invoice_id = _md5_uuid(f"{period_id}invoice")
    subtotal = Decimal("0")
    tax = Decimal("0")
    credit = Decimal("0")
    lines = []
    for line in preview:
        stored_amount = line.total if line.line_type == "credit" else line.amount
        lines.append(
            InvoiceLineRecord(
                line_id=_md5_uuid(f"{invoice_id}{line.line_no}"),
                line_no=line.line_no,
                line_type=line.line_type,
                description=line.description,
                amount=stored_amount,
            )
        )
        if line.line_type in ("plan", "usage"):
            subtotal += _round_half_up(line.amount, TWO_PLACES)
        elif line.line_type == "tax":
            tax += _round_half_up(line.amount, TWO_PLACES)
        elif line.line_type == "credit":
            credit = line.credit_applied
    total = _round_half_up(subtotal + tax - credit, TWO_PLACES)
    return IssuedInvoice(
        invoice_id=invoice_id,
        period_id=period_id,
        tenant_id=tenant_id,
        issued_at=period_end,
        subtotal=_round_half_up(subtotal, TWO_PLACES),
        tax=_round_half_up(tax, TWO_PLACES),
        total=total,
        status="issued",
        lines=lines,
        credit_applied=credit,
    )


def consume_credits(
    credit_notes: list[CreditNote], credit_applied: Decimal
) -> list[CreditConsumption]:
    remaining_credit = credit_applied
    consumptions = []
    open_notes = [
        note
        for note in credit_notes
        if note.remaining_amount is not None and note.remaining_amount > 0
    ]
    for note in sorted(open_notes, key=credit_note_order):
        if remaining_credit <= 0:
            break
        consumptions.append(
            CreditConsumption(
                credit_note_id=note.credit_note_id,
                remaining_amount=max(note.remaining_amount - remaining_credit, Decimal("0")),
            )
        )
        remaining_credit = max(remaining_credit - note.remaining_amount, Decimal("0"))
    return consumptions


def finalize_rating(
    tenant_id: UUID,
    subscriptions: list[RatedSubscription],
    events: list[UsageEvent],
    history: list[RatingHistoryEntry],
    period_start: date,
    period_end: date,
) -> FinalizedRating:
    period_id = _md5_uuid(f"{tenant_id}{period_start.isoformat()}")
    subscription = current_subscription(subscriptions, period_start, period_end)
    rating = usage_rating(
        tenant_id, subscriptions, events, history, period_start, period_end
    )
    return FinalizedRating(
        period_id=period_id,
        result_id=_md5_uuid(str(period_id)),
        subscription_id=subscription.subscription_id,
        period_start=period_start,
        period_end=period_end,
        used_units=rating.used_units,
        quota_units=rating.quota_units,
        rollover_units=max(rating.quota_units - rating.used_units, 0),
        billable_units=rating.billable_units,
        overage_amount=rating.overage_amount,
    )
