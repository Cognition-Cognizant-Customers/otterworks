"""Idempotent bulk loader for the `invoices` workload.

Every document's `_id` is its legacy key (`INVOICE_ID` / `LINE_ID`), so writes
are full-document replacements with `upsert=True`: a second run of the whole
migration rewrites the same documents in place and leaves every recon number
unchanged.
"""

from pymongo import ReplaceOne


class Loader:
    """Buffers documents per collection and flushes them in unordered batches."""

    def __init__(self, db, batch_size: int = 500) -> None:
        self._db = db
        self._batch_size = batch_size
        self._pending: dict[str, list] = {}
        self.stats: dict[str, dict] = {}

    def add(self, collection: str, doc: dict) -> None:
        pending = self._pending.setdefault(collection, [])
        pending.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
        if len(pending) >= self._batch_size:
            self._flush(collection)

    def flush(self) -> None:
        for collection in list(self._pending):
            self._flush(collection)

    def _flush(self, collection: str) -> None:
        operations = self._pending.pop(collection, [])
        if not operations:
            return
        result = self._db[collection].bulk_write(operations, ordered=False)
        stats = self.stats.setdefault(
            collection, {"upserted": 0, "modified": 0, "matched": 0})
        stats["upserted"] += result.upserted_count
        stats["modified"] += result.modified_count
        stats["matched"] += result.matched_count
