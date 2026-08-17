from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
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
class InvoiceLine:
    line_no: int
    line_type: str
    description: str
    amount: Decimal
    tax_amount: Decimal
    credit_applied: Decimal
    total: Decimal


@dataclass(frozen=True)
class InvoicePreview:
    tenant_id: UUID
    period_start: date
    period_end: date
    lines: list[InvoiceLine]


@dataclass(frozen=True)
class CreditNoteRow:
    note_id: UUID
    tenant_id: UUID
    issued_on: date
    amount: Decimal
    remaining_amount: Decimal


@dataclass(frozen=True)
class RatedChargeRow:
    tenant_id: UUID
    period_start: date
    period_end: date
    overage_amount: Decimal


@dataclass(frozen=True)
class InvoiceRow:
    invoice_id: UUID
    tenant_id: UUID
    period_id: UUID
    issued_at: date
    status: str
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    lines: list[InvoiceLine]


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_money(value: Decimal) -> str:
    normalized = Decimal("0.00") if value == 0 else value
    return f"{normalized.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def deterministic_uuid(value: str) -> UUID:
    return UUID(bytes=md5(value.encode()).digest())


def invoice_ids(tenant_id: UUID, period_start: date) -> tuple[UUID, UUID]:
    period_id = deterministic_uuid(f"{tenant_id}{period_start.isoformat()}")
    return period_id, deterministic_uuid(f"{period_id}invoice")


def preview(
    tenant_id: UUID,
    period_start: date,
    period_end: date,
    plan: PlanRow,
    overage_amount: Decimal,
    tax_exempt: bool,
    credit_amount: Decimal,
) -> InvoicePreview:
    tax = Decimal("0") if tax_exempt else (plan.monthly_fee + overage_amount) * Decimal("0.0825")
    applied = min(credit_amount, money(plan.monthly_fee + overage_amount + tax))
    lines = [
        InvoiceLine(
            1,
            "plan",
            plan.code,
            money(plan.monthly_fee),
            Decimal("0"),
            Decimal("0"),
            money(plan.monthly_fee),
        ),
        InvoiceLine(
            2,
            "usage",
            "usage overage",
            money(overage_amount),
            Decimal("0"),
            Decimal("0"),
            money(overage_amount),
        ),
        InvoiceLine(3, "tax", "regional tax", tax / 2, Decimal("0"), Decimal("0"), tax / 2),
        InvoiceLine(4, "tax", "local tax", tax / 2, Decimal("0"), Decimal("0"), tax / 2),
        InvoiceLine(5, "credit", "credit notes", Decimal("0"), Decimal("0"), applied, -applied),
    ]
    return InvoicePreview(tenant_id, period_start, period_end, lines)


def invoice_totals(preview_result: InvoicePreview) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    subtotal = sum(
        (
            money(line.amount)
            for line in preview_result.lines
            if line.line_type in {"plan", "usage"}
        ),
        Decimal("0"),
    )
    tax = sum(
        (money(line.amount) for line in preview_result.lines if line.line_type == "tax"),
        Decimal("0"),
    )
    credit = next(
        line.credit_applied for line in preview_result.lines if line.line_type == "credit"
    )
    total = money(subtotal + tax - credit)
    if total < 0:
        raise ValueError("invoice total cannot be negative")
    return money(subtotal), money(tax), credit, total


def ordered_lines(lines: list[InvoiceLine]) -> list[InvoiceLine]:
    return sorted(lines, key=lambda line: line.line_no)


def line_amount_for_storage(line: InvoiceLine) -> Decimal:
    return line.total if line.line_type == "credit" else line.amount


def stored_line_amount(line: InvoiceLine) -> Decimal:
    return money(line_amount_for_storage(line))


def consume_credits(
    notes: list[CreditNoteRow], credit_applied: Decimal
) -> list[tuple[UUID, Decimal]]:
    outstanding = credit_applied
    updates: list[tuple[UUID, Decimal]] = []
    for note in sorted(notes, key=lambda item: (item.issued_on, item.note_id)):
        if outstanding <= 0:
            break
        remaining = max(note.remaining_amount - outstanding, Decimal("0"))
        updates.append((note.note_id, remaining))
        outstanding = max(outstanding - note.remaining_amount, Decimal("0"))
    return updates


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


def change_plan(
    repository: PlansRepository,
    tenant_id: UUID,
    plan_id: UUID,
    effective_on: date,
) -> tuple[list[SubscriptionRow], SubscriptionRow]:
    subscriptions = repository.list_subscriptions(tenant_id)
    for subscription in subscriptions:
        if subscription.ends_on is None and subscription.starts_on < effective_on:
            next_status = subscription.status if subscription.status == "cancelled" else "active"
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
