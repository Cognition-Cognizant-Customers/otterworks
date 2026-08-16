import pytest
from extract import ATTRIBUTE_NAMES, batched, scan_items


class FakeTable:
    """DynamoDB table stub that pages and applies the `ns` filter itself."""

    def __init__(self, items, page_size=2):
        self.items = items
        self.page_size = page_size
        self.scan_calls = []

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)
        ns = kwargs["ExpressionAttributeValues"][":ns"]
        matching = [i for i in self.items if i["ns"] == ns]
        start = 0
        if "ExclusiveStartKey" in kwargs:
            start = next(
                idx + 1
                for idx, item in enumerate(matching)
                if item["id"] == kwargs["ExclusiveStartKey"]["id"]
            )
        page = matching[start : start + self.page_size]
        response = {"Items": page}
        if start + self.page_size < len(matching):
            response["LastEvaluatedKey"] = {"id": page[-1]["id"]}
        return response


def items(ns, count, offset=0):
    return [{"id": f"{ns}-{i + offset}", "ns": ns} for i in range(count)]


def test_scan_sums_every_page():
    table = FakeTable(items("demo", 5), page_size=2)

    scanned = list(scan_items("demo", table))

    assert [i["id"] for i in scanned] == [f"demo-{i}" for i in range(5)]
    assert len(table.scan_calls) == 3
    assert "ExclusiveStartKey" not in table.scan_calls[0]
    assert table.scan_calls[1]["ExclusiveStartKey"] == {"id": "demo-1"}


def test_scan_filters_to_the_namespace_slice():
    table = FakeTable(items("demo", 3) + items("t01", 4), page_size=2)

    scanned = list(scan_items("demo", table))

    assert {i["ns"] for i in scanned} == {"demo"}
    assert len(scanned) == 3
    assert table.scan_calls[0]["FilterExpression"] == "#ns = :ns"
    assert table.scan_calls[0]["ExpressionAttributeNames"] == ATTRIBUTE_NAMES


def test_scan_is_lazy():
    table = FakeTable(items("demo", 6), page_size=2)

    stream = scan_items("demo", table)
    next(stream)

    # Only the first page has been fetched.
    assert len(table.scan_calls) == 1


def test_scan_handles_an_empty_slice():
    table = FakeTable(items("t01", 3), page_size=2)

    assert list(scan_items("demo", table)) == []


def test_batched_groups_and_keeps_the_remainder():
    assert list(batched(iter(range(5)), 2)) == [[0, 1], [2, 3], [4]]
    assert list(batched(iter([]), 2)) == []


def test_batched_rejects_a_zero_batch_size():
    with pytest.raises(ValueError):
        list(batched(iter(range(3)), 0))
