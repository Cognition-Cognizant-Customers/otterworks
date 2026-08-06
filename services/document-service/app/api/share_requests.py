"""Share-request declination API endpoints."""

import structlog
from fastapi import APIRouter, HTTPException, status

from app.schemas.share_request import ShareDecisionResponse, ShareRequestCreate
from app.services.event_publisher import event_publisher
from app.services.share_decline_rules import ShareRequest, evaluate
from app.services.trust_score import RequesterProfile, resolve_trust_score

logger = structlog.get_logger()
router = APIRouter()

DECLINATION_NOTICE_EVENT = "share.declination_notice"


def _trust_score_for(body: ShareRequestCreate) -> int:
    """Use the caller-supplied score, else derive it from the requester details."""
    if body.trust_score is not None:
        return body.trust_score

    requester = body.requester
    if requester is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="provide either requester details or an explicit trust_score",
        )

    return resolve_trust_score(
        RequesterProfile(
            first_name=requester.first_name,
            last_name=requester.last_name,
            date_of_birth=requester.date_of_birth,
            address=requester.address,
        )
    )


@router.post("/", response_model=ShareDecisionResponse)
async def evaluate_share_request(body: ShareRequestCreate) -> ShareDecisionResponse:
    """Evaluate a share request against the auto-decline rules.

    A declined request (client portal only) emits a declination notice event
    for the audit service; blocked and allowed requests emit nothing.
    """
    trust_score = _trust_score_for(body)
    decision = evaluate(
        ShareRequest(
            source=body.source,
            region=body.region,
            workspace_type=body.workspace_type,
            share_type=body.share_type,
            transaction=body.transaction,
            trust_score=trust_score,
        )
    )

    if decision.declination_notice:
        await event_publisher.publish(
            DECLINATION_NOTICE_EVENT,
            {
                "document_id": body.document_id,
                "source": body.source.value,
                "rule_id": decision.rule_id,
                "trust_score": trust_score,
                "reason": decision.reason,
            },
        )

    logger.info(
        "share_request_evaluated",
        document_id=str(body.document_id),
        outcome=decision.outcome.value,
        rule_id=decision.rule_id,
        declination_notice=decision.declination_notice,
    )

    return ShareDecisionResponse(
        document_id=body.document_id,
        outcome=decision.outcome,
        rule_id=decision.rule_id,
        trust_score=trust_score,
        declination_notice_sent=decision.declination_notice,
        reason=decision.reason,
    )


@router.post("", response_model=ShareDecisionResponse, include_in_schema=False)
async def evaluate_share_request_no_slash(body: ShareRequestCreate) -> ShareDecisionResponse:
    """Evaluate a share request (no trailing slash)."""
    return await evaluate_share_request(body)
