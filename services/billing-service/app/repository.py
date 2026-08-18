from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import psycopg
from bson.decimal128 import Decimal128
from pymongo.database import Database

from app.domain import (
    CreditConsumption,
    CreditNote,
    EntitlementRow,
    FinalizedRating,
    InvoiceLineRecord,
    IssuedInvoice,
    PlanRow,
    RatedPlan,
    RatedSubscription,
    RatingHistoryEntry,
    SubscriptionRow,
    UsageEvent,
)

ORIGIN = "billing_svc"


class CustomerNotFoundError(LookupError):
    """No customer document exists for the tenant in this namespace."""


def _as_date(value: datetime | None) -> date | None:
    return value.date() if value is not None else None


def _as_decimal(value: Decimal128 | Decimal | str) -> Decimal:
    if isinstance(value, Decimal128):
        return value.to_decimal()
    return Decimal(value)


def _at_midnight(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


class MongoCustomersRepository:
    """Reads and writes the migrated `customers` documents for one namespace.

    Reads are namespace-scoped; writes touch only the `rating_history`
    array of an existing customer document and never create documents.
    """

    def __init__(self, database: Database, ns: str) -> None:
        self.collection = database["customers"]
        self.ns = ns

    def _document(self, tenant_id: UUID) -> dict:
        document = self.collection.find_one({"ns": self.ns, "tenant_id": str(tenant_id)})
        if document is None:
            raise CustomerNotFoundError(f"no customer document for tenant {tenant_id}")
        return document

    def find_subscriptions(self, tenant_id: UUID) -> list[RatedSubscription]:
        return [
            RatedSubscription(
                subscription_id=UUID(item["subscription_id"]),
                plan=RatedPlan(
                    included_units=item["plan"]["included_units"],
                    overage_rate=_as_decimal(item["plan"]["overage_rate"]),
                    code=item["plan"]["code"],
                    monthly_fee=_as_decimal(item["plan"]["monthly_fee"]),
                ),
                starts_on=item["starts_on"].date(),
                ends_on=_as_date(item.get("ends_on")),
                status=item["status"],
                suspended_on=_as_date(item.get("suspended_on")),
            )
            for item in self._document(tenant_id).get("subscriptions", [])
        ]

    def find_usage_events(self, tenant_id: UUID) -> list[UsageEvent]:
        return [
            UsageEvent(
                occurred_on=item["occurred_at"].date(),
                units=item.get("units"),
                kind=item["kind"],
            )
            for item in self._document(tenant_id).get("usage_events", [])
        ]

    def find_rating_history(self, tenant_id: UUID) -> list[RatingHistoryEntry]:
        return [
            RatingHistoryEntry(
                period_start=item["period_start"].date(),
                rollover_units=item["rollover_units"],
            )
            for item in self._document(tenant_id).get("rating_history", [])
        ]

    def find_tax_exempt(self, tenant_id: UUID) -> bool:
        return self._document(tenant_id)["tax_exempt"]

    def find_credit_notes(self, tenant_id: UUID) -> list[CreditNote]:
        notes = [
            CreditNote(
                credit_note_id=UUID(item["credit_note_id"]),
                issued_on=item["issued_on"].date(),
                remaining_amount=(
                    _as_decimal(item["remaining_amount"])
                    if item.get("remaining_amount") is not None
                    else None
                ),
            )
            for item in self._document(tenant_id).get("credit_notes", [])
        ]
        return sorted(notes, key=lambda note: (note.issued_on, note.credit_note_id))

    def apply_credit_consumptions(
        self, tenant_id: UUID, consumptions: list[CreditConsumption]
    ) -> None:
        document = self._document(tenant_id)
        for consumption in consumptions:
            self.collection.update_one(
                {"_id": document["_id"], "ns": self.ns},
                {
                    "$set": {
                        "credit_notes.$[note].remaining_amount": Decimal128(
                            consumption.remaining_amount
                        )
                    }
                },
                array_filters=[{"note.credit_note_id": str(consumption.credit_note_id)}],
            )

    def upsert_rating_result(self, tenant_id: UUID, finalized: FinalizedRating) -> list[dict]:
        document = self._document(tenant_id)
        period_start = _at_midnight(finalized.period_start)
        period_end = _at_midnight(finalized.period_end)
        for _ in range(2):
            history = document.get("rating_history", [])
            existing = next(
                (
                    entry
                    for entry in history
                    if entry["period_start"].date() == finalized.period_start
                ),
                None,
            )
            if existing is not None:
                self.collection.update_one(
                    {"_id": document["_id"], "ns": self.ns},
                    {
                        "$set": {
                            "rating_history.$[entry].period_end": period_end,
                            "rating_history.$[entry].used_units": finalized.used_units,
                            "rating_history.$[entry].rollover_units": finalized.rollover_units,
                            "rating_history.$[entry].billable_units": finalized.billable_units,
                            "rating_history.$[entry].overage_amount": Decimal128(
                                finalized.overage_amount
                            ),
                        }
                    },
                    array_filters=[{"entry.result_id": existing["result_id"]}],
                )
                break
            inserted = self.collection.update_one(
                {
                    "_id": document["_id"],
                    "ns": self.ns,
                    "rating_history": {
                        "$not": {"$elemMatch": {"period_start": period_start}}
                    },
                },
                {
                    "$push": {
                        "rating_history": {
                            "result_id": str(finalized.result_id),
                            "period_id": str(finalized.period_id),
                            "period_start": period_start,
                            "period_end": period_end,
                            "subscription_id": str(finalized.subscription_id),
                            "used_units": finalized.used_units,
                            "quota_units": finalized.quota_units,
                            "rollover_units": finalized.rollover_units,
                            "billable_units": finalized.billable_units,
                            "overage_amount": Decimal128(finalized.overage_amount),
                            "created_at": period_end,
                        }
                    }
                },
            )
            if inserted.modified_count == 1:
                break
            document = self._document(tenant_id)
        return [
            entry
            for entry in self._document(tenant_id).get("rating_history", [])
            if entry["period_start"].date() == finalized.period_start
        ]


class MongoInvoicesRepository:
    """Reads and writes the migrated `invoices` documents for one namespace.

    Invoice lines are embedded in the invoice document, so reads and
    writes each touch exactly one namespace/origin-scoped document.
    """

    def __init__(self, database: Database, ns: str) -> None:
        self.collection = database["invoices"]
        self.ns = ns

    def find_lines(self, invoice_id: UUID) -> list[InvoiceLineRecord]:
        document = self.collection.find_one(
            {"ns": self.ns, "invoice_id": str(invoice_id)}
        )
        if document is None:
            return []
        lines = [
            InvoiceLineRecord(
                line_id=UUID(item["line_id"]),
                line_no=item["line_no"],
                line_type=item["line_type"],
                description=item["description"],
                amount=_as_decimal(item["amount"]),
            )
            for item in document.get("lines", [])
        ]
        return sorted(lines, key=lambda line: line.line_no)

    def upsert_issued(self, invoice: IssuedInvoice) -> None:
        document_id = f"{self.ns}:{ORIGIN}:{invoice.invoice_id}"
        self.collection.update_one(
            {"_id": document_id, "ns": self.ns, "origin": ORIGIN},
            {
                "$set": {
                    "status": invoice.status,
                    "subtotal": Decimal128(invoice.subtotal),
                    "tax": Decimal128(invoice.tax),
                    "total": Decimal128(invoice.total),
                    "lines": [
                        {
                            "line_id": str(line.line_id),
                            "line_no": line.line_no,
                            "line_type": line.line_type,
                            "description": line.description,
                            "amount": Decimal128(line.amount),
                        }
                        for line in invoice.lines
                    ],
                },
                "$setOnInsert": {
                    "invoice_id": str(invoice.invoice_id),
                    "tenant_id": str(invoice.tenant_id),
                    "period_id": str(invoice.period_id),
                    "issued_at": _at_midnight(invoice.issued_at),
                },
            },
            upsert=True,
        )

    def find_state(self, invoice_id: UUID) -> dict | None:
        document = self.collection.find_one(
            {"ns": self.ns, "invoice_id": str(invoice_id)}
        )
        if document is None:
            return None
        return {
            "status": document["status"],
            "subtotal": _as_decimal(document["subtotal"]),
            "tax": _as_decimal(document["tax"]),
            "total": _as_decimal(document["total"]),
        }


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
