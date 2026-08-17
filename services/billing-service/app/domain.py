from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import md5
from typing import Protocol
from uuid import UUID, uuid5

PLAN_CHANGE_NAMESPACE = UUID("d8e9df63-6e46-4d6a-b9c2-2ef6e99cb5ee")


class InvoicingRefusal(Exception):  # noqa: N818
    pass


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
class UsageEvent:
    event_id: UUID
    tenant_id: UUID
    occurred_at: datetime
    units: int
    kind: str


@dataclass(frozen=True)
class RatingPeriod:
    period_id: UUID
    tenant_id: UUID
    period_start: date
    period_end: date
    result: RatingResult


@dataclass(frozen=True)
class RatingResult:
    result_id: UUID
    subscription_id: UUID | None
    used_units: int
    quota_units: int | None
    rollover_units: int
    billable_units: int
    overage_amount: Decimal | None
    created_at: datetime


@dataclass(frozen=True)
class Rating:
    used_units: int
    quota_units: int | None
    rollover_units: int
    billable_units: int
    first_tier_units: int
    second_tier_units: int
    overage_amount: Decimal | None
    subscription: SubscriptionRow | None


def usage_summary(
    events: list[UsageEvent], tenant_id: UUID, period_start: date, period_end: date
) -> list[dict]:
    grouped: dict[str, dict[str, int]] = {}
    for event in events:
        occurred = event.occurred_at.astimezone(UTC).date()
        if event.tenant_id != tenant_id or not period_start <= occurred <= period_end:
            continue
        item = grouped.setdefault(event.kind, {"event_count": 0, "units": 0})
        item["event_count"] += 1
        item["units"] += event.units
    return [{"kind": kind, **grouped[kind]} for kind in sorted(grouped)]


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


def select_subscription(
    subscriptions: list[SubscriptionRow],
    tenant_id: UUID,
    period_start: date,
    period_end: date,
) -> SubscriptionRow | None:
    eligible = [
        item
        for item in subscriptions
        if item.tenant_id == tenant_id
        and item.starts_on <= period_end
        and (item.ends_on is None or item.ends_on >= period_start)
    ]
    return max(eligible, key=lambda item: (item.starts_on, str(item.subscription_id)), default=None)


def calendar_months_before(value: date, months: int) -> date:
    month = value.month - months
    year = value.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def calculate_rating(
    subscriptions: list[SubscriptionRow],
    plans: list[PlanRow],
    events: list[UsageEvent],
    prior_periods: list[RatingPeriod],
    tenant_id: UUID,
    period_start: date,
    period_end: date,
) -> Rating:
    used = sum(
        event.units
        for event in events
        if event.tenant_id == tenant_id
        and period_start <= event.occurred_at.astimezone(UTC).date() <= period_end
    )
    subscription = select_subscription(subscriptions, tenant_id, period_start, period_end)
    plan = next(
        (
            item
            for item in plans
            if subscription is not None and item.plan_id == subscription.plan_id
        ),
        None,
    )
    if plan is None:
        return Rating(used, None, 0, 0, 0, 0, None, subscription)
    lower = calendar_months_before(period_start, 3)
    prior = sum(
        item.result.rollover_units
        for item in prior_periods
        if item.tenant_id == tenant_id and lower <= item.period_start < period_start
    )
    prior = min(2 * plan.included_units, prior)
    rollover = min(prior, plan.included_units * 2)
    billable = max(used - rollover - plan.included_units, 0)
    first = min(billable, 101)
    second = max(billable - 101, 0)
    amount = (
        Decimal(first) * plan.overage_rate + Decimal(second) * plan.overage_rate * Decimal("1.5")
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if (
        subscription.status == "suspended"
        and subscription.suspended_on is not None
        and period_start <= subscription.suspended_on <= period_end
    ):
        factor = Decimal((period_end - subscription.suspended_on).days + 1) / Decimal(
            (period_end - period_start).days + 1
        )
        billable = int((Decimal(billable) * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        amount = (amount * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Rating(
        used, plan.included_units, rollover, billable, first, second, amount, subscription
    )


def finalize_result(
    rating: Rating,
    tenant_id: UUID,
    period_start: date,
    period_end: date,
) -> tuple[UUID, RatingResult]:
    period_id = UUID(bytes=md5(f"{tenant_id}{period_start}".encode()).digest())
    result_id = UUID(bytes=md5(str(period_id).encode()).digest())
    return period_id, RatingResult(
        result_id,
        rating.subscription.subscription_id if rating.subscription else None,
        rating.used_units,
        rating.quota_units,
        max(rating.quota_units - rating.used_units, 0) if rating.quota_units is not None else 0,
        rating.billable_units,
        rating.overage_amount,
        datetime.combine(period_end, time.min, tzinfo=UTC),
    )


def change_plan(
    repository: PlansRepository,
    tenant_id: UUID,
    plan_id: UUID,
    effective_on: date,
) -> tuple[list[SubscriptionRow], SubscriptionRow]:
    subscriptions = repository.list_subscriptions(tenant_id)
    for subscription in subscriptions:
        if subscription.ends_on is None and subscription.starts_on < effective_on:
            next_status = subscription.status if subscription.status == "cancelled" else "active"
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
class InvoiceLine:
    line_no: int
    line_type: str
    description: str
    amount: Decimal
    tax_amount: Decimal
    credit_applied: Decimal
    total: Decimal


@dataclass(frozen=True)
class InvoicePreview:
    tenant_id: UUID
    period_start: date
    period_end: date
    lines: list[InvoiceLine]


@dataclass(frozen=True)
class CreditNoteRow:
    note_id: UUID
    tenant_id: UUID
    issued_on: date
    amount: Decimal
    remaining_amount: Decimal


@dataclass(frozen=True)
class InvoiceRow:
    invoice_id: UUID
    tenant_id: UUID
    period_id: UUID
    issued_at: date
    status: str
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    lines: list[InvoiceLine]


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_money(value: Decimal) -> str:
    normalized = Decimal("0.00") if value == 0 else value
    return f"{normalized.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def deterministic_uuid(value: str) -> UUID:
    return UUID(bytes=md5(value.encode()).digest())


def invoice_ids(tenant_id: UUID, period_start: date) -> tuple[UUID, UUID]:
    period_id = deterministic_uuid(f"{tenant_id}{period_start.isoformat()}")
    return period_id, deterministic_uuid(f"{period_id}invoice")


def preview(
    tenant_id: UUID,
    period_start: date,
    period_end: date,
    plan: PlanRow,
    overage_amount: Decimal,
    tax_exempt: bool,
    credit_amount: Decimal,
) -> InvoicePreview:
    tax = Decimal("0") if tax_exempt else (plan.monthly_fee + overage_amount) * Decimal("0.0825")
    applied = min(credit_amount, money(plan.monthly_fee + overage_amount + tax))
    lines = [
        InvoiceLine(
            1,
            "plan",
            plan.code,
            money(plan.monthly_fee),
            Decimal("0"),
            Decimal("0"),
            money(plan.monthly_fee),
        ),
        InvoiceLine(
            2,
            "usage",
            "usage overage",
            money(overage_amount),
            Decimal("0"),
            Decimal("0"),
            money(overage_amount),
        ),
        InvoiceLine(3, "tax", "regional tax", tax / 2, Decimal("0"), Decimal("0"), tax / 2),
        InvoiceLine(4, "tax", "local tax", tax / 2, Decimal("0"), Decimal("0"), tax / 2),
        InvoiceLine(5, "credit", "credit notes", Decimal("0"), Decimal("0"), applied, -applied),
    ]
    return InvoicePreview(tenant_id, period_start, period_end, lines)


def invoice_totals(preview_result: InvoicePreview) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    subtotal = sum(
        (
            money(line.amount)
            for line in preview_result.lines
            if line.line_type in {"plan", "usage"}
        ),
        Decimal("0"),
    )
    tax = sum(
        (money(line.amount) for line in preview_result.lines if line.line_type == "tax"),
        Decimal("0"),
    )
    credit = next(
        line.credit_applied for line in preview_result.lines if line.line_type == "credit"
    )
    total = money(subtotal + tax - credit)
    if total < 0:
        raise InvoicingRefusal("invoice total cannot be negative")
    return money(subtotal), money(tax), credit, total


def ordered_lines(lines: list[InvoiceLine]) -> list[InvoiceLine]:
    return sorted(lines, key=lambda line: line.line_no)


def line_amount_for_storage(line: InvoiceLine) -> Decimal:
    return line.total if line.line_type == "credit" else line.amount


def stored_line_amount(line: InvoiceLine) -> Decimal:
    return money(line_amount_for_storage(line))


def consume_credits(
    notes: list[CreditNoteRow], credit_applied: Decimal
) -> list[tuple[UUID, Decimal]]:
    outstanding = credit_applied
    updates: list[tuple[UUID, Decimal]] = []
    for note in sorted(notes, key=lambda item: (item.issued_on, item.note_id)):
        if outstanding <= 0:
            break
        remaining = max(note.remaining_amount - outstanding, Decimal("0"))
        updates.append((note.note_id, remaining))
        outstanding = max(outstanding - note.remaining_amount, Decimal("0"))
    return updates
