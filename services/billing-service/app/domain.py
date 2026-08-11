from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import md5
from typing import Protocol
from uuid import UUID, uuid5

PLAN_CHANGE_NAMESPACE = UUID("d8e9df63-6e46-4d6a-b9c2-2ef6e99cb5ee")

FIRST_TIER_CAP = 101
SECOND_TIER_MULTIPLIER = Decimal("1.5")
ROLLOVER_WINDOW_MONTHS = 3
ROLLOVER_CAP_MULTIPLIER = 2
CENTS = Decimal("0.01")


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


@dataclass(frozen=True)
class UsageEventRow:
    tenant_id: UUID
    occurred_at: datetime
    units: int
    kind: str


@dataclass(frozen=True)
class PriorRatingRow:
    period_start: date
    rollover_units: int


@dataclass(frozen=True)
class UsageSummaryRow:
    kind: str
    event_count: int
    units: int


@dataclass(frozen=True)
class RatingOutcome:
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
class RatingResultRow:
    result_id: UUID
    period_id: UUID
    subscription_id: UUID
    used_units: int
    quota_units: int
    rollover_units: int
    billable_units: int
    overage_amount: Decimal
    created_at: datetime


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


class RatingRepository(Protocol):
    def list_plans(self) -> list[PlanRow]: ...

    def list_subscriptions(self, tenant_id: UUID) -> list[SubscriptionRow]: ...

    def list_usage_events(self, tenant_id: UUID) -> list[UsageEventRow]: ...

    def list_prior_ratings(self, tenant_id: UUID) -> list[PriorRatingRow]: ...

    def get_rating_period(self, tenant_id: UUID, period_start: date) -> UUID | None: ...

    def insert_rating_period(
        self, period_id: UUID, tenant_id: UUID, period_start: date, period_end: date
    ) -> None: ...

    def update_rating_period(self, period_id: UUID, period_end: date) -> None: ...

    def get_rating_result(self, result_id: UUID) -> RatingResultRow | None: ...

    def insert_rating_result(self, result: RatingResultRow) -> None: ...

    def update_rating_result(
        self,
        result_id: UUID,
        used_units: int,
        rollover_units: int,
        billable_units: int,
        overage_amount: Decimal,
    ) -> None: ...


def md5_uuid(text: str) -> UUID:
    return UUID(hex=md5(text.encode()).hexdigest())


def months_before(day: date, months: int) -> date:
    month = day.month - months
    year = day.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def round_half_up(value: Decimal, exponent: Decimal) -> Decimal:
    return value.quantize(exponent, rounding=ROUND_HALF_UP)


def effective_subscription(
    subscriptions: list[SubscriptionRow], period_start: date, period_end: date
) -> SubscriptionRow | None:
    eligible = [
        item
        for item in subscriptions
        if item.starts_on <= period_end
        and (item.ends_on is None or item.ends_on >= period_start)
    ]
    return max(eligible, key=lambda item: item.starts_on, default=None)


def events_in_period(
    events: list[UsageEventRow], period_start: date, period_end: date
) -> list[UsageEventRow]:
    return [
        event
        for event in events
        if period_start <= event.occurred_at.astimezone(UTC).date() <= period_end
    ]


def rollover_credit(priors: list[PriorRatingRow], period_start: date, included_units: int) -> int:
    window_start = months_before(period_start, ROLLOVER_WINDOW_MONTHS)
    prior_units = sum(
        item.rollover_units
        for item in priors
        if window_start <= item.period_start < period_start
    )
    return min(prior_units, ROLLOVER_CAP_MULTIPLIER * included_units)


def rate_usage(
    plan: PlanRow,
    subscription: SubscriptionRow,
    events: list[UsageEventRow],
    priors: list[PriorRatingRow],
    tenant_id: UUID,
    period_start: date,
    period_end: date,
) -> RatingOutcome:
    used = sum(event.units for event in events_in_period(events, period_start, period_end))
    rollover = rollover_credit(priors, period_start, plan.included_units)
    billable = max(used - rollover - plan.included_units, 0)
    first_tier = min(billable, FIRST_TIER_CAP)
    second_tier = max(billable - FIRST_TIER_CAP, 0)
    amount = round_half_up(
        first_tier * plan.overage_rate
        + second_tier * plan.overage_rate * SECOND_TIER_MULTIPLIER,
        CENTS,
    )
    if (
        subscription.status == "suspended"
        and subscription.suspended_on is not None
        and period_start <= subscription.suspended_on <= period_end
    ):
        fraction = Decimal((period_end - subscription.suspended_on).days + 1) / Decimal(
            (period_end - period_start).days + 1
        )
        billable = int(round_half_up(billable * fraction, Decimal("1")))
        amount = round_half_up(amount * fraction, CENTS)
    return RatingOutcome(
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


def rate_tenant(
    repository: RatingRepository, tenant_id: UUID, period_start: date, period_end: date
) -> RatingOutcome | None:
    subscription = effective_subscription(
        repository.list_subscriptions(tenant_id), period_start, period_end
    )
    if subscription is None:
        return None
    plan = next(
        (item for item in repository.list_plans() if item.plan_id == subscription.plan_id), None
    )
    if plan is None:
        return None
    return rate_usage(
        plan,
        subscription,
        repository.list_usage_events(tenant_id),
        repository.list_prior_ratings(tenant_id),
        tenant_id,
        period_start,
        period_end,
    )


def usage_summary(
    events: list[UsageEventRow], period_start: date, period_end: date
) -> list[UsageSummaryRow]:
    grouped: dict[str, list[UsageEventRow]] = {}
    for event in events_in_period(events, period_start, period_end):
        grouped.setdefault(event.kind, []).append(event)
    return [
        UsageSummaryRow(
            kind=kind,
            event_count=len(items),
            units=sum(item.units for item in items),
        )
        for kind, items in sorted(grouped.items())
    ]


def finalize_rating(
    repository: RatingRepository, tenant_id: UUID, period_start: date, period_end: date
) -> RatingResultRow | None:
    subscription = effective_subscription(
        repository.list_subscriptions(tenant_id), period_start, period_end
    )
    outcome = rate_tenant(repository, tenant_id, period_start, period_end)
    if subscription is None or outcome is None:
        return None
    period_id = md5_uuid(f"{tenant_id}{period_start.isoformat()}")
    existing_period = repository.get_rating_period(tenant_id, period_start)
    if existing_period is None:
        repository.insert_rating_period(period_id, tenant_id, period_start, period_end)
    else:
        repository.update_rating_period(existing_period, period_end)
    result_id = md5_uuid(str(period_id))
    stored_rollover = max(outcome.quota_units - outcome.used_units, 0)
    if repository.get_rating_result(result_id) is None:
        repository.insert_rating_result(
            RatingResultRow(
                result_id=result_id,
                period_id=period_id,
                subscription_id=subscription.subscription_id,
                used_units=outcome.used_units,
                quota_units=outcome.quota_units,
                rollover_units=stored_rollover,
                billable_units=outcome.billable_units,
                overage_amount=outcome.overage_amount,
                created_at=datetime.combine(period_end, time.min, tzinfo=UTC),
            )
        )
    else:
        repository.update_rating_result(
            result_id,
            outcome.used_units,
            stored_rollover,
            outcome.billable_units,
            outcome.overage_amount,
        )
    return repository.get_rating_result(result_id)


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
