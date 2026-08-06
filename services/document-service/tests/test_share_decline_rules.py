"""Rule-level tests for the share-request auto-decline rules (BRD sections 3-5)."""

import pytest

from app.services.share_decline_rules import (
    RULES,
    Decision,
    Outcome,
    RequestSource,
    ShareRequest,
    ShareType,
    TransactionType,
    WorkspaceType,
    evaluate,
    find_matching_rule,
)
from app.services.trust_score import (
    TEST_PROFILE_SCORES,
    RequesterProfile,
    resolve_trust_score,
)


def make_request(
    *,
    source: RequestSource = RequestSource.CLIENT_PORTAL,
    region: str = "MA",
    workspace_type: WorkspaceType = WorkspaceType.HOME_DRIVE,
    share_type: ShareType = ShareType.PUBLIC_LINK,
    transaction: TransactionType = TransactionType.NEW_SHARE,
    trust_score: int = 500,
) -> ShareRequest:
    return ShareRequest(
        source=source,
        region=region,
        workspace_type=workspace_type,
        share_type=share_type,
        transaction=transaction,
        trust_score=trust_score,
    )


# --- BRD section 3: the rule table itself ---


def test_rule_table_matches_brd():
    assert [(r.rule_id, r.share_type, r.min_trust_score) for r in RULES] == [
        ("RULE-1", ShareType.PUBLIC_LINK, 590),
        ("RULE-2", ShareType.EXTERNAL_EMAIL, 580),
    ]
    assert all(
        r.region == "MA"
        and r.workspace_type is WorkspaceType.HOME_DRIVE
        and r.transaction is TransactionType.NEW_SHARE
        for r in RULES
    )


# --- Threshold boundaries: the score condition is strictly "below" ---


@pytest.mark.parametrize(
    ("share_type", "trust_score", "expected_rule"),
    [
        (ShareType.PUBLIC_LINK, 300, "RULE-1"),
        (ShareType.PUBLIC_LINK, 589, "RULE-1"),
        (ShareType.PUBLIC_LINK, 590, None),
        (ShareType.PUBLIC_LINK, 591, None),
        (ShareType.PUBLIC_LINK, 850, None),
        (ShareType.EXTERNAL_EMAIL, 300, "RULE-2"),
        (ShareType.EXTERNAL_EMAIL, 579, "RULE-2"),
        (ShareType.EXTERNAL_EMAIL, 580, None),
        (ShareType.EXTERNAL_EMAIL, 581, None),
        # A score between the two thresholds trips RULE-1 only.
        (ShareType.PUBLIC_LINK, 585, "RULE-1"),
        (ShareType.EXTERNAL_EMAIL, 585, None),
    ],
)
def test_threshold_boundaries(share_type, trust_score, expected_rule):
    rule = find_matching_rule(make_request(share_type=share_type, trust_score=trust_score))
    assert (rule.rule_id if rule else None) == expected_rule


# --- Dimension matching: every criterion must match for a rule to apply ---


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("region", "NY"),
        ("workspace_type", WorkspaceType.TEAM_DRIVE),
        ("share_type", ShareType.INTERNAL_LINK),
        ("transaction", TransactionType.RENEWAL),
    ],
)
def test_out_of_scope_dimension_is_not_declined(field, value):
    request = make_request(trust_score=350, **{field: value})
    assert find_matching_rule(request) is None
    assert evaluate(request).outcome is Outcome.ALLOWED


def test_in_scope_low_score_is_declined():
    assert evaluate(make_request(trust_score=350)).outcome is Outcome.DECLINED


# --- BRD section 5: the four behaviour quadrants ---


@pytest.mark.parametrize(
    ("brd_section", "source", "trust_score", "expected"),
    [
        (
            "5.1",
            RequestSource.CLIENT_PORTAL,
            500,
            Decision(Outcome.DECLINED, "RULE-1", True, ""),
        ),
        (
            "5.2",
            RequestSource.CLIENT_PORTAL,
            700,
            Decision(Outcome.ALLOWED, None, False, ""),
        ),
        (
            "5.3",
            RequestSource.ADMIN_CONSOLE,
            500,
            Decision(Outcome.BLOCKED, "RULE-1", False, ""),
        ),
        (
            "5.4",
            RequestSource.ADMIN_CONSOLE,
            700,
            Decision(Outcome.ALLOWED, None, False, ""),
        ),
    ],
)
def test_behaviour_quadrants(brd_section, source, trust_score, expected):
    decision = evaluate(make_request(source=source, trust_score=trust_score))
    assert decision.outcome is expected.outcome, brd_section
    assert decision.rule_id == expected.rule_id, brd_section
    assert decision.declination_notice is expected.declination_notice, brd_section


def test_declination_reason_names_the_rule_and_score():
    decision = evaluate(make_request(trust_score=412))
    assert "RULE-1" in decision.reason
    assert "412" in decision.reason
    assert "590" in decision.reason


def test_allowed_decision_is_not_declined():
    assert evaluate(make_request(trust_score=800)).is_declined is False
    assert evaluate(make_request(trust_score=400)).is_declined is True


# --- BRD section 4: reproducible trust-score test data ---


@pytest.mark.parametrize(("profile_key", "expected_score"), list(TEST_PROFILE_SCORES.items()))
def test_designated_test_profiles_pin_their_band(profile_key, expected_score):
    first, last, dob, address = profile_key
    assert resolve_trust_score(RequesterProfile(first, last, dob, address)) == expected_score


def test_profile_lookup_ignores_case_and_padding():
    assert (
        resolve_trust_score(
            RequesterProfile("  Olive ", "OTTER", "1985-03-14", "12 Harbor St, Boston, MA ")
        )
        == 545
    )


def test_unknown_profile_scores_are_deterministic_and_in_range():
    profile = RequesterProfile("Unlisted", "Requester", "2000-01-01", "1 Nowhere Rd")
    score = resolve_trust_score(profile)
    assert score == resolve_trust_score(profile)
    assert 300 <= score <= 850
