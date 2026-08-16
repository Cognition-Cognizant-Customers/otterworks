import pytest
from load import BATCH_SIZE, upsert_documents
from pymongo import ReplaceOne


class FakeResult:
    def __init__(self, matched, upserted_ids, modified):
        self.matched_count = matched
        self.upserted_ids = upserted_ids
        self.modified_count = modified


class FakeCollection:
    """Collection stub that keeps documents keyed by `_id`, like an upsert would."""

    def __init__(self):
        self.documents = {}
        self.bulk_calls = []

    def bulk_write(self, operations, ordered=True):
        self.bulk_calls.append(operations)
        matched, modified, upserted_ids = 0, 0, {}
        for index, operation in enumerate(operations):
            document = operation._doc
            key = document["_id"]
            if key in self.documents:
                matched += 1
                if self.documents[key] != document:
                    modified += 1
            else:
                upserted_ids[index] = key
            self.documents[key] = document
        return FakeResult(matched, upserted_ids, modified)


def documents(count, tenant="demo"):
    return [{"_id": f"id-{i}", "tenant": tenant, "sizeBytes": i} for i in range(count)]


def test_upsert_inserts_then_replaces_in_place():
    collection = FakeCollection()

    first = upsert_documents(collection, documents(3), "demo")
    second = upsert_documents(collection, documents(3), "demo")

    assert (first.upserted, first.matched) == (3, 0)
    assert (second.upserted, second.matched) == (0, 3)
    assert first.written == second.written == 3
    # A rerun leaves exactly the same document set.
    assert len(collection.documents) == 3


def test_upsert_uses_replace_by_id():
    collection = FakeCollection()

    upsert_documents(collection, documents(1), "demo")

    operation = collection.bulk_calls[0][0]
    assert isinstance(operation, ReplaceOne)
    assert operation._filter == {"_id": "id-0"}
    assert operation._upsert is True


def test_upsert_batches_large_inputs():
    collection = FakeCollection()

    upsert_documents(collection, documents(BATCH_SIZE + 5), "demo")

    assert [len(call) for call in collection.bulk_calls] == [BATCH_SIZE, 5]


def test_upsert_refuses_a_foreign_tenant_before_writing():
    collection = FakeCollection()

    with pytest.raises(ValueError, match="refusing to write"):
        upsert_documents(collection, documents(2, tenant="t01"), "demo")

    assert collection.bulk_calls == []


def test_upsert_of_nothing_writes_nothing():
    collection = FakeCollection()

    stats = upsert_documents(collection, [], "demo")

    assert stats.written == 0
    assert collection.bulk_calls == []
