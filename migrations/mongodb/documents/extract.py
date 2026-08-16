"""Extractor: streams the legacy Postgres document estate in bounded batches.

Never materializes the whole estate: documents are read through a server-side
(named) cursor and fetched `batch_size` at a time; each batch's versions and
snapshots are then fetched with one keyed query per table, so peak memory is
one batch of documents plus their fan-out (2-12 versions, ~0.2 snapshots each).
"""

from dataclasses import dataclass, field
from typing import Iterator

import psycopg2
import psycopg2.extras

DOC_COLUMNS = (
    "id", "title", "content", "content_type", "owner_id", "folder_id",
    "is_deleted", "is_template", "word_count", "version", "created_at", "updated_at",
)
VERSION_COLUMNS = (
    "id", "document_id", "version_number", "title", "content", "created_by", "created_at",
)
SNAPSHOT_COLUMNS = (
    "id", "document_id", "state_b64", "label", "created_by", "created_at",
)

DEFAULT_BATCH_SIZE = 500


@dataclass
class DocumentBatch:
    """One bounded slice of the estate: documents plus their children."""

    documents: list[dict] = field(default_factory=list)
    versions_by_document: dict[str, list[dict]] = field(default_factory=dict)
    snapshots_by_document: dict[str, list[dict]] = field(default_factory=dict)

    @property
    def snapshots(self) -> list[dict]:
        return [s for snaps in self.snapshots_by_document.values() for s in snaps]


def connect(pg: dict):
    conn = psycopg2.connect(**pg)
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _fetch_children(conn, sql: str, doc_ids: list[str], key: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (doc_ids,))
        for row in cur:
            grouped.setdefault(str(row[key]), []).append(row)
    return grouped


def iter_document_batches(
    conn, schema: str, batch_size: int = DEFAULT_BATCH_SIZE
) -> Iterator[DocumentBatch]:
    """Yield batches of documents with their versions and snapshots attached."""
    versions_sql = (
        f"SELECT {', '.join(VERSION_COLUMNS)} FROM {schema}.document_versions"
        " WHERE document_id = ANY(%s::uuid[]) ORDER BY document_id, version_number"
    )
    snapshots_sql = (
        f"SELECT {', '.join(SNAPSHOT_COLUMNS)} FROM {schema}.document_snapshots"
        " WHERE document_id = ANY(%s::uuid[]) ORDER BY document_id, id"
    )
    with conn.cursor(
        name="mongo_documents_stream", cursor_factory=psycopg2.extras.RealDictCursor
    ) as stream:
        stream.itersize = batch_size
        stream.execute(
            f"SELECT {', '.join(DOC_COLUMNS)} FROM {schema}.documents ORDER BY id"
        )
        while True:
            rows = stream.fetchmany(batch_size)
            if not rows:
                return
            doc_ids = [str(row["id"]) for row in rows]
            yield DocumentBatch(
                documents=[dict(row) for row in rows],
                versions_by_document=_fetch_children(
                    conn, versions_sql, doc_ids, "document_id"),
                snapshots_by_document=_fetch_children(
                    conn, snapshots_sql, doc_ids, "document_id"),
            )


def iter_orphaned_snapshots(
    conn, schema: str, batch_size: int = DEFAULT_BATCH_SIZE
) -> Iterator[list[dict]]:
    """Yield batches of snapshots whose `document_id` has no document row."""
    cols = ", ".join(f"s.{c}" for c in SNAPSHOT_COLUMNS)
    with conn.cursor(
        name="mongo_orphan_snapshot_stream",
        cursor_factory=psycopg2.extras.RealDictCursor,
    ) as stream:
        stream.itersize = batch_size
        stream.execute(
            f"SELECT {cols} FROM {schema}.document_snapshots s"
            f" LEFT JOIN {schema}.documents d ON d.id = s.document_id"
            " WHERE d.id IS NULL ORDER BY s.id"
        )
        while True:
            rows = stream.fetchmany(batch_size)
            if not rows:
                return
            yield [dict(row) for row in rows]
