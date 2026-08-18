from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from app.domain import (
    NullUsageUnitsError,
    RatedPlan,
    RatedSubscription,
    RatingHistoryEntry,
    SubscriptionNotFoundError,
    UsageEvent,
    current_subscription,
    finalize_rating,
    usage_rating,
    usage_summary,
)

TENANT = UUID("00000000-0000-0000-0000-000000000001")
SUBSCRIPTION = UUID("20000000-0000-0000-0000-000000000001")
PERIOD_START = date(2026, 2, 1)
PERIOD_END = date(2026, 2, 28)

STARTER = RatedPlan(included_units=100, overage_rate=Decimal("0.055000"))
GROWTH = RatedPlan(included_units=500, overage_rate=Decimal("0.035000"))


def subscription(
    plan: RatedPlan = STARTER,
    starts_on: date = date(2026, 1, 1),
    ends_on: date | None = None,
    status: str = "active",
    suspended_on: date | None = None,
    subscription_id: UUID = SUBSCRIPTION,
) -> RatedSubscription:
    return RatedSubscription(
        subscription_id=subscription_id,
        plan=plan,
        starts_on=starts_on,
        ends_on=ends_on,
        status=status,
        suspended_on=suspended_on,
    )


def event(
    units: int | None, occurred_on: date = date(2026, 2, 10), kind: str = "api"
) -> UsageEvent:
    return UsageEvent(occurred_on=occurred_on, units=units, kind=kind)


@pytest.mark.rule("RATING-001")
def test_latest_overlapping_subscription_supplies_the_plan_quota():
    older = subscription(
        plan=GROWTH,
        starts_on=date(2025, 1, 1),
        ends_on=date(2026, 2, 10),
        subscription_id=UUID("20000000-0000-0000-0000-000000000009"),
    )
    newer = subscription(starts_on=date(2026, 2, 5))
    assert current_subscription([older, newer], PERIOD_START, PERIOD_END) is newer
    rating = usage_rating(TENANT, [older, newer], [], [], PERIOD_START, PERIOD_END)
    assert rating.quota_units == 100


@pytest.mark.rule("RATING-001")
def test_no_overlapping_subscription_fails_closed():
    ended = subscription(ends_on=date(2026, 1, 31))
    with pytest.raises(SubscriptionNotFoundError):
        usage_rating(TENANT, [ended], [], [], PERIOD_START, PERIOD_END)


@pytest.mark.rule("RATING-002")
def test_used_units_sum_events_inside_the_period_only():
    events = [
        event(260),
        event(50, occurred_on=date(2026, 1, 31)),
        event(70, occurred_on=date(2026, 3, 1)),
    ]
    rating = usage_rating(TENANT, [subscription()], events, [], PERIOD_START, PERIOD_END)
    assert rating.used_units == 260
    assert rating.billable_units == 160


@pytest.mark.rule("RATING-002")
def test_null_units_never_fail_open_into_a_rating_result():
    with pytest.raises(NullUsageUnitsError):
        usage_rating(
            TENANT, [subscription()], [event(260), event(None)], [], PERIOD_START, PERIOD_END
        )


@pytest.mark.rule("RATING-003")
def test_rollover_sums_three_months_of_history_capped_at_twice_quota():
    history = [
        RatingHistoryEntry(period_start=date(2025, 11, 1), rollover_units=100),
        RatingHistoryEntry(period_start=date(2025, 12, 1), rollover_units=100),
        RatingHistoryEntry(period_start=date(2026, 1, 1), rollover_units=100),
        RatingHistoryEntry(period_start=date(2025, 10, 1), rollover_units=999),
    ]
    rating = usage_rating(
        TENANT, [subscription()], [event(260)], history, PERIOD_START, PERIOD_END
    )
    assert rating.rollover_units == 200
    assert rating.overage_amount == Decimal("0.00")


@pytest.mark.rule("RATING-004")
def test_tier_split_caps_the_first_tier_at_101_units():
    at_boundary = usage_rating(
        TENANT, [subscription()], [event(201)], [], PERIOD_START, PERIOD_END
    )
    assert (at_boundary.first_tier_units, at_boundary.second_tier_units) == (101, 0)
    past_boundary = usage_rating(
        TENANT, [subscription()], [event(202)], [], PERIOD_START, PERIOD_END
    )
    assert (past_boundary.first_tier_units, past_boundary.second_tier_units) == (101, 1)


@pytest.mark.rule("RATING-005")
def test_second_tier_charges_one_and_a_half_times_the_rate_rounded_half_up():
    plan = RatedPlan(included_units=2000, overage_rate=Decimal("0.020000"))
    rating = usage_rating(
        TENANT, [subscription(plan=plan)], [event(2201)], [], PERIOD_START, PERIOD_END
    )
    assert rating.overage_amount == Decimal("5.02")
    boundary = usage_rating(TENANT, [subscription()], [event(201)], [], PERIOD_START, PERIOD_END)
    assert boundary.overage_amount == Decimal("5.56")


@pytest.mark.rule("RATING-006")
def test_suspension_prorates_billable_units_and_amount():
    suspended = subscription(
        plan=GROWTH, status="suspended", suspended_on=date(2026, 2, 15)
    )
    rating = usage_rating(TENANT, [suspended], [event(700)], [], PERIOD_START, PERIOD_END)
    assert rating.billable_units == 100
    assert rating.overage_amount == Decimal("4.37")
    assert (rating.first_tier_units, rating.second_tier_units) == (101, 99)


@pytest.mark.rule("RATING-007")
def test_usage_summary_groups_by_kind_in_kind_order():
    events = [
        event(30, occurred_on=date(2026, 2, 6), kind="storage"),
        event(20, occurred_on=date(2026, 2, 5), kind="api"),
        event(5, occurred_on=date(2026, 3, 2), kind="api"),
    ]
    rows = usage_summary(events, PERIOD_START, PERIOD_END)
    assert [(row.kind, row.event_count, row.units) for row in rows] == [
        ("api", 1, 20),
        ("storage", 1, 30),
    ]
    with pytest.raises(NullUsageUnitsError):
        usage_summary([event(None)], PERIOD_START, PERIOD_END)


@pytest.mark.rule("RATING-008")
def test_finalization_is_deterministic_and_stores_unused_quota_as_rollover():
    finalized = finalize_rating(
        TENANT, [subscription()], [event(260)], [], PERIOD_START, PERIOD_END
    )
    again = finalize_rating(
        TENANT, [subscription()], [event(260)], [], PERIOD_START, PERIOD_END
    )
    assert finalized.period_id == again.period_id
    assert finalized.result_id == again.result_id
    assert finalized.subscription_id == SUBSCRIPTION
    assert finalized.rollover_units == 0
    assert finalized.used_units == 260

    idle = finalize_rating(TENANT, [subscription()], [], [], PERIOD_START, PERIOD_END)
    assert idle.rollover_units == 100
    assert idle.billable_units == 0
