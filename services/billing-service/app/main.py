from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from datetime import date
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException, Path, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.config import settings
from app.db import connect, ensure_rating_indexes, migrate, mongo_client, mongo_database, reset
from app.domain import (
    calculate_rating,
    catalog,
    change_plan,
    entitlement,
    finalize_result,
    usage_summary,
)
from app.repository import MongoRatingRepository, PostgresPlansRepository


@asynccontextmanager
async def lifespan(_app: FastAPI):
    migrate()
    with suppress(PyMongoError):
        ensure_rating_indexes()
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
    try:
        mongo_client().admin.command("ping")
    except PyMongoError as error:
        raise HTTPException(status_code=503, detail="mongo unavailable") from error
    return {"status": "healthy", "service": settings.app_name}


@app.post("/internal/reset", status_code=204)
def internal_reset() -> Response:
    if not settings.allow_internal_reset:
        raise HTTPException(status_code=404, detail="internal reset is disabled")
    reset()
    return Response(status_code=204)


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


def _rating(tenant_id: UUID, period_start: date, period_end: date):
    with connect() as connection:
        plans_repository = PostgresPlansRepository(connection)
        subscriptions = plans_repository.list_subscriptions(tenant_id)
        plans = plans_repository.list_plans()
    rating_repository = MongoRatingRepository(mongo_database())
    rating = calculate_rating(
        subscriptions,
        plans,
        rating_repository.list_usage(tenant_id, period_start, period_end),
        rating_repository.list_periods(tenant_id),
        tenant_id,
        period_start,
        period_end,
    )
    return rating, rating_repository


def _rating_payload(tenant_id: UUID, period_start: date, period_end: date, rating) -> dict:
    return {
        "tenant_id": str(tenant_id),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "used_units": rating.used_units,
        "quota_units": rating.quota_units,
        "rollover_units": rating.rollover_units,
        "billable_units": rating.billable_units,
        "first_tier_units": rating.first_tier_units,
        "second_tier_units": rating.second_tier_units,
        "overage_amount": (
            f"{rating.overage_amount:.2f}" if rating.overage_amount is not None else None
        ),
    }


@app.get("/api/tenants/{tenant_id}/rating")
def get_rating(
    tenant_id: Annotated[UUID, Path()],
    period_start: Annotated[date, Query()],
    period_end: Annotated[date, Query()],
) -> dict:
    rating, _ = _rating(tenant_id, period_start, period_end)
    return _rating_payload(tenant_id, period_start, period_end, rating)


@app.get("/api/tenants/{tenant_id}/usage-summary")
def get_usage_summary(
    tenant_id: Annotated[UUID, Path()],
    period_start: Annotated[date, Query()],
    period_end: Annotated[date, Query()],
) -> list[dict]:
    repository = MongoRatingRepository(mongo_database())
    events = repository.list_usage(tenant_id, period_start, period_end)
    return usage_summary(events, tenant_id, period_start, period_end)


class RatingFinalize(BaseModel):
    period_start: date
    period_end: date


@app.post("/api/tenants/{tenant_id}/rating-finalize")
def finalize_rating(tenant_id: Annotated[UUID, Path()], request: RatingFinalize) -> dict:
    rating, repository = _rating(tenant_id, request.period_start, request.period_end)
    period_id, result = finalize_result(
        rating,
        tenant_id,
        request.period_start,
        request.period_end,
    )
    try:
        persisted = repository.upsert_rating(
            period_id, tenant_id, request.period_start, request.period_end, result
        )
    except DuplicateKeyError as error:
        raise HTTPException(
            status_code=409,
            detail="rating period identity conflicts with an existing period",
        ) from error
    persisted_result = persisted.result
    row = {
        "used_units": persisted_result.used_units,
        "quota_units": persisted_result.quota_units,
        "rollover_units": persisted_result.rollover_units,
        "billable_units": persisted_result.billable_units,
        "overage_amount": (
            f"{persisted_result.overage_amount:.2f}"
            if persisted_result.overage_amount is not None
            else None
        ),
    }
    return {**row, "rating_result": [row]}
