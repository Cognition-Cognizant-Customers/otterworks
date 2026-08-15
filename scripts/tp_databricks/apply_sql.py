#!/usr/bin/env python3
"""Apply a .sql file to the serverless warehouse, one statement at a time.

The SQL Statement Execution API takes a single statement per request, so files
under databricks/sql/ separate statements with a line containing only `;`.

Usage:
    python3 scripts/tp_databricks/apply_sql.py databricks/sql/search_reindex_tables.sql
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dbx  # noqa: E402


def statements(sql_text: str) -> list[str]:
    """Split a .sql file on lines containing only a semicolon."""
    chunks = []
    current: list[str] = []
    for line in sql_text.splitlines():
        if line.strip() == ";":
            chunks.append("\n".join(current))
            current = []
        else:
            current.append(line)
    chunks.append("\n".join(current))
    return [c for c in (chunk.strip() for chunk in chunks) if c and not _only_comments(c)]


def _only_comments(chunk: str) -> bool:
    return all(not line.strip() or line.strip().startswith("--") for line in chunk.splitlines())


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(argv[0])
    for statement in statements(path.read_text()):
        first_line = statement.splitlines()[0][:100]
        print(f"-> {first_line}")
        dbx.sql(statement, catalog=dbx.CATALOG)
    print(f"applied {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
