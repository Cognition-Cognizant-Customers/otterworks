"""Deterministic document-store seed for the billing-service fixture.

Mirrors services/legacy-billing/db/seed.sql as the two migrated MongoDB
collections (`customers` with embedded subscriptions/usage/rating/credit
state, `invoices` with embedded lines), following the wave-1 migration
conventions: string `_id`, an `ns` field, BSON Decimal128 money, and BSON
dates. Every document written by this service carries
`origin: "billing_svc"`, so a reset removes exactly the fixture-managed
documents and never touches migrated documents sharing the collections.
"""

from __future__ import annotations

from datetime import UTC, datetime

from bson.decimal128 import Decimal128
from pymongo.database import Database

ORIGIN = "billing_svc"

CUSTOMERS_COLLECTION = "customers"
INVOICES_COLLECTION = "invoices"


def _d(value: str) -> Decimal128:
    return Decimal128(value)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _date(value: str) -> datetime:
    return datetime.fromisoformat(value + "T00:00:00+00:00")


_PLANS = {
    "10000000-0000-0000-0000-000000000001": {
        "plan_id": "10000000-0000-0000-0000-000000000001",
        "code": "STARTER",
        "tier": "starter",
        "monthly_fee": _d("49.00"),
        "included_units": 100,
        "overage_rate": _d("0.055000"),
        "active": True,
    },
    "10000000-0000-0000-0000-000000000002": {
        "plan_id": "10000000-0000-0000-0000-000000000002",
        "code": "GROWTH",
        "tier": "growth",
        "monthly_fee": _d("149.00"),
        "included_units": 500,
        "overage_rate": _d("0.035000"),
        "active": True,
    },
    "10000000-0000-0000-0000-000000000003": {
        "plan_id": "10000000-0000-0000-0000-000000000003",
        "code": "SCALE",
        "tier": "scale",
        "monthly_fee": _d("499.00"),
        "included_units": 2000,
        "overage_rate": _d("0.020000"),
        "active": True,
    },
}

_TENANTS = [
    ("00000000-0000-0000-0000-000000000001", "Tenant One", False, "active"),
    ("00000000-0000-0000-0000-000000000002", "Tenant Two", False, "suspended"),
    ("00000000-0000-0000-0000-000000000003", "Tenant Three", True, "active"),
    ("00000000-0000-0000-0000-000000000004", "Tenant Four", False, "active"),
    ("00000000-0000-0000-0000-000000000005", "Tenant Five", False, "active"),
    ("00000000-0000-0000-0000-000000000006", "Tenant Six", False, "active"),
    ("00000000-0000-0000-0000-000000000007", "Tenant Seven", False, "active"),
    ("00000000-0000-0000-0000-000000000008", "Tenant Eight", False, "active"),
    ("00000000-0000-0000-0000-000000000009", "Tenant Nine", False, "active"),
]

# subscription_id, tenant_id, plan_id, starts_on, ends_on, status, suspended_on
_SUBSCRIPTIONS = [
    ("20000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000001",
     "10000000-0000-0000-0000-000000000001", "2026-01-01", None, "active", None),
    ("20000000-0000-0000-0000-000000000002", "00000000-0000-0000-0000-000000000002",
     "10000000-0000-0000-0000-000000000002", "2026-01-01", None, "suspended", "2026-02-15"),
    ("20000000-0000-0000-0000-000000000003", "00000000-0000-0000-0000-000000000003",
     "10000000-0000-0000-0000-000000000003", "2026-01-01", None, "active", None),
    ("20000000-0000-0000-0000-000000000004", "00000000-0000-0000-0000-000000000004",
     "10000000-0000-0000-0000-000000000001", "2026-01-01", None, "active", None),
    ("20000000-0000-0000-0000-000000000005", "00000000-0000-0000-0000-000000000005",
     "10000000-0000-0000-0000-000000000002", "2026-01-01", None, "active", None),
    ("20000000-0000-0000-0000-000000000006", "00000000-0000-0000-0000-000000000006",
     "10000000-0000-0000-0000-000000000001", "2026-01-01", None, "active", None),
    ("20000000-0000-0000-0000-000000000007", "00000000-0000-0000-0000-000000000007",
     "10000000-0000-0000-0000-000000000001", "2026-01-01", None, "active", None),
    ("20000000-0000-0000-0000-000000000008", "00000000-0000-0000-0000-000000000008",
     "10000000-0000-0000-0000-000000000001", "2026-01-01", None, "active", None),
    ("20000000-0000-0000-0000-000000000009", "00000000-0000-0000-0000-000000000009",
     "10000000-0000-0000-0000-000000000001", "2026-01-01", None, "active", None),
]

# event_id, tenant_id, occurred_at, units, kind
_USAGE_EVENTS = [
    ("30000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000001",
     "2026-02-10T10:00:00Z", 260, "api"),
    ("30000000-0000-0000-0000-000000000003", "00000000-0000-0000-0000-000000000002",
     "2026-02-10T10:00:00Z", 700, "api"),
    ("30000000-0000-0000-0000-000000000004", "00000000-0000-0000-0000-000000000003",
     "2026-02-10T10:00:00Z", 2201, "compute"),
    ("30000000-0000-0000-0000-000000000005", "00000000-0000-0000-0000-000000000004",
     "2026-02-05T10:00:00Z", 20, "api"),
    ("30000000-0000-0000-0000-000000000008", "00000000-0000-0000-0000-000000000004",
     "2026-02-06T10:00:00Z", 30, "storage"),
    ("30000000-0000-0000-0000-000000000006", "00000000-0000-0000-0000-000000000005",
     "2026-02-01T10:00:00Z", 610, "api"),
    ("30000000-0000-0000-0000-000000000007", "00000000-0000-0000-0000-000000000006",
     "2026-02-28T10:00:00Z", 201, "api"),
    ("30000000-0000-0000-0000-000000000009", "00000000-0000-0000-0000-000000000007",
     "2026-02-10T10:00:00Z", 260, "api"),
    ("30000000-0000-0000-0000-000000000010", "00000000-0000-0000-0000-000000000008",
     "2026-02-28T10:00:00Z", 202, "api"),
    ("30000000-0000-0000-0000-000000000011", "00000000-0000-0000-0000-000000000009",
     "2026-02-10T10:00:00Z", 1, "api"),
]

# period_id, tenant_id, period_start, period_end
_RATING_PERIODS = [
    ("40000000-0000-0000-0000-000000000003", "00000000-0000-0000-0000-000000000001",
     "2025-11-01", "2025-11-30"),
    ("40000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000001",
     "2025-12-01", "2025-12-31"),
    ("40000000-0000-0000-0000-000000000002", "00000000-0000-0000-0000-000000000001",
     "2026-01-01", "2026-01-31"),
]

# result_id, period_id, subscription_id, used, quota, rollover, billable, overage, created_at
_RATING_RESULTS = [
    ("50000000-0000-0000-0000-000000000003", "40000000-0000-0000-0000-000000000003",
     "20000000-0000-0000-0000-000000000001", 0, 100, 100, 0, "0.00", "2025-11-30T00:00:00Z"),
    ("50000000-0000-0000-0000-000000000001", "40000000-0000-0000-0000-000000000001",
     "20000000-0000-0000-0000-000000000001", 0, 100, 100, 0, "0.00", "2025-12-31T00:00:00Z"),
    ("50000000-0000-0000-0000-000000000002", "40000000-0000-0000-0000-000000000002",
     "20000000-0000-0000-0000-000000000001", 0, 100, 100, 0, "0.00", "2026-01-31T00:00:00Z"),
]

# credit_note_id, tenant_id, issued_on, amount, remaining_amount
_CREDIT_NOTES = [
    ("70000000-0000-0000-0000-000000000002", "00000000-0000-0000-0000-000000000004",
     "2026-02-01", "30.00", "30.00"),
    ("70000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000004",
     "2026-02-01", "30.00", "30.00"),
    ("70000000-0000-0000-0000-000000000004", "00000000-0000-0000-0000-000000000003",
     "2026-02-02", "25.00", "25.00"),
    ("70000000-0000-0000-0000-000000000005", "00000000-0000-0000-0000-000000000009",
     "2026-01-31", "5.00", "5.00"),
    ("70000000-0000-0000-0000-000000000006", "00000000-0000-0000-0000-000000000009",
     "2026-02-01", "55.00", "55.00"),
]

# invoice_id, tenant_id, period_id, issued_at, subtotal, tax, total, status, lines
_INVOICES = [
    ("60000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002",
     "40000000-0000-0000-0000-000000000001", "2026-02-01T00:00:00Z",
     "149.00", "12.29", "161.29", "overdue",
     [
         {"line_id": "a0000000-0000-0000-0000-000000000001", "line_no": 1,
          "line_type": "plan", "description": "GROWTH", "amount": _d("149.00")},
         {"line_id": "a0000000-0000-0000-0000-000000000002", "line_no": 2,
          "line_type": "usage", "description": "usage overage", "amount": _d("12.29")},
     ]),
    ("60000000-0000-0000-0000-000000000002", "00000000-0000-0000-0000-000000000005",
     "40000000-0000-0000-0000-000000000001", "2026-02-13T00:00:00Z",
     "149.00", "12.29", "161.29", "overdue", []),
    ("60000000-0000-0000-0000-000000000003", "00000000-0000-0000-0000-000000000006",
     "40000000-0000-0000-0000-000000000002", "2026-02-28T00:00:00Z",
     "49.00", "4.04", "53.04", "issued", []),
]


def customer_documents(ns: str) -> list[dict]:
    documents = []
    for tenant_id, name, tax_exempt, status in _TENANTS:
        subscriptions = []
        for sub in _SUBSCRIPTIONS:
            if sub[1] != tenant_id:
                continue
            subscriptions.append(
                {
                    "subscription_id": sub[0],
                    "plan": dict(_PLANS[sub[2]]),
                    "starts_on": _date(sub[3]),
                    "ends_on": _date(sub[4]) if sub[4] else None,
                    "status": sub[5],
                    "suspended_on": _date(sub[6]) if sub[6] else None,
                }
            )
        usage_events = [
            {
                "event_id": event[0],
                "occurred_at": _dt(event[2]),
                "units": event[3],
                "kind": event[4],
            }
            for event in _USAGE_EVENTS
            if event[1] == tenant_id
        ]
        periods = {p[0]: p for p in _RATING_PERIODS if p[1] == tenant_id}
        rating_history = [
            {
                "result_id": result[0],
                "period_id": result[1],
                "period_start": _date(periods[result[1]][2]),
                "period_end": _date(periods[result[1]][3]),
                "subscription_id": result[2],
                "used_units": result[3],
                "quota_units": result[4],
                "rollover_units": result[5],
                "billable_units": result[6],
                "overage_amount": _d(result[7]),
                "created_at": _dt(result[8]),
            }
            for result in _RATING_RESULTS
            if result[1] in periods
        ]
        credit_notes = [
            {
                "credit_note_id": note[0],
                "issued_on": _date(note[2]),
                "amount": _d(note[3]),
                "remaining_amount": _d(note[4]),
            }
            for note in _CREDIT_NOTES
            if note[1] == tenant_id
        ]
        documents.append(
            {
                "_id": tenant_id,
                "ns": ns,
                "origin": ORIGIN,
                "name": name,
                "tax_exempt": tax_exempt,
                "status": status,
                "subscriptions": subscriptions,
                "usage_events": usage_events,
                "rating_history": rating_history,
                "credit_notes": credit_notes,
            }
        )
    return documents


def invoice_documents(ns: str) -> list[dict]:
    return [
        {
            "_id": invoice_id,
            "ns": ns,
            "origin": ORIGIN,
            "tenant_id": tenant_id,
            "period_id": period_id,
            "issued_at": _dt(issued_at),
            "subtotal": _d(subtotal),
            "tax": _d(tax),
            "total": _d(total),
            "status": status,
            "lines": lines,
        }
        for invoice_id, tenant_id, period_id, issued_at,
        subtotal, tax, total, status, lines in _INVOICES
    ]


def seed(database: Database, ns: str) -> None:
    """Reset the fixture-managed slice of the document store for one namespace.

    Removes only documents this service wrote (`origin == "billing_svc"`,
    matching `ns`), then reinserts the deterministic seed. Documents from the
    wave-1 data migration are never matched and never touched.
    """
    for collection_name, documents in (
        (CUSTOMERS_COLLECTION, customer_documents(ns)),
        (INVOICES_COLLECTION, invoice_documents(ns)),
    ):
        collection = database[collection_name]
        collection.delete_many({"ns": ns, "origin": ORIGIN})
        collection.insert_many(documents)
