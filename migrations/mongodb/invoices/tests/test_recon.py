"""Unit tests for the pure parts of recon (no Atlas: reports are built by hand)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import recon  # noqa: E402


def test_buckets_cover_the_whole_id_space_in_ascending_order():
    buckets = recon._buckets()

    assert len(buckets) == 256
    assert buckets[0]["$gte"] == "00"
    assert buckets[-1]["$gte"] == "ff"
    # contiguous: each bucket starts where the previous one ended
    assert all(low["$lt"] == high["$gte"]
               for low, high in zip(buckets, buckets[1:]))
    # the closing bound sorts after every hex digit, so "ffff..." is included
    assert buckets[-1]["$lt"] > "ffffffff"


def planted(index: int, ns: str = "demo") -> dict:
    """A quarantined row shaped exactly the way the seeder plants its orphans."""
    return {"lineId": recon.seeded_uuid(f"{ns}:line:{index}"),
            "danglingInvoiceId": recon.seeded_uuid(f"{ns}:ghost-invoice:{index}"),
            "invoiceNo": f"{ns.upper()}-GHOST-{index:09d}",
            "amount": "10.00",
            "quarantineReason": "missing_header"}


def test_planted_orphans_are_recognised_by_their_derived_ids():
    assert recon.unplanted_orphans([planted(0), planted(96153)], "demo") == []


def test_orphan_with_the_right_count_but_the_wrong_ids_is_flagged():
    swapped = planted(7) | {"lineId": recon.seeded_uuid("demo:line:8")}
    not_a_ghost = planted(9) | {"invoiceNo": "DEMO-000000009"}
    other_ns = planted(9, ns="stage")

    flagged = recon.unplanted_orphans([planted(7), swapped, not_a_ghost, other_ns],
                                      "demo")

    assert [row["lineId"] for row in flagged] == [
        swapped["lineId"], not_a_ghost["lineId"], other_ns["lineId"]]
    assert flagged[0]["why"] == "ids do not match the planted recipe"
    assert flagged[1]["why"] == "invoiceNo is not a planted ghost"


def report(**overrides) -> dict:
    doc = {
        "ns": "demo",
        "database": "ow_tp_demo",
        "reconciledAt": "2026-01-01T00:00:00Z",
        "manifestGeneratedAt": "2026-01-01T00:00:00Z",
        "counts": {"invoices": 2, "embeddedLines": 3, "orphanedLines": 1,
                   "totalLines": 4, "zeroLineInvoices": 1, "thinInvoices": 1,
                   "minLinesPerInvoice": 0, "maxLinesPerInvoice": 3},
        "checksum": {"checksum": "abc", "lines": 4, "amountTotal": "10.00"},
        "expected": {"invoices": 2, "lines": 4, "orphans": 1, "checksum": "abc",
                     "embeddedLines": 3,
                     "fanout": {"zeroLineInvoices": 1, "thinInvoices": 1}},
        "checks": [{"check": "invoices documents == manifest INVOICE_HEADER rows",
                    "actual": 2, "expected": 2, "ok": True}],
        "anomalyLedger": {"orphans": [planted(7)],
                          "danglingInvoiceIds": [planted(7)["danglingInvoiceId"]],
                          "danglingIdsThatResolve": [],
                          "orphanLinesAlsoEmbedded": [],
                          "unplantedOrphans": [],
                          "unexpectedQuarantineReasons": []},
        "verdict": "PASS",
    }
    return doc | overrides


def test_counts_table_marks_a_mismatched_metric_and_never_invents_ok():
    doc = report(
        counts=report()["counts"] | {"invoices": 1},
        checks=[{"check": "invoices documents == manifest INVOICE_HEADER rows",
                 "actual": 1, "expected": 2, "ok": False}],
        verdict="FAIL")

    markdown = recon.as_markdown(doc)

    assert "| `invoices` documents | 1 | 2 | MISMATCH |" in markdown
    assert "**Verdict: FAIL**" in markdown


def test_metric_without_a_check_is_reported_as_not_asserted():
    # a namespace with no measured fan-out: reported, never asserted
    doc = report(expected=report()["expected"] | {"fanout": None})

    markdown = recon.as_markdown(doc)

    assert "| Invoices with zero lines | 1 | — | not asserted |" in markdown


def test_anomaly_ledger_prose_is_derived_from_the_ledger():
    ledger = report()["anomalyLedger"]
    doc = report(anomalyLedger=ledger | {
        "orphans": [ledger["orphans"][0] | {"quarantineReason": "something_else"}],
        "danglingIdsThatResolve": ["ghost"],
        "orphanLinesAlsoEmbedded": ["inv-1"]})

    markdown = recon.as_markdown(doc)

    assert "['something_else']" in markdown
    assert "1 of them also embedded" in markdown
    assert "1 of the 1 distinct `INVOICE_ID`s" in markdown
