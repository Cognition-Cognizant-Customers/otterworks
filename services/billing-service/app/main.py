from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException, Path, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import settings
from app.db import connect, migrate, reset
from app.domain import catalog, change_plan, entitlement, format_money, preview
from app.repository import MongoInvoicingRepository, PostgresPlansRepository


@asynccontextmanager
async def lifespan(_app: FastAPI):
    migrate()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PlanChange(BaseModel):
    plan_id: UUID
    effective_on: date


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with connect() as connection:
            connection.execute("SELECT 1")
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="database unavailable") from error
    mongo_status = "reachable" if MongoInvoicingRepository().ping() else "unreachable"
    return {"status": "healthy", "service": settings.app_name, "mongo": mongo_status}


@app.post("/internal/reset", status_code=204)
def internal_reset() -> Response:
    if not settings.allow_internal_reset:
        raise HTTPException(status_code=404, detail="internal reset is disabled")
    reset()
    return Response(status_code=204)


@app.get("/api/tenants/{tenant_id}/invoice-preview")
def invoice_preview(
    tenant_id: Annotated[UUID, Path()],
    period_start: Annotated[date, Query()],
    period_end: Annotated[date, Query()],
) -> dict:
    try:
        with connect() as connection:
            plan, tax_exempt = PostgresPlansRepository(connection).invoice_context(
                tenant_id, period_start, period_end
            )
        mongo = MongoInvoicingRepository()
        rated = mongo.rated_charge(tenant_id, period_start)
        credit = sum(
            (note.remaining_amount for note in mongo.credit_notes(tenant_id, positive_only=True)),
            Decimal("0"),
        )
        result = preview(
            tenant_id, period_start, period_end, plan, rated.overage_amount, tax_exempt, credit
        )
    except LookupError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "tenant_id": str(tenant_id),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "lines": [
            {
                "line_no": line.line_no,
                "line_type": line.line_type,
                "description": line.description,
                "amount": format_money(line.amount),
                "tax_amount": format_money(line.tax_amount),
                "credit_applied": format_money(line.credit_applied),
                "total": format_money(line.total),
            }
            for line in result.lines
        ],
    }


@app.get("/api/invoices/{invoice_id}/lines")
def get_invoice_lines(invoice_id: Annotated[UUID, Path()]) -> dict:
    return {
        "invoice_id": str(invoice_id),
        "lines": [
            {
                "line_no": line.line_no,
                "line_type": line.line_type,
                "description": line.description,
                "amount": format_money(line.amount),
            }
            for line in MongoInvoicingRepository().invoice_lines(invoice_id)
        ],
    }


class InvoiceIssue(BaseModel):
    period_start: date
    period_end: date


@app.post("/api/tenants/{tenant_id}/invoices")
def issue_invoice(tenant_id: Annotated[UUID, Path()], request: InvoiceIssue) -> dict:
    try:
        with connect() as connection:
            plan, tax_exempt = PostgresPlansRepository(connection).invoice_context(
                tenant_id, request.period_start, request.period_end
            )
        mongo = MongoInvoicingRepository()
        invoice = mongo.issue(plan, tax_exempt, tenant_id, request.period_start, request.period_end)
    except (LookupError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    notes = mongo.credit_notes(tenant_id)
    return {
        "invoice": {
            "invoice_id": str(invoice.invoice_id),
            "tenant_id": str(invoice.tenant_id),
            "period_id": str(invoice.period_id),
            "issued_at": invoice.issued_at.isoformat(),
            "status": invoice.status,
            "subtotal": format_money(invoice.subtotal),
            "tax": format_money(invoice.tax),
            "total": format_money(invoice.total),
            "lines": [
                {
                    "line_no": line.line_no,
                    "line_type": line.line_type,
                    "description": line.description,
                    "amount": format_money(
                        -line.total if line.line_type == "credit" else line.amount
                    ),
                }
                for line in invoice.lines
            ],
        },
        "credit_notes": [
            {
                "id": str(note.note_id),
                "issued_on": note.issued_on.isoformat(),
                "amount": format_money(note.amount),
                "remaining_amount": format_money(note.remaining_amount),
            }
            for note in notes
        ],
        "invoice_state": [mongo.invoice_state(invoice.invoice_id)],
    }


@app.get("/api/plans")
def list_plans() -> list[dict]:
    with connect() as connection:
        plans = catalog(PostgresPlansRepository(connection).list_plans())
    return [
        {
            "plan_id": str(plan.plan_id),
            "code": plan.code,
            "tier": plan.tier,
            "monthly_fee": f"{plan.monthly_fee:.2f}",
            "included_units": plan.included_units,
            "overage_rate": f"{plan.overage_rate:.6f}",
        }
        for plan in plans
    ]


@app.get("/api/tenants/{tenant_id}/entitlement")
def get_entitlement(
    tenant_id: Annotated[UUID, Path()],
    on: Annotated[date, Query()],
) -> dict:
    with connect() as connection:
        row = entitlement(
            PostgresPlansRepository(connection).find_entitlements(tenant_id),
            tenant_id,
            on,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="entitlement not found")
    return {
        "tenant_id": str(row.tenant_id),
        "plan_code": row.plan_code,
        "tier": row.tier,
        "monthly_fee": f"{row.monthly_fee:.2f}",
        "included_units": row.included_units,
        "subscription_status": row.subscription_status,
        "effective_on": max(row.starts_on, on).isoformat(),
    }


@app.post("/api/tenants/{tenant_id}/plan-change")
def change_tenant_plan(tenant_id: Annotated[UUID, Path()], request: PlanChange) -> dict:
    try:
        with connect() as connection:
            repository = PostgresPlansRepository(connection)
            subscriptions, created = change_plan(
                repository,
                tenant_id,
                request.plan_id,
                request.effective_on,
            )
            return {
                "latest_plan": str(created.plan_id),
                "latest_start": created.starts_on.isoformat(),
                "subscriptions": [
                    {
                        "plan_id": str(item.plan_id),
                        "starts_on": item.starts_on.isoformat(),
                        "ends_on": item.ends_on.isoformat() if item.ends_on else None,
                        "status": item.status,
                    }
                    for item in subscriptions
                ],
            }
    except psycopg.errors.ForeignKeyViolation as error:
        raise HTTPException(status_code=400, detail="invalid plan change") from error
    except psycopg.errors.UniqueViolation as error:
        raise HTTPException(
            status_code=409,
            detail="this plan change has already been requested",
        ) from error
