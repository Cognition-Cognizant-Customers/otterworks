"""Batched Oracle extractor for the `customers` workload.

Streams `CUSTOMER_MASTER` for one conversion batch in `_id` order and yields
`(rows, eav_by_cust)` chunks. Nothing is materialized beyond the current chunk:
the customer cursor is read with `fetchmany`, and each chunk's EAV rows are
fetched with a single bound `IN (...)` lookup keyed on that chunk's `CUST_ID`s.

`CODES` is small (a dozen rows) and is loaded once into a dict.
"""

import config

CODES_SQL = "SELECT code_type, code_val, code_desc FROM codes"

CUSTOMERS_SQL = """
    SELECT * FROM customer_master
     WHERE conversion_batch_no = :batch
     ORDER BY cust_id
"""

EAV_SQL_TEMPLATE = """
    SELECT entity_id, attr_name, attr_value, attr_type, created_dt
      FROM entity_attr_value
     WHERE entity_type = 'CUSTOMER'
       AND entity_id IN ({placeholders})
"""


def load_codes(conn) -> dict:
    """`(code_type, code_value) -> code_desc` for the whole `CODES` table."""
    with conn.cursor() as cur:
        cur.execute(CODES_SQL)
        return {(str(t), v): d for t, v, d in cur}


def _row_mapper(cur):
    columns = [c[0].upper() for c in cur.description]
    return lambda values: dict(zip(columns, values))


def _eav_for(conn, cust_ids):
    """EAV rows for the given customers, grouped by `entity_id`."""
    grouped = {cust_id: [] for cust_id in cust_ids}
    if not cust_ids:
        return grouped
    names = [f":c{i}" for i in range(len(cust_ids))]
    sql = EAV_SQL_TEMPLATE.format(placeholders=", ".join(names))
    with conn.cursor() as cur:
        cur.execute(sql, {f"c{i}": cid for i, cid in enumerate(cust_ids)})
        for entity_id, attr_name, attr_value, attr_type, created_dt in cur:
            grouped.setdefault(entity_id, []).append({
                "ENTITY_ID": entity_id,
                "ATTR_NAME": attr_name,
                "ATTR_VALUE": attr_value,
                "ATTR_TYPE": attr_type,
                "CREATED_DT": created_dt,
            })
    return grouped


def iter_customer_batches(conn, batch_no: int, size: int = config.BATCH_SIZE):
    """Yield `(rows, eav_by_cust)` for successive chunks of `size` customers."""
    with conn.cursor() as cur:
        cur.arraysize = size
        cur.execute(CUSTOMERS_SQL, batch=batch_no)
        to_dict = _row_mapper(cur)
        while True:
            chunk = cur.fetchmany(size)
            if not chunk:
                return
            rows = [to_dict(values) for values in chunk]
            yield rows, _eav_for(conn, [r["CUST_ID"] for r in rows])


def count_customers(conn, batch_no: int) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM customer_master "
                    "WHERE conversion_batch_no = :batch", batch=batch_no)
        return cur.fetchone()[0]


def count_customer_eav(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM entity_attr_value "
                    "WHERE entity_type = 'CUSTOMER'")
        return cur.fetchone()[0]
