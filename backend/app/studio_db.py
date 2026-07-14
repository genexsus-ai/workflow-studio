"""Shared SQL access for studio stores: the Postgres database or SQLite.

The studio's small stores (sources, analyses, reports, models, experiments)
were written against sqlite3. ``studio_connect`` keeps that code shape: in
file mode it yields a real sqlite3 connection to the given path; when
PERSISTENCE_BACKEND=postgres (and the database is reachable) it yields an
adapter over a shared SQLAlchemy engine that accepts the same qmark-style
SQL, so the callers' statements work on both backends.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

import sqlalchemy as sa

from app.config import get_settings

logger = logging.getLogger(__name__)

_engine: sa.Engine | None = None
_use_pg: bool | None = None


def get_studio_engine() -> sa.Engine:
    """The single engine shared by every Postgres-backed studio store."""
    global _engine
    if _engine is None:
        _engine = sa.create_engine(
            get_settings().sync_database_url, pool_pre_ping=True
        )
    return _engine


def use_postgres() -> bool:
    """True when configured for Postgres and the database answers.

    Decided once per process; on failure the studio falls back to its
    file/SQLite persistence so it stays usable.
    """
    global _use_pg
    if _use_pg is None:
        settings = get_settings()
        if not settings.use_db_persistence:
            _use_pg = False
        else:
            try:
                with get_studio_engine().connect() as conn:
                    conn.execute(sa.text("select 1"))
                _use_pg = True
            except Exception:
                logger.exception(
                    "PERSISTENCE_BACKEND=postgres but the database is "
                    "unreachable — falling back to SQLite for studio data"
                )
                _use_pg = False
    return _use_pg


class _PgConn:
    """sqlite3-shaped facade over a SQLAlchemy connection.

    Each statement runs in a savepoint so an expected failure (e.g. an
    ALTER TABLE for a column that already exists) doesn't poison the
    enclosing transaction, matching sqlite3's statement-level behavior.
    """

    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def _run(self, sql: str, params: Any) -> sa.CursorResult:
        nested = self._conn.begin_nested()
        try:
            if params:
                result = self._conn.exec_driver_sql(sql.replace("?", "%s"), params)
            else:
                result = self._conn.exec_driver_sql(sql)
            nested.commit()
        except Exception:
            nested.rollback()
            raise
        result.rowcount  # memoize while the cursor is live
        return result

    def execute(self, sql: str, params: tuple | list = ()) -> sa.CursorResult:
        return self._run(sql, tuple(params))

    def executemany(self, sql: str, seq: list[tuple]) -> sa.CursorResult:
        return self._run(sql, [tuple(p) for p in seq])


@contextmanager
def studio_connect(db_path) -> Iterator[Any]:
    """Connection to the studio database, committed on clean exit."""
    if use_postgres():
        with get_studio_engine().begin() as conn:
            yield _PgConn(conn)
    else:
        conn = sqlite3.connect(db_path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()


def try_execute(conn: Any, sql: str) -> None:
    """Run DDL that may legitimately fail (e.g. column already exists)."""
    try:
        conn.execute(sql)
    except (sqlite3.OperationalError, sa.exc.DatabaseError):
        pass
