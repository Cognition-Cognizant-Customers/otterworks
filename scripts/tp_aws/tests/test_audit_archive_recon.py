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
    wait_for_ttl_probe,
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


class StubProbeTarget:
    def __init__(self, archived: list[str], present: list[str]) -> None:
        self.archived = archived
        self.present = present

    def archive_objects(self) -> list[str]:
        return [f"audit-archive/expired/{event_id}__suffix" for event_id in self.archived]

    def scan_ids(self) -> list[str]:
        return self.present


def probe_records() -> list[dict]:
    return [{"event_id": "demo-probe-ascii"}, {"event_id": "demo-probe-unicode"}]


def test_ttl_probe_passes_when_all_removed_records_are_archived() -> None:
    records = probe_records()
    result = wait_for_ttl_probe(
        StubProbeTarget(
            ["demo-probe-ascii", "demo-probe-unicode"],
            [],
        ),
        records,
        timeout_seconds=0,
    )
    assert result == {
        "result": "pass",
        "archived_objects": ["demo-probe-ascii", "demo-probe-unicode"],
        "absent_from_table": ["demo-probe-ascii", "demo-probe-unicode"],
    }


def test_ttl_probe_skips_when_a_probe_remains_in_the_table() -> None:
    records = probe_records()
    result = wait_for_ttl_probe(
        StubProbeTarget(
            ["demo-probe-unicode"],
            ["demo-probe-ascii"],
        ),
        records,
        timeout_seconds=0,
    )
    assert result["result"] == "skipped"
    assert result["archived_objects"] == ["demo-probe-unicode"]
    assert result["absent_from_table"] == ["demo-probe-unicode"]


def test_ttl_probe_fails_when_all_removed_probes_are_not_archived() -> None:
    records = probe_records()
    result = wait_for_ttl_probe(
        StubProbeTarget(
            ["demo-probe-unicode"],
            [],
        ),
        records,
        timeout_seconds=0,
    )
    assert result["result"] == "fail"
    assert result["archived_objects"] == ["demo-probe-unicode"]
    assert result["absent_from_table"] == ["demo-probe-ascii", "demo-probe-unicode"]
