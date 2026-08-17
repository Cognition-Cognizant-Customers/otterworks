from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

import psycopg
from bson import Decimal128

from app.domain import (
    EntitlementRow,
    PlanRow,
    RatingPeriod,
    RatingResult,
    SubscriptionRow,
    UsageEvent,
)


class PostgresPlansRepository:
    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    def list_plans(self) -> list[PlanRow]:
        rows = self.connection.execute(
            """
            SELECT id, code, tier, monthly_fee, included_units, overage_rate, active
            FROM billing_svc.plans
            """
        ).fetchall()
        return [
            PlanRow(
                plan_id=row["id"],
                code=row["code"],
                tier=row["tier"],
                monthly_fee=Decimal(row["monthly_fee"]),
                included_units=row["included_units"],
                overage_rate=Decimal(row["overage_rate"]),
                active=row["active"],
            )
            for row in rows
        ]

    def find_entitlements(self, tenant_id: UUID) -> list[EntitlementRow]:
        rows = self.connection.execute(
            """
            SELECT t.id AS tenant_id, p.code AS plan_code, p.tier,
                   p.monthly_fee, p.included_units, s.status,
                   s.starts_on, s.ends_on
            FROM billing_svc.tenants t
            JOIN billing_svc.subscriptions s ON s.tenant_id = t.id
            JOIN billing_svc.plans p ON p.id = s.plan_id
            WHERE t.id = %s
            """,
            (tenant_id,),
        ).fetchall()
        return [
            EntitlementRow(
                tenant_id=row["tenant_id"],
                plan_code=row["plan_code"],
                tier=row["tier"],
                monthly_fee=Decimal(row["monthly_fee"]),
                included_units=row["included_units"],
                subscription_status=row["status"],
                ends_on=row["ends_on"],
                starts_on=row["starts_on"],
            )
            for row in rows
        ]

    def list_subscriptions(self, tenant_id: UUID) -> list[SubscriptionRow]:
        rows = self.connection.execute(
            """
            SELECT id, tenant_id, plan_id, starts_on, ends_on, status, suspended_on
            FROM billing_svc.subscriptions
            WHERE tenant_id = %s
            """,
            (tenant_id,),
        ).fetchall()
        return [
            SubscriptionRow(
                subscription_id=row["id"],
                tenant_id=row["tenant_id"],
                plan_id=row["plan_id"],
                starts_on=row["starts_on"],
                ends_on=row["ends_on"],
                status=row["status"],
                suspended_on=row["suspended_on"],
            )
            for row in rows
        ]

    def update_subscription(self, subscription_id: UUID, ends_on: date, status: str) -> None:
        self.connection.execute(
            """
            UPDATE billing_svc.subscriptions
            SET ends_on = %s, status = %s
            WHERE id = %s
            """,
            (ends_on, status, subscription_id),
        )

    def insert_subscription(
        self,
        subscription_id: UUID,
        tenant_id: UUID,
        plan_id: UUID,
        starts_on: date,
        status: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO billing_svc.subscriptions
                (id, tenant_id, plan_id, starts_on, status)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (subscription_id, tenant_id, plan_id, starts_on, status),
        )


class MongoRatingRepository:
    def __init__(self, database) -> None:
        self.database = database

    def _ensure_indexes(self) -> None:
        key = (id(self.database.client), self.database.name)
        if key not in _INDEXED_DATABASES:
            self.database.rating_periods.create_index(
                [("tenant_id", 1), ("period_start", 1)],
                unique=True,
                name="tenant_period_start_unique",
            )
            _INDEXED_DATABASES.add(key)

    def list_usage(
        self, tenant_id: UUID, period_start: date, period_end: date
    ) -> list[UsageEvent]:
        self._ensure_indexes()
        start = datetime.combine(period_start, time.min, tzinfo=UTC)
        end = datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=UTC)
        rows = self.database.usage_events.find(
            {"tenant_id": tenant_id, "occurred_at": {"$gte": start, "$lt": end}}
        )
        return [
            UsageEvent(row["_id"], row["tenant_id"], row["occurred_at"], row["units"], row["kind"])
            for row in rows
        ]

    def list_periods(self, tenant_id: UUID) -> list[RatingPeriod]:
        self._ensure_indexes()
        rows = self.database.rating_periods.find({"tenant_id": tenant_id})
        return [self._period(row) for row in rows]

    @staticmethod
    def _period(row: dict) -> RatingPeriod:
        result = row["result"]
        amount = result["overage_amount"]
        return RatingPeriod(
            row["_id"],
            row["tenant_id"],
            row["period_start"].date(),
            row["period_end"].date(),
            RatingResult(
                result["result_id"],
                result["subscription_id"],
                result["used_units"],
                result["quota_units"],
                result["rollover_units"],
                result["billable_units"],
                amount.to_decimal() if isinstance(amount, Decimal128) else amount,
                result["created_at"],
            ),
        )

    def upsert_rating(
        self,
        period_id: UUID,
        tenant_id: UUID,
        period_start: date,
        period_end: date,
        result: RatingResult,
    ) -> RatingPeriod:
        self._ensure_indexes()
        period_start_value = datetime.combine(period_start, time.min, tzinfo=UTC)
        period_end_value = datetime.combine(period_end, time.min, tzinfo=UTC)
        amount_value = (
            Decimal128(result.overage_amount) if result.overage_amount is not None else None
        )
        self.database.rating_periods.update_one(
            {
                "_id": period_id,
                "tenant_id": tenant_id,
                "period_start": period_start_value,
            },
            [
                {
                    "$set": {
                        "tenant_id": {"$literal": tenant_id},
                        "period_start": {"$literal": period_start_value},
                        "period_end": {"$literal": period_end_value},
                        "result.result_id": {
                            "$cond": [
                                {"$eq": [{"$type": "$result.result_id"}, "missing"]},
                                {"$literal": result.result_id},
                                "$result.result_id",
                            ]
                        },
                        "result.subscription_id": {
                            "$cond": [
                                {"$eq": [{"$type": "$result.subscription_id"}, "missing"]},
                                {"$literal": result.subscription_id},
                                "$result.subscription_id",
                            ]
                        },
                        "result.quota_units": {
                            "$cond": [
                                {"$eq": [{"$type": "$result.quota_units"}, "missing"]},
                                {"$literal": result.quota_units},
                                "$result.quota_units",
                            ]
                        },
                        "result.created_at": {
                            "$cond": [
                                {"$eq": [{"$type": "$result.created_at"}, "missing"]},
                                {"$literal": result.created_at},
                                "$result.created_at",
                            ]
                        },
                        "result.used_units": {"$literal": result.used_units},
                        "result.rollover_units": {"$literal": result.rollover_units},
                        "result.billable_units": {"$literal": result.billable_units},
                        "result.overage_amount": {"$literal": amount_value},
                    }
                }
            ],
            upsert=True,
        )
        row = self.database.rating_periods.find_one(
            {
                "_id": period_id,
                "tenant_id": tenant_id,
                "period_start": period_start_value,
            }
        )
        return self._period(row)


_INDEXED_DATABASES: set[tuple[int, str]] = set()
