"""Trust-score based auto-decline rules for external share requests.

OtterWorks cast of the "Credit Score Based Auto-Decline Rules" BRD:
a quote becomes an external share request, the applicant's credit score becomes
the requester's trust score, and the declination notice becomes a
``share.declination_notice`` domain event consumed by the audit service.

See ``docs/brd/share-decline-rules.md`` for the field-by-field mapping.
"""

from dataclasses import dataclass
from enum import Enum

MIN_TRUST_SCORE = 300
MAX_TRUST_SCORE = 850


class RequestSource(str, Enum):
    """Channel the share request originated from (BRD: Source of Quote)."""

    CLIENT_PORTAL = "CLIENT_PORTAL"
    ADMIN_CONSOLE = "ADMIN_CONSOLE"


class WorkspaceType(str, Enum):
    """Workspace the document lives in (BRD: Line of Business)."""

    HOME_DRIVE = "HOME_DRIVE"
    TEAM_DRIVE = "TEAM_DRIVE"


class ShareType(str, Enum):
    """Kind of share being requested (BRD: Policy Type)."""

    PUBLIC_LINK = "PUBLIC_LINK"
    EXTERNAL_EMAIL = "EXTERNAL_EMAIL"
    INTERNAL_LINK = "INTERNAL_LINK"


class TransactionType(str, Enum):
    """Lifecycle stage of the request (BRD: Transaction)."""

    NEW_SHARE = "NEW_SHARE"
    RENEWAL = "RENEWAL"


class Outcome(str, Enum):
    ALLOWED = "ALLOWED"
    DECLINED = "DECLINED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class DeclineRule:
    """One row of the BRD declination-rule table.

    ``min_trust_score`` is exclusive: the request is declined when the
    requester's trust score is strictly below it.
    """

    rule_id: str
    region: str
    workspace_type: WorkspaceType
    share_type: ShareType
    transaction: TransactionType
    min_trust_score: int

    def matches_dimensions(self, request: "ShareRequest") -> bool:
        return (
            self.region == request.region
            and self.workspace_type == request.workspace_type
            and self.share_type == request.share_type
            and self.transaction == request.transaction
        )


# BRD section 3 — Declination Rules.
RULES: tuple[DeclineRule, ...] = (
    DeclineRule(
        rule_id="RULE-1",
        region="MA",
        workspace_type=WorkspaceType.HOME_DRIVE,
        share_type=ShareType.PUBLIC_LINK,
        transaction=TransactionType.NEW_SHARE,
        min_trust_score=590,
    ),
    DeclineRule(
        rule_id="RULE-2",
        region="MA",
        workspace_type=WorkspaceType.HOME_DRIVE,
        share_type=ShareType.EXTERNAL_EMAIL,
        transaction=TransactionType.NEW_SHARE,
        min_trust_score=580,
    ),
)


@dataclass(frozen=True)
class ShareRequest:
    source: RequestSource
    region: str
    workspace_type: WorkspaceType
    share_type: ShareType
    transaction: TransactionType
    trust_score: int


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    rule_id: str | None
    declination_notice: bool
    reason: str

    @property
    def is_declined(self) -> bool:
        return self.outcome is not Outcome.ALLOWED


def find_matching_rule(request: ShareRequest) -> DeclineRule | None:
    """Return the first rule whose criteria the request trips, if any."""
    for rule in RULES:
        if rule.matches_dimensions(request) and request.trust_score < rule.min_trust_score:
            return rule
    return None


def evaluate(request: ShareRequest) -> Decision:
    """Apply the declination rules — BRD section 5, Business Logic.

    * criteria met via the client portal -> declined, notice generated (5.1)
    * criteria not met via the client portal -> allowed, no notice (5.2)
    * criteria met via the admin console -> blocked, no notice (5.3)
    * criteria not met via the admin console -> allowed, no notice (5.4)
    """
    rule = find_matching_rule(request)
    if rule is None:
        return Decision(
            outcome=Outcome.ALLOWED,
            rule_id=None,
            declination_notice=False,
            reason="No declination rule criteria met",
        )

    threshold = f"trust score {request.trust_score} below {rule.min_trust_score}"
    if request.source is RequestSource.CLIENT_PORTAL:
        return Decision(
            outcome=Outcome.DECLINED,
            rule_id=rule.rule_id,
            declination_notice=True,
            reason=f"{rule.rule_id}: {threshold} — share declined in the client portal",
        )

    return Decision(
        outcome=Outcome.BLOCKED,
        rule_id=rule.rule_id,
        declination_notice=False,
        reason=f"{rule.rule_id}: {threshold} — share blocked in the admin console",
    )
