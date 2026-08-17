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
    archive_key,
    delete_probe_objects,
    wait_for_ttl_probe,
)
import audit_archive_recon as recon  # noqa: E402

LAMBDA = Path(__file__).resolve().parents[3] / "infrastructure/terraform/tp-cronbox/lambda/audit_archive"
sys.path.insert(0, str(LAMBDA))
import handler as archive_handler  # noqa: E402


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
    def __init__(self, archived: list[str], presence: list[list[str]]) -> None:
        self.archived = archived
        self.presence = presence
        self.probe_calls = 0

    def archive_objects(self) -> list[str]:
        return [f"audit-archive/expired/{event_id}__suffix" for event_id in self.archived]

    def probe_present(self, records: list[dict]) -> set[str]:
        del records
        present = self.presence[min(self.probe_calls, len(self.presence) - 1)]
        self.probe_calls += 1
        return set(present)


def fake_clock():
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    return clock, sleep


def probe_records() -> list[dict]:
    return [{"event_id": "demo-probe-ascii"}, {"event_id": "demo-probe-unicode"}]


def test_ttl_probe_passes_when_all_removed_records_are_archived() -> None:
    records = probe_records()
    clock, sleeper = fake_clock()
    result = wait_for_ttl_probe(
        StubProbeTarget(
            ["demo-probe-ascii", "demo-probe-unicode"],
            [["demo-probe-ascii", "demo-probe-unicode"], []],
        ),
        records,
        timeout_seconds=1,
        interval_seconds=1,
        clock=clock,
        sleeper=sleeper,
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
            [["demo-probe-ascii"], ["demo-probe-ascii"]],
        ),
        records,
        timeout_seconds=0,
    )
    assert result["result"] == "skipped"
    assert result["archived_objects"] == ["demo-probe-unicode"]
    assert result["absent_from_table"] == ["demo-probe-unicode"]


def test_ttl_probe_fails_when_all_removed_probes_are_not_archived() -> None:
    records = probe_records()
    clock, sleeper = fake_clock()
    result = wait_for_ttl_probe(
        StubProbeTarget(
            ["demo-probe-unicode"],
            [["demo-probe-ascii", "demo-probe-unicode"], [], []],
        ),
        records,
        timeout_seconds=20,
        interval_seconds=10,
        grace_seconds=5,
        clock=clock,
        sleeper=sleeper,
    )
    assert result["result"] == "fail"
    assert result["archived_objects"] == ["demo-probe-unicode"]
    assert result["absent_from_table"] == ["demo-probe-ascii", "demo-probe-unicode"]


def test_ttl_probe_does_not_fail_before_stream_grace_expires(monkeypatch) -> None:
    records = probe_records()
    now = [0.0]
    monkeypatch.setattr(recon.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(recon.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    result = wait_for_ttl_probe(
        StubProbeTarget(["demo-probe-unicode"], [[], []]),
        records,
        timeout_seconds=0,
        grace_seconds=120,
    )
    assert result["result"] == "skipped"


def test_ttl_probe_skips_when_consistent_presence_is_never_observed() -> None:
    records = probe_records()
    result = wait_for_ttl_probe(
        StubProbeTarget([], [[], []]),
        records,
        timeout_seconds=0,
    )
    assert result["result"] == "skipped"


def test_probe_archive_key_matches_handler_for_ttl_and_ttl_less_records() -> None:
    prefix = "audit-archive/expired"
    records = [
        {
            "event_id": "demo-probe-ascii",
            "timestamp": "2026-08-17T12:34:56Z",
            "expires_at": 1786962896,
        },
        {
            "event_id": "demo-probe-unexpirable",
            "timestamp": "2026-08-17T12:34:56Z",
        },
    ]
    for record in records:
        assert archive_key(record, prefix) == archive_handler.archive_key(record)


class StubCleanupTarget:
    def __init__(self, listings: list[list[str]] | None = None) -> None:
        self.deleted: list[str] = []
        self.listings = listings or [[]]
        self.list_calls = 0

    def archive_objects(self) -> list[str]:
        listing = self.listings[min(self.list_calls, len(self.listings) - 1)]
        self.list_calls += 1
        return listing

    def delete_objects(self, keys: list[str]) -> None:
        self.deleted.extend(keys)


def test_probe_cleanup_deletes_derived_keys_even_when_listing_misses_them() -> None:
    target = StubCleanupTarget()
    key = "audit-archive/expired/dt=unknown/demo-probe__stamp.jsonl.gz"
    clock, sleeper = fake_clock()
    delete_probe_objects(
        target,
        {key},
        interval_seconds=5,
        max_seconds=45,
        batching_window_seconds=10,
        clock=clock,
        sleeper=sleeper,
    )
    assert target.deleted == [key]


def test_probe_cleanup_catches_key_appearing_on_later_poll() -> None:
    key = "audit-archive/expired/dt=unknown/demo-probe__stamp.jsonl.gz"
    target = StubCleanupTarget([[], [], [key], []])
    clock, sleeper = fake_clock()
    delete_probe_objects(
        target,
        {key},
        interval_seconds=5,
        max_seconds=45,
        batching_window_seconds=10,
        clock=clock,
        sleeper=sleeper,
    )
    assert target.deleted == [key, key]
    assert clock() == 25
