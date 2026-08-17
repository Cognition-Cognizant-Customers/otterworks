from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import md5
from typing import Protocol
from uuid import UUID, uuid5

PLAN_CHANGE_NAMESPACE = UUID("d8e9df63-6e46-4d6a-b9c2-2ef6e99cb5ee")


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
        Decimal(first) * plan.overage_rate
        + Decimal(second) * plan.overage_rate * Decimal("1.5")
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
