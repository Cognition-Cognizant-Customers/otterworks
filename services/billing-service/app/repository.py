from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import psycopg
import pymongo
from bson import Decimal128
from pymongo.client_session import ClientSession

from app.domain import (
    CreditNoteRow,
    EntitlementRow,
    InvoiceLine,
    InvoiceRow,
    InvoicingRefusal,
    PlanRow,
    Rating,
    RatingPeriod,
    RatingResult,
    SubscriptionRow,
    UsageEvent,
    consume_credits,
    deterministic_uuid,
    finalize_result,
    invoice_ids,
    invoice_totals,
    ordered_lines,
    preview,
    stored_line_amount,
)

_INDEXED_DATABASES: set[tuple[object, str]] = set()
_INVOICING_INDEXED_DATABASES: set[tuple[object, str]] = set()


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

    def invoice_context(
        self, tenant_id: UUID, period_start: date, period_end: date
    ) -> tuple[PlanRow, bool]:
        row = self.connection.execute(
            """
            SELECT p.id, p.code, p.tier, p.monthly_fee, p.included_units,
                   p.overage_rate, p.active, t.tax_exempt
            FROM billing_svc.tenants t
            JOIN billing_svc.subscriptions s ON s.tenant_id = t.id
            JOIN billing_svc.plans p ON p.id = s.plan_id
            WHERE t.id = %s
              AND s.starts_on <= %s
              AND (s.ends_on IS NULL OR s.ends_on >= %s)
            ORDER BY s.starts_on DESC
            LIMIT 1
            """,
            (tenant_id, period_end, period_start),
        ).fetchone()
        if row is None:
            raise InvoicingRefusal("subscription not found")
        return (
            PlanRow(
                plan_id=row["id"],
                code=row["code"],
                tier=row["tier"],
                monthly_fee=Decimal(row["monthly_fee"]),
                included_units=row["included_units"],
                overage_rate=Decimal(row["overage_rate"]),
                active=row["active"],
            ),
            row["tax_exempt"],
        )

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
        key = (self.database.client, self.database.name)
        if key not in _INDEXED_DATABASES:
            self.database.rating_periods.create_index(
                [("tenant_id", 1), ("period_start", 1)],
                unique=True,
                name="tenant_period_start_unique",
            )
            _INDEXED_DATABASES.add(key)

    def list_usage(
        self,
        tenant_id: UUID,
        period_start: date,
        period_end: date,
        session: ClientSession | None = None,
    ) -> list[UsageEvent]:
        self._ensure_indexes()
        start = datetime.combine(period_start, time.min, tzinfo=UTC)
        end = datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=UTC)
        rows = self.database.usage_events.find(
            {"tenant_id": tenant_id, "occurred_at": {"$gte": start, "$lt": end}}, session=session
        )
        return [
            UsageEvent(row["_id"], row["tenant_id"], row["occurred_at"], row["units"], row["kind"])
            for row in rows
        ]

    def list_periods(
        self, tenant_id: UUID, session: ClientSession | None = None
    ) -> list[RatingPeriod]:
        self._ensure_indexes()
        rows = self.database.rating_periods.find({"tenant_id": tenant_id}, session=session)
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
        session: ClientSession | None = None,
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
            session=session,
        )
        row = self.database.rating_periods.find_one(
            {
                "_id": period_id,
                "tenant_id": tenant_id,
                "period_start": period_start_value,
            },
            session=session,
        )
        return self._period(row)


class MongoInvoicingRepository:
    collections = ("billing_invoices", "billing_credit_notes")

    def __init__(self, database) -> None:
        self.database = database

    @staticmethod
    def _date(value: date) -> datetime:
        return datetime.combine(value, time.min, tzinfo=UTC)

    @staticmethod
    def _money(value: Decimal) -> Decimal128:
        return Decimal128(value)

    @staticmethod
    def _decimal(value: Decimal128 | Decimal) -> Decimal:
        return value.to_decimal() if isinstance(value, Decimal128) else value

    def ensure_schema(self) -> None:
        validators = self._validators()
        for name in self.collections:
            if name not in self.database.list_collection_names():
                self.database.create_collection(
                    name,
                    validator=validators[name],
                    validationLevel="strict",
                    validationAction="error",
                )
            else:
                self.database.command(
                    "collMod",
                    name,
                    validator=validators[name],
                    validationLevel="strict",
                    validationAction="error",
                )
        key = (self.database.client, self.database.name)
        if key not in _INVOICING_INDEXED_DATABASES:
            self.database.billing_invoices.create_index([("tenant_id", 1)])
            self.database.billing_invoices.create_index([("period_id", 1)])
            self.database.billing_credit_notes.create_index(
                [("tenant_id", 1), ("issued_on", 1), ("_id", 1)]
            )
            _INVOICING_INDEXED_DATABASES.add(key)

    def reset(self) -> None:
        for name in self.collections:
            self.database.drop_collection(name)
        _INVOICING_INDEXED_DATABASES.discard((self.database.client, self.database.name))
        self.ensure_schema()
        seed = json.loads(
            (Path(__file__).resolve().parents[1] / "db" / "mongo_seed.json").read_text()
        )
        self.database.billing_credit_notes.insert_many(
            [
                {
                    "_id": item["id"],
                    "tenant_id": item["tenant_id"],
                    "issued_on": self._date(date.fromisoformat(item["issued_on"])),
                    "amount": self._money(Decimal(item["amount"])),
                    "remaining_amount": self._money(Decimal(item["remaining_amount"])),
                }
                for item in seed["credit_notes"]
            ]
        )
        self.database.billing_invoices.insert_many(
            [
                {
                    "_id": item["id"],
                    "tenant_id": item["tenant_id"],
                    "period_id": item["period_id"],
                    "issued_at": datetime.fromisoformat(item["issued_at"].replace("Z", "+00:00")),
                    "status": item["status"],
                    "subtotal": self._money(Decimal(item["subtotal"])),
                    "tax": self._money(Decimal(item["tax"])),
                    "total": self._money(Decimal(item["total"])),
                    "line_count": len(item["lines"]),
                    "lines": [
                        {
                            "line_id": line["id"],
                            "line_no": line["line_no"],
                            "line_type": line["line_type"],
                            "description": line["description"],
                            "amount": self._money(Decimal(line["amount"])),
                        }
                        for line in sorted(item["lines"], key=lambda value: value["line_no"])
                    ],
                    "source": {"system": "legacy-billing", "seed": "mongo_seed.json"},
                }
                for item in seed["invoices"]
            ]
        )

    def credit_notes(
        self, tenant_id: UUID, session: ClientSession | None = None, positive_only: bool = False
    ) -> list[CreditNoteRow]:
        query = {"tenant_id": str(tenant_id)}
        if positive_only:
            query["remaining_amount"] = {"$gt": Decimal128("0.00")}
        rows = self.database.billing_credit_notes.find(query, session=session).sort(
            [("issued_on", 1), ("_id", 1)]
        )
        return [
            CreditNoteRow(
                UUID(row["_id"]),
                tenant_id,
                row["issued_on"].date(),
                self._decimal(row["amount"]),
                self._decimal(row["remaining_amount"]),
            )
            for row in rows
        ]

    def invoice_lines(self, invoice_id: UUID) -> list[InvoiceLine]:
        row = self.database.billing_invoices.find_one({"_id": str(invoice_id)})
        if row is None:
            return []
        return ordered_lines(
            [
                InvoiceLine(
                    line["line_no"],
                    line["line_type"],
                    line["description"],
                    self._decimal(line["amount"]),
                    Decimal("0"),
                    Decimal("0"),
                    self._decimal(line["amount"]),
                )
                for line in row["lines"]
            ]
        )

    def issue(
        self,
        plan: PlanRow,
        tax_exempt: bool,
        tenant_id: UUID,
        period_start: date,
        period_end: date,
        rating_repository: MongoRatingRepository,
        rating: Rating,
    ) -> InvoiceRow:
        period_id, invoice_id = invoice_ids(tenant_id, period_start)
        session = self.database.client.start_session()
        try:
            with session, session.start_transaction():
                _, result = finalize_result(rating, tenant_id, period_start, period_end)
                rating_repository.upsert_rating(
                    period_id,
                    tenant_id,
                    period_start,
                    period_end,
                    result,
                    session=session,
                )
                notes = self.credit_notes(tenant_id, session=session, positive_only=True)
                credit = sum((note.remaining_amount for note in notes), Decimal("0"))
                calculated = preview(
                    tenant_id,
                    period_start,
                    period_end,
                    plan,
                    rating.overage_amount,
                    tax_exempt,
                    credit,
                )
                subtotal, tax, credit_applied, total = invoice_totals(calculated)
                self.database.billing_invoices.update_one(
                    {"_id": str(invoice_id)},
                    {
                        "$set": {
                            "status": "issued",
                            "subtotal": self._money(subtotal),
                            "tax": self._money(tax),
                            "total": self._money(total),
                            "line_count": len(calculated.lines),
                            "lines": [
                                {
                                    "line_id": str(
                                        deterministic_uuid(f"{invoice_id}{line.line_no}")
                                    ),
                                    "line_no": line.line_no,
                                    "line_type": line.line_type,
                                    "description": line.description,
                                    "amount": self._money(stored_line_amount(line)),
                                }
                                for line in ordered_lines(calculated.lines)
                            ],
                            "source": {"system": "billing-service", "module": "invoicing"},
                        },
                        "$setOnInsert": {
                            "_id": str(invoice_id),
                            "tenant_id": str(tenant_id),
                            "period_id": str(period_id),
                            "issued_at": self._date(period_end),
                        },
                    },
                    upsert=True,
                    session=session,
                )
                persisted_invoice = self.database.billing_invoices.find_one(
                    {"_id": str(invoice_id)}, session=session
                )
                issued_at = persisted_invoice["issued_at"].date()
                for note_id, remaining in consume_credits(notes, credit_applied):
                    self.database.billing_credit_notes.update_one(
                        {"_id": str(note_id)},
                        {"$set": {"remaining_amount": self._money(remaining)}},
                        session=session,
                    )
        finally:
            session.end_session()
        return InvoiceRow(
            invoice_id,
            tenant_id,
            period_id,
            issued_at,
            "issued",
            subtotal,
            tax,
            total,
            calculated.lines,
        )

    def invoice_state(
        self, invoice_id: UUID, session: ClientSession | None = None
    ) -> dict[str, str]:
        row = self.database.billing_invoices.find_one({"_id": str(invoice_id)}, session=session)
        if row is None:
            raise LookupError("invoice state not found")
        return {
            "status": row["status"],
            "subtotal": f"{self._decimal(row['subtotal']):.2f}",
            "tax": f"{self._decimal(row['tax']):.2f}",
            "total": f"{self._decimal(row['total']):.2f}",
        }

    def ping(self) -> bool:
        try:
            self.database.client.admin.command("ping")
        except pymongo.errors.PyMongoError:
            return False
        return True

    @staticmethod
    def _validators() -> dict[str, dict]:
        money = {"bsonType": "decimal"}
        line = {
            "bsonType": "object",
            "required": ["line_id", "line_no", "line_type", "description", "amount"],
            "additionalProperties": False,
            "properties": {
                "line_id": {"bsonType": "string"},
                "line_no": {"bsonType": "int"},
                "line_type": {"bsonType": "string"},
                "description": {"bsonType": "string"},
                "amount": money,
            },
        }
        return {
            "billing_invoices": {
                "$jsonSchema": {
                    "bsonType": "object",
                    "required": [
                        "_id",
                        "tenant_id",
                        "period_id",
                        "issued_at",
                        "status",
                        "subtotal",
                        "tax",
                        "total",
                        "line_count",
                        "lines",
                        "source",
                    ],
                    "additionalProperties": False,
                    "properties": {
                        "_id": {"bsonType": "string"},
                        "tenant_id": {"bsonType": "string"},
                        "period_id": {"bsonType": "string"},
                        "issued_at": {"bsonType": "date"},
                        "status": {"bsonType": "string"},
                        "subtotal": money,
                        "tax": money,
                        "total": money,
                        "line_count": {"bsonType": "int"},
                        "lines": {"bsonType": "array", "items": line},
                        "source": {"bsonType": "object"},
                    },
                }
            },
            "billing_credit_notes": {
                "$jsonSchema": {
                    "bsonType": "object",
                    "required": ["_id", "tenant_id", "issued_on", "amount", "remaining_amount"],
                    "additionalProperties": False,
                    "properties": {
                        "_id": {"bsonType": "string"},
                        "tenant_id": {"bsonType": "string"},
                        "issued_on": {"bsonType": "date"},
                        "amount": money,
                        "remaining_amount": money,
                    },
                }
            },
        }
