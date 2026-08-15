"""Loader — idempotent bulk upsert of `files` documents into Atlas.

Idempotent per namespace: `_id` is the source partition key, so a rerun replaces
each document in place instead of inserting a duplicate. Reconciliation numbers
are therefore identical after any number of runs.
"""

from dataclasses import dataclass

from pymongo import ReplaceOne

BATCH_SIZE = 1_000


@dataclass
class LoadStats:
    matched: int = 0
    upserted: int = 0
    modified: int = 0

    @property
    def written(self) -> int:
        return self.matched + self.upserted

    def add(self, result) -> None:
        self.matched += result.matched_count
        self.upserted += len(result.upserted_ids)
        self.modified += result.modified_count


def upsert_documents(collection, documents: list[dict], ns: str) -> LoadStats:
    """Replace-or-insert each document by `_id`, in bulk.

    Every document is checked against `ns` first: the loader must never write an
    item belonging to another namespace, whatever the extractor handed it.
    """
    stats = LoadStats()
    if not documents:
        return stats

    operations = []
    for document in documents:
        if document.get("tenant") != ns:
            raise ValueError(
                f"document {document.get('_id')!r} has tenant "
                f"{document.get('tenant')!r}, refusing to write into ns {ns!r}"
            )
        operations.append(ReplaceOne({"_id": document["_id"]}, document, upsert=True))

    for start in range(0, len(operations), BATCH_SIZE):
        result = collection.bulk_write(operations[start : start + BATCH_SIZE], ordered=False)
        stats.add(result)
    return stats
