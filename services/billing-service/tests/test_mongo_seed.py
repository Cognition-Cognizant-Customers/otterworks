from __future__ import annotations

from bson.decimal128 import Decimal128

from db.mongo_seed import ORIGIN, customer_documents, invoice_documents


def test_customer_documents_are_deterministic_and_ns_scoped():
    first = customer_documents("dev")
    second = customer_documents("dev")
    assert first == second
    assert len(first) == 9
    assert all(doc["ns"] == "dev" and doc["origin"] == ORIGIN for doc in first)


def test_customer_documents_ids_are_namespace_scoped():
    docs = customer_documents("dev")
    assert all(doc["_id"] == f"dev:{ORIGIN}:{doc['tenant_id']}" for doc in docs)
    other = customer_documents("other")
    assert not {doc["_id"] for doc in docs} & {doc["_id"] for doc in other}


def test_customer_documents_embed_subscription_plan_snapshot():
    by_id = {doc["tenant_id"]: doc for doc in customer_documents("dev")}
    tenant_two = by_id["00000000-0000-0000-0000-000000000002"]
    (subscription,) = tenant_two["subscriptions"]
    assert subscription["status"] == "suspended"
    assert subscription["suspended_on"] is not None
    assert subscription["plan"]["code"] == "GROWTH"
    assert subscription["plan"]["monthly_fee"] == Decimal128("149.00")

    tenant_nine = by_id["00000000-0000-0000-0000-000000000009"]
    assert [note["credit_note_id"] for note in tenant_nine["credit_notes"]] == [
        "70000000-0000-0000-0000-000000000005",
        "70000000-0000-0000-0000-000000000006",
    ]


def test_rating_history_only_on_tenant_one():
    docs = customer_documents("dev")
    with_history = [doc["tenant_id"] for doc in docs if doc["rating_history"]]
    assert with_history == ["00000000-0000-0000-0000-000000000001"]
    history = next(doc for doc in docs if doc["rating_history"])["rating_history"]
    assert len(history) == 3
    assert all(entry["rollover_units"] == 100 for entry in history)


def test_invoice_documents_embed_lines():
    docs = invoice_documents("dev")
    assert len(docs) == 3
    by_id = {doc["invoice_id"]: doc for doc in docs}
    lined = by_id["60000000-0000-0000-0000-000000000001"]
    assert all(doc["_id"] == f"dev:{ORIGIN}:{doc['invoice_id']}" for doc in docs)
    assert [line["line_no"] for line in lined["lines"]] == [1, 2]
    assert lined["total"] == Decimal128("161.29")
    assert all(doc["ns"] == "dev" and doc["origin"] == ORIGIN for doc in docs)
