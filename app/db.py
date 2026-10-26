"""Database connection pool (as app_user). Rows come back as dicts."""

from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def open_pool(database_url: str) -> None:
    global _pool
    _pool = ConnectionPool(
        database_url,
        min_size=1,
        max_size=5,
        kwargs={"row_factory": dict_row},
        open=True,
    )


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def get_conn() -> Iterator[psycopg.Connection]:
    """FastAPI dependency: one connection per request, committed at the end (rolled back on error)."""
    if _pool is None:
        raise RuntimeError("database pool is not open")
    with _pool.connection() as conn:
        yield conn
