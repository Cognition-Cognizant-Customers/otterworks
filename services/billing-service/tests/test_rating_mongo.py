import os
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from app.domain import Rating, SubscriptionRow, finalize_result
from app.repository import MongoRatingRepository


@pytest.mark.skipif(
    os.getenv("RATING_MONGO_TESTS") != "1",
    reason="set RATING_MONGO_TESTS=1 for the local Mongo fixture",
)
def test_finalize_twice_preserves_first_write_fields():
    client = MongoClient(
        os.getenv("BILLING_SVC_MONGO_URI", "mongodb://localhost:27693"),
        tz_aware=True,
        uuidRepresentation="standard",
    )
    database = client[os.getenv("BILLING_SVC_MONGO_DB", "ow_tp_ratingext")]
    repository = MongoRatingRepository(database)
    tenant_id = UUID("90000000-0000-0000-0000-000000000001")
    subscription_id = UUID("90000000-0000-0000-0000-000000000002")
    subscription = SubscriptionRow(
        subscription_id,
        tenant_id,
        UUID("10000000-0000-0000-0000-000000000001"),
        date(2026, 1, 1),
        None,
        "active",
        None,
    )
    period_start = date(2026, 2, 1)
    first_rating = Rating(10, 100, 0, 0, 0, 0, Decimal("0.00"), subscription)
    second_rating = Rating(40, 100, 0, 0, 0, 0, Decimal("0.00"), subscription)
    database.rating_periods.delete_many({"tenant_id": tenant_id})

    period_id, first_result = finalize_result(
        first_rating, tenant_id, period_start, date(2026, 2, 28)
    )
    repository.upsert_rating(
        period_id, tenant_id, period_start, date(2026, 2, 28), first_result
    )
    _, second_result = finalize_result(
        second_rating, tenant_id, period_start, date(2026, 3, 31)
    )
    persisted = repository.upsert_rating(
        period_id, tenant_id, period_start, date(2026, 3, 31), second_result
    )

    assert persisted.period_end == date(2026, 3, 31)
    assert persisted.result.used_units == 40
    assert persisted.result.rollover_units == 60
    assert persisted.result.billable_units == 0
    assert persisted.result.overage_amount == Decimal("0.00")
    assert persisted.result.quota_units == 100
    assert persisted.result.created_at == datetime(2026, 2, 28, tzinfo=UTC)
    assert persisted.result.subscription_id == subscription_id
    index = database.rating_periods.index_information()["tenant_period_start_unique"]
    assert index["unique"] is True
    collision_tenant = UUID("00000000-0000-0000-0000-000000000001")
    collision_subscription = SubscriptionRow(
        UUID("20000000-0000-0000-0000-000000000001"),
        collision_tenant,
        UUID("10000000-0000-0000-0000-000000000001"),
        date(2025, 1, 1),
        None,
        "active",
        None,
    )
    collision_rating = Rating(
        0, 100, 100, 0, 0, 0, Decimal("0.00"), collision_subscription
    )
    collision_start = date(2026, 1, 1)
    collision_period_id, collision_result = finalize_result(
        collision_rating, collision_tenant, collision_start, date(2026, 1, 31)
    )
    with pytest.raises(DuplicateKeyError):
        repository.upsert_rating(
            collision_period_id,
            collision_tenant,
            collision_start,
            date(2026, 1, 31),
            collision_result,
        )
    assert (
        database.rating_periods.count_documents(
            {
                "tenant_id": collision_tenant,
                "period_start": datetime(2026, 1, 1, tzinfo=UTC),
            }
        )
        == 1
    )
    client.close()
