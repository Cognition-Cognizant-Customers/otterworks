"""Trust-score lookup for share requesters.

BRD section 4 (Credit Score Trigger — Test Data Note) requires that testers can
reproduce a specific score band from a fixed set of applicant details. The
designated QA profiles below pin each band deterministically; every other
profile falls back to a stable hash so repeated runs are reproducible.
"""

import hashlib
from dataclasses import dataclass

from app.services.share_decline_rules import MAX_TRUST_SCORE, MIN_TRUST_SCORE


@dataclass(frozen=True)
class RequesterProfile:
    first_name: str
    last_name: str
    date_of_birth: str
    address: str

    def key(self) -> tuple[str, str, str, str]:
        return (
            self.first_name.strip().casefold(),
            self.last_name.strip().casefold(),
            self.date_of_birth.strip(),
            self.address.strip().casefold(),
        )


# Designated QA test data — each profile reproduces one score band.
TEST_PROFILE_SCORES: dict[tuple[str, str, str, str], int] = {
    ("olive", "otter", "1985-03-14", "12 harbor st, boston, ma"): 545,
    ("river", "otter", "1979-11-02", "88 wharf ln, salem, ma"): 585,
    ("marina", "kelp", "1990-06-30", "4 tidal way, quincy, ma"): 589,
    ("clam", "digger", "1972-01-19", "301 shell rd, revere, ma"): 590,
    ("pearl", "diver", "1988-09-05", "17 cove ct, lynn, ma"): 720,
}


def resolve_trust_score(profile: RequesterProfile) -> int:
    """Return the trust score for a requester profile."""
    designated = TEST_PROFILE_SCORES.get(profile.key())
    if designated is not None:
        return designated

    digest = hashlib.sha256("|".join(profile.key()).encode()).digest()
    span = MAX_TRUST_SCORE - MIN_TRUST_SCORE + 1
    return MIN_TRUST_SCORE + int.from_bytes(digest[:4], "big") % span
