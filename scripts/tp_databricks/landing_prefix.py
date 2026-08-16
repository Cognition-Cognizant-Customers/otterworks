"""Shared landing-prefix validation for extraction and ingestion."""

from __future__ import annotations

import re

LANDING_PREFIX_PATTERN = r"[a-z0-9_-]+(/[a-z0-9_-]+)*"


def normalize_landing_prefix(raw: str) -> str:
    value = raw.strip()
    if not re.fullmatch(LANDING_PREFIX_PATTERN, value):
        raise ValueError(
            "landing_prefix must match [a-z0-9_-]+(/[a-z0-9_-]+)*, "
            f"got {raw!r}"
        )
    return value
