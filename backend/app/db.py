"""Postgres access: one small pool, dict rows, parameterized SQL only."""
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.database_url,
            min_size=0,
            max_size=settings.db_pool_max,
            kwargs={'row_factory': dict_row, 'connect_timeout': 5},
            open=True,
        )
    return _pool


@contextmanager
def conn():
    with pool().connection() as c:
        yield c


def fetchall(sql: str, params: dict | tuple | None = None) -> list[dict]:
    with conn() as c:
        return c.execute(sql, params).fetchall()


def fetchone(sql: str, params: dict | tuple | None = None) -> dict | None:
    with conn() as c:
        return c.execute(sql, params).fetchone()


def execute(sql: str, params: dict | tuple | None = None) -> dict | None:
    """Run a write; returns the RETURNING row if any. Commits on exit."""
    with conn() as c:
        cur = c.execute(sql, params)
        return cur.fetchone() if cur.description else None
