"""Idempotent Atlas loader for the `customers` workload.

Every write is keyed on a deterministic `_id` — the source `CUST_ID` for a
customer, `"<CUST_ID>:<COLUMN>"` for a quarantine-ledger entry — and issued as
an unordered `ReplaceOne(upsert=True)` batch, so re-running the migration
converges on the same documents instead of duplicating them.
"""

from dataclasses import dataclass

from pymongo import ReplaceOne

import config


@dataclass
class LoadStats:
    upserted: int = 0
    modified: int = 0
    matched: int = 0

    def add(self, result) -> None:
        self.upserted += len(result.upserted_ids or {})
        self.modified += result.modified_count
        self.matched += result.matched_count


def quarantine_docs(doc, quarantined, ns, migrated_at):
    """Ledger documents for the fields quarantined on one customer."""
    return [{
        "_id": f"{doc['_id']}:{q.field}",
        "custId": doc["_id"],
        "field": q.field,
        "kind": q.kind,
        "raw": q.raw,
        "_migration": {"ns": ns, "sourceTable": config.SOURCE_TABLE,
                       "migratedAt": migrated_at},
    } for q in quarantined]


def _replace_all(collection, docs, stats) -> None:
    if not docs:
        return
    ops = [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs]
    stats.add(collection.bulk_write(ops, ordered=False))


def load_batch(db, customers, quarantine, cust_stats, quar_stats) -> None:
    """Upsert one batch of customer documents and their quarantine entries."""
    _replace_all(db[config.CUSTOMERS], customers, cust_stats)
    _replace_all(db[config.QUARANTINE], quarantine, quar_stats)
