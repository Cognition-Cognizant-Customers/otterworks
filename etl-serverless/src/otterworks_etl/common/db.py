"""PostgreSQL access via Secrets Manager-backed credentials."""

from contextlib import contextmanager

import psycopg2

from otterworks_etl.common.config import database_config


@contextmanager
def pg_connection():
    cfg = database_config()
    conn = psycopg2.connect(
        host=cfg.host,
        port=cfg.port,
        dbname=cfg.database,
        user=cfg.user,
        password=cfg.password,
        connect_timeout=10,
    )
    try:
        yield conn
    finally:
        conn.close()
