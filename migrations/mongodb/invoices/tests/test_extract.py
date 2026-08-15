"""Unit tests for the header/line merge-join (no Oracle: cursors are faked)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import extract  # noqa: E402

BATCH_NO = 85559852


def fake_cursors(monkeypatch, headers: list[dict], lines: list[dict]) -> None:
    """Stand in for the two ordered server-side cursors."""
    ordered_headers = sorted(headers, key=lambda r: r["INVOICE_ID"])
    ordered_lines = sorted(lines, key=lambda r: (r["INVOICE_ID"], r["LINE_ID"]))

    def _iter_rows(conn, sql, batch_no, arraysize):
        assert batch_no == BATCH_NO
        rows = ordered_headers if sql is extract.HEADER_SQL else ordered_lines
        yield from rows

    monkeypatch.setattr(extract, "_iter_rows", _iter_rows)


def header(invoice_id: str) -> dict:
    return {"INVOICE_ID": invoice_id, "INVOICE_NO": invoice_id.upper()}


def line(line_id: str, invoice_id: str) -> dict:
    return {"LINE_ID": line_id, "INVOICE_ID": invoice_id}


def test_lines_are_grouped_under_their_header(monkeypatch):
    fake_cursors(monkeypatch,
                 [header("inv-a"), header("inv-b")],
                 [line("l3", "inv-b"), line("l1", "inv-a"), line("l2", "inv-a")])

    units = list(extract.iter_units(None, BATCH_NO))

    assert [(kind, row["INVOICE_ID"], [x["LINE_ID"] for x in (lines or [])])
            for kind, row, lines in units] == [
        (extract.INVOICE, "inv-a", ["l1", "l2"]),
        (extract.INVOICE, "inv-b", ["l3"]),
    ]


def test_lines_without_a_header_come_out_as_orphans(monkeypatch):
    # ghost pointers sort before, between and after the real headers
    fake_cursors(monkeypatch,
                 [header("inv-b"), header("inv-d")],
                 [line("l0", "inv-a"), line("l1", "inv-b"),
                  line("l2", "inv-c"), line("l3", "inv-d"),
                  line("l4", "inv-e")])

    units = list(extract.iter_units(None, BATCH_NO))
    orphans = [row["LINE_ID"] for kind, row, _ in units
               if kind == extract.ORPHAN_LINE]
    embedded = {row["INVOICE_ID"]: [x["LINE_ID"] for x in lines]
                for kind, row, lines in units if kind == extract.INVOICE}

    assert orphans == ["l0", "l2", "l4"]
    assert embedded == {"inv-b": ["l1"], "inv-d": ["l3"]}


def test_header_without_lines_yields_an_empty_group(monkeypatch):
    fake_cursors(monkeypatch, [header("inv-a")], [])

    units = list(extract.iter_units(None, BATCH_NO))

    assert units == [(extract.INVOICE, header("inv-a"), [])]


def test_late_header_for_a_quarantined_line_is_an_error(monkeypatch):
    # Oracle ordered the headers inv-b, inv-a; Python says "inv-a" < "inv-b",
    # so inv-a's line is quarantined before its header shows up.
    def _iter_rows(conn, sql, batch_no, arraysize):
        if sql is extract.HEADER_SQL:
            yield from [header("inv-b"), header("inv-a")]
        else:
            yield from [line("l1", "inv-a"), line("l2", "inv-b")]

    monkeypatch.setattr(extract, "_iter_rows", _iter_rows)

    with pytest.raises(RuntimeError, match="ordering mismatch"):
        list(extract.iter_units(None, BATCH_NO))


def test_line_pointing_at_a_consumed_header_is_an_error(monkeypatch):
    def _iter_rows(conn, sql, batch_no, arraysize):
        if sql is extract.HEADER_SQL:
            yield from [header("inv-a"), header("inv-b")]
        else:
            # deliberately out of order: inv-a comes back after inv-b
            yield from [line("l1", "inv-b"), line("l2", "inv-a")]

    monkeypatch.setattr(extract, "_iter_rows", _iter_rows)

    with pytest.raises(RuntimeError, match="ordering mismatch"):
        list(extract.iter_units(None, BATCH_NO))
