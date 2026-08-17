from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from audit_archive_recon import (  # noqa: E402
    epoch,
    iso,
    live_reference_time,
    seed_corpus,
)


def test_live_ttl_anchoring_preserves_split_and_boundaries() -> None:
    now = datetime(2026, 8, 17, 12, 34, 56, 789000, tzinfo=timezone.utc)
    reference = live_reference_time(now)
    records = seed_corpus("demo", "2026-01-15", reference)
    by_id = {record["event_id"]: record for record in records}
    reference_epoch = epoch(iso(reference))

    expirable = [
        record for record in records if "expires_at" in record
    ]
    assert len([record for record in expirable if record["expires_at"] < reference_epoch]) == 81
    assert len([record for record in expirable if record["expires_at"] >= reference_epoch]) == 22
    assert by_id["demo-boundary-0"]["expires_at"] == reference_epoch - 1
    assert by_id["demo-boundary-1"]["expires_at"] == reference_epoch
    assert by_id["demo-boundary-2"]["expires_at"] == reference_epoch + 1
    assert "expires_at" not in by_id["recon-unexpirable-0"]
