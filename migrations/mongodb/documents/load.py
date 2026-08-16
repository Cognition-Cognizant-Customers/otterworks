"""Loader: idempotent bulk upserts into this workload's Atlas collections.

Every document's `_id` is its deterministic source primary key, so a rerun
replaces documents in place — the migration can be run any number of times and
the recon numbers stay identical.
"""

from pymongo import ReplaceOne

DEFAULT_CHUNK = 500


def upsert_documents(collection, docs: list[dict], chunk: int = DEFAULT_CHUNK) -> int:
    """Replace-upsert documents by `_id`; returns the number written."""
    written = 0
    for start in range(0, len(docs), chunk):
        window = docs[start:start + chunk]
        if not window:
            continue
        collection.bulk_write(
            [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in window],
            ordered=False,
        )
        written += len(window)
    return written
