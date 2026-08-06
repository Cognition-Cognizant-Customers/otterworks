"""Pydantic schemas for share-request declination decisions."""

from uuid import UUID

from pydantic import BaseModel, Field

from app.services.share_decline_rules import (
    MAX_TRUST_SCORE,
    MIN_TRUST_SCORE,
    Outcome,
    RequestSource,
    ShareType,
    TransactionType,
    WorkspaceType,
)


class RequesterProfileIn(BaseModel):
    """Applicant details that drive the trust-score lookup (BRD section 4)."""

    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    date_of_birth: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    address: str = Field(..., min_length=1, max_length=500)


class ShareRequestCreate(BaseModel):
    document_id: UUID
    source: RequestSource
    region: str = Field(..., min_length=2, max_length=10)
    workspace_type: WorkspaceType
    share_type: ShareType
    transaction: TransactionType
    requester: RequesterProfileIn | None = None
    trust_score: int | None = Field(None, ge=MIN_TRUST_SCORE, le=MAX_TRUST_SCORE)


class ShareDecisionResponse(BaseModel):
    document_id: UUID
    outcome: Outcome
    rule_id: str | None
    trust_score: int
    declination_notice_sent: bool
    reason: str
