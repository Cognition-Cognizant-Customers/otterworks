from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.domain import (
    PlanRow,
    PriorRatingRow,
    RatingResultRow,
    SubscriptionRow,
    UsageEventRow,
    finalize_rating,
    md5_uuid,
    rate_usage,
    rollover_credit,
    usage_summary,
)

TENANT = UUID("00000000-0000-0000-0000-000000000001")
STARTER_ID = UUID("10000000-0000-0000-0000-000000000001")
SUBSCRIPTION = UUID("20000000-0000-0000-0000-000000000001")
PERIOD_START = date(2026, 2, 1)
PERIOD_END = date(2026, 2, 28)

STARTER = PlanRow(STARTER_ID, "STARTER", "starter", Decimal("49"), 100, Decimal("0.055"), True)
ACTIVE_SUB = SubscriptionRow(
    SUBSCRIPTION, TENANT, STARTER_ID, date(2026, 1, 1), None, "active", None
)


def event(units: int, day: int = 10, kind: str = "api") -> UsageEventRow:
    return UsageEventRow(TENANT, datetime(2026, 2, day, 10, tzinfo=UTC), units, kind)


def rate(plan=STARTER, subscription=ACTIVE_SUB, events=(), priors=()):
    return rate_usage(
        plan, subscription, list(events), list(priors), TENANT, PERIOD_START, PERIOD_END
    )


@pytest.mark.rule("RATING-001")
def test_usage_in_inclusive_window_nets_rollover_and_quota() -> None:
    events = [
        event(260),
        UsageEventRow(TENANT, datetime(2026, 1, 31, 23, tzinfo=UTC), 999, "api"),
        UsageEventRow(TENANT, datetime(2026, 3, 1, 0, tzinfo=UTC), 999, "api"),
    ]
    priors = [PriorRatingRow(date(2026, 1, 1), 100)]
    outcome = rate(events=events, priors=priors)
    assert outcome.used_units == 260
    assert outcome.billable_units == max(260 - 100 - 100, 0)


@pytest.mark.rule("RATING-001")
def test_cancelled_subscription_is_still_rated() -> None:
    cancelled = replace(ACTIVE_SUB, status="cancelled")
    assert rate(subscription=cancelled, events=[event(150)]).billable_units == 50


@pytest.mark.rule("RATING-002")
def test_rollover_window_is_three_months_capped_at_twice_included() -> None:
    priors = [
        PriorRatingRow(date(2025, 11, 1), 100),
        PriorRatingRow(date(2025, 12, 1), 100),
        PriorRatingRow(date(2026, 1, 1), 100),
        PriorRatingRow(date(2025, 10, 31), 100),
        PriorRatingRow(PERIOD_START, 100),
    ]
    assert rollover_credit(priors, PERIOD_START, 100) == 200
    assert rollover_credit(priors[:1], PERIOD_START, 100) == 100


@pytest.mark.rule("RATING-003")
def test_suspension_prorates_tail_fraction_without_retiering() -> None:
    growth = PlanRow(
        UUID("10000000-0000-0000-0000-000000000002"),
        "GROWTH",
        "growth",
        Decimal("149"),
        500,
        Decimal("0.035"),
        True,
    )
    suspended = SubscriptionRow(
        SUBSCRIPTION, TENANT, growth.plan_id, date(2026, 1, 1), None, "suspended", date(2026, 2, 15)
    )
    outcome = rate(plan=growth, subscription=suspended, events=[event(700)])
    assert outcome.billable_units == 100
    assert outcome.overage_amount == Decimal("4.37")
    assert outcome.first_tier_units == 101
    assert outcome.second_tier_units == 99


@pytest.mark.rule("RATING-004")
def test_first_tier_priced_at_plan_rate_rounded_half_up() -> None:
    outcome = rate(events=[event(201, day=28)])
    assert outcome.first_tier_units == 101
    assert outcome.overage_amount == Decimal("5.56")


@pytest.mark.rule("RATING-005")
def test_first_tier_boundary_is_101_units() -> None:
    outcome = rate(events=[event(201, day=28)])
    assert outcome.billable_units == 101
    assert outcome.first_tier_units == 101
    assert outcome.second_tier_units == 0


@pytest.mark.rule("RATING-006")
def test_second_tier_priced_at_one_and_a_half_times_rate() -> None:
    scale = PlanRow(
        UUID("10000000-0000-0000-0000-000000000003"),
        "SCALE",
        "scale",
        Decimal("499"),
        2000,
        Decimal("0.02"),
        True,
    )
    outcome = rate(plan=scale, events=[event(2201)])
    assert outcome.first_tier_units == 101
    assert outcome.second_tier_units == 100
    assert outcome.overage_amount == Decimal("5.02")


@pytest.mark.rule("RATING-007")
def test_summary_groups_by_kind_in_alphabetical_order() -> None:
    events = [event(20, day=5), event(30, day=6, kind="storage"), event(5, day=7)]
    rows = usage_summary(events, PERIOD_START, PERIOD_END)
    assert [(row.kind, row.event_count, row.units) for row in rows] == [
        ("api", 2, 25),
        ("storage", 1, 30),
    ]


class FakeRatingRepository:
    def __init__(self, plans, subscriptions, events, priors) -> None:
        self._plans = plans
        self._subscriptions = subscriptions
        self._events = events
        self._priors = priors
        self.periods: dict[UUID, tuple[UUID, date, date]] = {}
        self.results: dict[UUID, RatingResultRow] = {}
        self.result_updates: list[tuple] = []

    def list_plans(self):
        return list(self._plans)

    def list_subscriptions(self, tenant_id):
        return list(self._subscriptions)

    def list_usage_events(self, tenant_id):
        return list(self._events)

    def list_prior_ratings(self, tenant_id):
        return list(self._priors)

    def get_rating_period(self, tenant_id, period_start):
        for period_id, (tenant, start, _end) in self.periods.items():
            if tenant == tenant_id and start == period_start:
                return period_id
        return None

    def insert_rating_period(self, period_id, tenant_id, period_start, period_end):
        self.periods[period_id] = (tenant_id, period_start, period_end)

    def update_rating_period(self, period_id, period_end):
        tenant, start, _ = self.periods[period_id]
        self.periods[period_id] = (tenant, start, period_end)

    def get_rating_result(self, result_id):
        return self.results.get(result_id)

    def insert_rating_result(self, result):
        self.results[result.result_id] = result

    def update_rating_result(
        self, result_id, used_units, rollover_units, billable_units, overage_amount
    ):
        self.result_updates.append(
            (result_id, used_units, rollover_units, billable_units, overage_amount)
        )
        self.results[result_id] = replace(
            self.results[result_id],
            used_units=used_units,
            rollover_units=rollover_units,
            billable_units=billable_units,
            overage_amount=overage_amount,
        )


@pytest.mark.rule("RATING-008")
def test_finalize_persists_md5_ids_and_unused_quota_as_rollover() -> None:
    repository = FakeRatingRepository(
        [STARTER],
        [ACTIVE_SUB],
        [event(260)],
        [
            PriorRatingRow(date(2025, 11, 1), 100),
            PriorRatingRow(date(2025, 12, 1), 100),
            PriorRatingRow(date(2026, 1, 1), 100),
        ],
    )
    result = finalize_rating(repository, TENANT, PERIOD_START, PERIOD_END)
    period_id = md5_uuid(f"{TENANT}{PERIOD_START.isoformat()}")
    assert result.result_id == md5_uuid(str(period_id))
    assert result.period_id == period_id
    assert result.subscription_id == SUBSCRIPTION
    assert result.used_units == 260
    assert result.quota_units == 100
    assert result.rollover_units == 0
    assert result.billable_units == 0
    assert result.created_at == datetime(2026, 2, 28, tzinfo=UTC)


@pytest.mark.rule("RATING-008")
def test_finalize_updates_existing_result_without_touching_quota() -> None:
    repository = FakeRatingRepository([STARTER], [ACTIVE_SUB], [event(260)], [])
    first = finalize_rating(repository, TENANT, PERIOD_START, PERIOD_END)
    repository._events.append(event(40, day=20))
    second = finalize_rating(repository, TENANT, PERIOD_START, PERIOD_END)
    assert first.result_id == second.result_id
    assert repository.result_updates
    assert second.used_units == 300
    assert second.quota_units == first.quota_units
    assert second.created_at == first.created_at
