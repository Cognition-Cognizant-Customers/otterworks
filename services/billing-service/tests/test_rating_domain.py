from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.domain import (
    PlanRow,
    RatingPeriod,
    RatingResult,
    SubscriptionRow,
    UsageEvent,
    calculate_rating,
    finalize_result,
    usage_summary,
)

TENANT = UUID("00000000-0000-0000-0000-000000000001")
PLAN_ID = UUID("10000000-0000-0000-0000-000000000001")
SUB_ID = UUID("20000000-0000-0000-0000-000000000001")
PLAN = PlanRow(PLAN_ID, "STARTER", "starter", Decimal("49.00"), 100, Decimal("0.055"), True)


def subscription(**changes) -> SubscriptionRow:
    values = {
        "subscription_id": SUB_ID,
        "tenant_id": TENANT,
        "plan_id": PLAN_ID,
        "starts_on": date(2026, 1, 1),
        "ends_on": None,
        "status": "active",
        "suspended_on": None,
    }
    values.update(changes)
    return SubscriptionRow(**values)


def event(units: int, occurred: str, kind: str = "api") -> UsageEvent:
    return UsageEvent(
        UUID("30000000-0000-0000-0000-000000000001"),
        TENANT,
        datetime.fromisoformat(occurred).replace(tzinfo=UTC),
        units,
        kind,
    )


def rate(events=None, subscriptions=None, plans=None, periods=None):
    return calculate_rating(
        subscriptions or [subscription()],
        plans or [PLAN],
        events or [],
        periods or [],
        TENANT,
        date(2026, 2, 1),
        date(2026, 2, 28),
    )


@pytest.mark.rule("RATING-001")
def test_selects_latest_overlapping_subscription():
    earlier = subscription(starts_on=date(2025, 1, 1))
    later = subscription(
        subscription_id=UUID("20000000-0000-0000-0000-000000000002"),
        starts_on=date(2026, 2, 1),
    )
    assert rate(subscriptions=[earlier, later]).subscription == later


@pytest.mark.rule("RATING-002")
def test_used_units_is_inclusive_and_zero_without_events():
    events = [event(5, "2026-01-31T23:59:59"), event(7, "2026-02-28T23:59:59")]
    assert rate(events=events).used_units == 7
    assert rate().used_units == 0


@pytest.mark.rule("RATING-003")
def test_rollover_uses_inclusive_three_calendar_month_lookback():
    prior = RatingPeriod(
        UUID("40000000-0000-0000-0000-000000000001"),
        TENANT,
        date(2025, 11, 1),
        date(2025, 11, 30),
        RatingResult(UUID(int=1), SUB_ID, 0, 100, 100, 0, Decimal("0"), datetime.now(UTC)),
    )
    assert rate(periods=[prior]).rollover_units == 100


@pytest.mark.rule("RATING-004")
def test_billable_units_floors_after_quota_and_rollover():
    assert rate(events=[event(260, "2026-02-10T00:00:00")]).billable_units == 160


@pytest.mark.rule("RATING-005")
def test_tiers_split_at_literal_101():
    result = rate(events=[event(302, "2026-02-10T00:00:00")])
    assert (result.first_tier_units, result.second_tier_units) == (101, 101)


@pytest.mark.rule("RATING-006")
def test_amount_rounds_once_half_up_after_summing_tiers():
    result = rate(events=[event(201, "2026-02-10T00:00:00")])
    assert result.overage_amount == Decimal("5.56")


@pytest.mark.rule("RATING-007")
def test_suspension_prorates_billable_and_already_rounded_amount():
    result = rate(
        events=[event(700, "2026-02-10T00:00:00")],
        subscriptions=[
            subscription(
                status="suspended",
                suspended_on=date(2026, 2, 15),
                plan_id=UUID("10000000-0000-0000-0000-000000000002"),
            )
        ],
        plans=[
            PlanRow(
                UUID("10000000-0000-0000-0000-000000000002"),
                "GROWTH",
                "growth",
                Decimal("149"),
                500,
                Decimal("0.035"),
                True,
            )
        ],
    )
    assert result.billable_units == 100
    assert result.overage_amount == Decimal("4.37")


@pytest.mark.rule("RATING-008")
def test_usage_summary_groups_and_orders_by_kind():
    events = [event(20, "2026-02-05T00:00:00"), event(30, "2026-02-06T00:00:00", "storage")]
    assert usage_summary(events, TENANT, date(2026, 2, 1), date(2026, 2, 28)) == [
        {"kind": "api", "event_count": 1, "units": 20},
        {"kind": "storage", "event_count": 1, "units": 30},
    ]


@pytest.mark.rule("RATING-009")
def test_persisted_rollover_is_unused_quota():
    result = rate(events=[event(260, "2026-02-10T00:00:00")])
    period_start = date(2026, 2, 1)
    period_end = date(2026, 2, 28)
    period_id, persisted = finalize_result(result, TENANT, period_start, period_end)
    assert period_id == UUID("27cdd7d2-32b3-afc0-922c-e9858e767b6d")
    assert persisted.result_id == UUID("175fe5b7-8f91-1c3a-8586-1ef5c455fc9a")
    assert persisted.rollover_units == 0
    assert persisted.created_at == datetime(2026, 2, 28, tzinfo=UTC)
    assert persisted.subscription_id == SUB_ID


@pytest.mark.rule("RATING-010")
def test_missing_subscription_or_plan_preserves_usage_and_nulls_money():
    missing_plan = UUID("10000000-0000-0000-0000-000000000099")
    result = rate(
        events=[event(7, "2026-02-10T00:00:00")],
        subscriptions=[subscription(plan_id=missing_plan)],
        plans=[],
    )
    assert (
        result.used_units,
        result.quota_units,
        result.rollover_units,
        result.billable_units,
    ) == (7, None, 0, 0)
    assert result.overage_amount is None
