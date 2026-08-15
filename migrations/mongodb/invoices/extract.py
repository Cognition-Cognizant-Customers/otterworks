"""Streaming extractor for the legacy Oracle invoice feed.

Headers and lines are read through two independent server-side cursors, both
ordered by `INVOICE_ID`, and merge-joined in one pass: memory is bounded by a
single invoice's lines (3-25 rows) plus the fetch array size, never by the
150k-row line feed. Lines whose `INVOICE_ID` never appears in
`INVOICE_HEADER` fall out of the merge as orphans.
"""

import os
from collections.abc import Iterator

import oracledb

# NUMBER columns must arrive as decimal.Decimal, not float: cents survive the
# trip to Decimal128 only if they are never a binary double.
oracledb.defaults.fetch_decimals = True

HEADER_COLUMNS = ("INVOICE_ID", "INVOICE_NO", "CUST_ID", "TENANT_ID",
                  "INVOICE_DT", "DUE_DT", "STATUS_CD", "TOTAL_AMT", "BATCH_NO")
LINE_COLUMNS = ("LINE_ID", "INVOICE_NO", "INVOICE_ID", "CUST_ID", "CUST_NO",
                "CUST_NAME", "TENANT_ID", "LINE_NO", "LINE_TYPE_CD",
                "ITEM_DESC", "QTY", "UNIT_PRICE", "AMOUNT", "TAX_AMT",
                "INVOICE_DT", "SERVICE_PERIOD", "POSTED_YN", "GL_ACCT_CSV",
                "BATCH_NO", "SRC_SYSTEM")

HEADER_SQL = (f"SELECT {', '.join(HEADER_COLUMNS)} FROM invoice_header "
              "WHERE batch_no = :1 ORDER BY invoice_id")
LINE_SQL = (f"SELECT {', '.join(LINE_COLUMNS)} FROM invoice_line "
            "WHERE batch_no = :1 ORDER BY invoice_id, line_id")

INVOICE = "invoice"
ORPHAN_LINE = "orphan_line"


def connect() -> oracledb.Connection:
    return oracledb.connect(
        user=os.getenv("DB_USER", "ow_billing"),
        password=os.getenv("DB_PASSWORD", "ow_billing"),
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "52521")),
        service_name=os.getenv("DB_SERVICE", "FREEPDB1"),
    )


def fetch_codes(conn, code_type: str) -> dict[int, str]:
    """CODES('<code_type>') as {code_val: code_desc} — the magic-number decoder."""
    with conn.cursor() as cur:
        cur.execute("SELECT code_val, code_desc FROM codes WHERE code_type = :1",
                    [code_type])
        return {int(val): desc for val, desc in cur}


def _iter_rows(conn, sql: str, batch_no: int, arraysize: int) -> Iterator[dict]:
    """Yield dict rows from a server-side cursor, `arraysize` rows per round trip."""
    cur = conn.cursor()
    cur.arraysize = arraysize
    cur.prefetchrows = arraysize + 1
    try:
        cur.execute(sql, [batch_no])
        columns = [d[0].upper() for d in cur.description]
        while True:
            rows = cur.fetchmany(arraysize)
            if not rows:
                return
            for row in rows:
                yield dict(zip(columns, row))
    finally:
        cur.close()


def iter_units(conn, batch_no: int, arraysize: int = 1000) -> Iterator[tuple]:
    """Yield `(INVOICE, header, lines)` and `(ORPHAN_LINE, line, None)` in one pass.

    Both cursors are ordered by `INVOICE_ID` and advanced together, which is
    only correct while Oracle's `ORDER BY` collation agrees with Python's `<`.
    Both directions of a disagreement are checked — a line pointing at an
    already-consumed header, and a header arriving after its lines were
    quarantined — so a skew fails loudly instead of silently inflating the
    orphan count the recon contract asserts on.
    """
    headers = _iter_rows(conn, HEADER_SQL, batch_no, arraysize)
    lines = _iter_rows(conn, LINE_SQL, batch_no, arraysize)
    seen_headers: set[str] = set()
    orphaned_ids: set[str] = set()  # bounded by the orphan count, not the feed
    line = next(lines, None)

    def emit_orphan(row: dict) -> tuple:
        if row["INVOICE_ID"] in seen_headers:
            raise RuntimeError(
                "merge-join ordering mismatch: line "
                f"{row['LINE_ID']} points at already-consumed header "
                f"{row['INVOICE_ID']}")
        orphaned_ids.add(row["INVOICE_ID"])
        return (ORPHAN_LINE, row, None)

    for header in headers:
        invoice_id = header["INVOICE_ID"]
        while line is not None and line["INVOICE_ID"] < invoice_id:
            yield emit_orphan(line)
            line = next(lines, None)
        if invoice_id in orphaned_ids:
            raise RuntimeError(
                "merge-join ordering mismatch: header "
                f"{invoice_id} arrived after its lines were quarantined")
        group = []
        while line is not None and line["INVOICE_ID"] == invoice_id:
            group.append(line)
            line = next(lines, None)
        seen_headers.add(invoice_id)
        yield (INVOICE, header, group)

    while line is not None:
        yield emit_orphan(line)
        line = next(lines, None)
