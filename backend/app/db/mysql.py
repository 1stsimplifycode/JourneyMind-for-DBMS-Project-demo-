"""MySQL access: a small connection pool and a handful of statement helpers.

WHY RAW SQL AND NOT AN ORM
--------------------------
The SQL in this project is meant to be read. Every statement that touches the
database is either in ``database/mysql/schema.sql`` or in a function in this
package, written out in full, so that "what does the application ask MySQL?"
is answerable by reading rather than by guessing what a mapper generated.

WHY A POOL
----------
Opening a TCP connection and authenticating costs about 15 ms. The enterprise
dashboard issues several statements per request, and a page load that pays that
five times over is a page load that feels slow for no reason.

FAILURE BEHAVIOUR
-----------------
`available()` is false when MySQL is unconfigured, unreachable or refusing
connections, and every caller has a documented fallback (the bundled CSV, the
in-memory audit buffer). Failures are logged at WARNING with the driver's own
message -- never swallowed silently -- and retried on the next call, because a
database that was down a minute ago may not be down now.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Sequence

from .settings import get_db_settings

log = logging.getLogger("journeymind.db.mysql")

_pool = None
_pool_lock = threading.Lock()
_unavailable_reason: str | None = None


class MySQLUnavailable(RuntimeError):
    """Raised instead of returning a wrong answer when MySQL cannot be reached."""


def _make_pool():
    """Create the pool once. Returns None when MySQL is not usable."""
    global _unavailable_reason
    s = get_db_settings().mysql
    if not s.configured:
        _unavailable_reason = "MYSQL_HOST/MYSQL_USER are not set"
        return None
    try:
        from mysql.connector import pooling
    except ImportError:                       # driver absent from this image
        _unavailable_reason = "mysql-connector-python is not installed"
        log.warning("MySQL disabled: %s", _unavailable_reason)
        return None
    try:
        pool = pooling.MySQLConnectionPool(
            pool_name="journeymind",
            pool_size=max(1, min(s.pool_size, 32)),
            host=s.host, port=s.port, database=s.database,
            user=s.user, password=s.password,
            autocommit=True,                  # explicit transactions opt out below
            charset="utf8mb4",
            connection_timeout=10,
            use_pure=True,
        )
        log.info("MySQL pool ready - %s@%s:%d/%s (size %d)",
                 s.user, s.host, s.port, s.database, pool.pool_size)
        _unavailable_reason = None
        return pool
    except Exception as exc:
        _unavailable_reason = f"{type(exc).__name__}: {exc}"
        log.warning("MySQL unavailable (%s) - callers will fall back",
                    _unavailable_reason)
        return None


def get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = _make_pool()
    return _pool


def reset_pool() -> None:
    """Test hook, and the way a seeding script picks up new settings."""
    global _pool
    with _pool_lock:
        _pool = None
    get_db_settings.cache_clear()


@contextmanager
def connection() -> Iterator[Any]:
    """Borrow a pooled connection. Raises `MySQLUnavailable` if there is none."""
    pool = get_pool()
    if pool is None:
        raise MySQLUnavailable(_unavailable_reason or "MySQL is not configured")
    try:
        conn = pool.get_connection()
    except Exception as exc:
        raise MySQLUnavailable(f"{type(exc).__name__}: {exc}") from exc
    try:
        yield conn
    finally:
        conn.close()                          # returns it to the pool


def available() -> bool:
    """True when a connection can actually be opened right now."""
    try:
        with connection() as conn:
            conn.ping(reconnect=True, attempts=1, delay=0)
        return True
    except Exception as exc:
        log.debug("MySQL availability check failed: %s", exc)
        return False


def unavailable_reason() -> str | None:
    return _unavailable_reason


# --------------------------------------------------------------------------
# statements
# --------------------------------------------------------------------------
def query(sql: str, params: Sequence[Any] | None = None) -> list[dict]:
    """SELECT returning a list of dicts. Small result sets only."""
    with connection() as conn:
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(sql, tuple(params or ()))
            return cur.fetchall()
        finally:
            cur.close()


def query_rows(sql: str, params: Sequence[Any] | None = None,
               batch: int = 20_000) -> Iterator[tuple]:
    """SELECT streamed in batches, for the 60,000-row booking history.

    `fetchall()` on that table materialises every row at once; fetching in
    batches keeps the peak allocation to one batch and lets the caller build
    its NumPy columns incrementally.
    """
    with connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(sql, tuple(params or ()))
            while True:
                rows = cur.fetchmany(batch)
                if not rows:
                    return
                yield from rows
        finally:
            cur.close()


def scalar(sql: str, params: Sequence[Any] | None = None) -> Any:
    """The first column of the first row - COUNT(*) and friends."""
    with connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(sql, tuple(params or ()))
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            cur.close()


def execute(sql: str, params: Sequence[Any] | None = None) -> int:
    """One INSERT/UPDATE/DELETE. Returns the affected row count."""
    with connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(sql, tuple(params or ()))
            return cur.rowcount
        finally:
            cur.close()


def execute_many(sql: str, rows: Iterable[Sequence[Any]], chunk: int = 5_000) -> int:
    """Bulk insert, one transaction per chunk.

    `executemany` over a 60,000-row load with autocommit on would ask InnoDB to
    flush the redo log 60,000 times. Committing per chunk turns that into a
    dozen flushes, which is the difference between a seed that takes minutes
    and one that takes seconds.
    """
    total = 0
    buf: list[Sequence[Any]] = []
    with connection() as conn:
        cur = conn.cursor()
        try:
            for row in rows:
                buf.append(row)
                if len(buf) >= chunk:
                    total += _flush(conn, cur, sql, buf)
                    buf.clear()
            if buf:
                total += _flush(conn, cur, sql, buf)
        finally:
            cur.close()
    return total


def _flush(conn, cur, sql: str, buf: list[Sequence[Any]]) -> int:
    conn.start_transaction()
    try:
        cur.executemany(sql, buf)
        conn.commit()
        return cur.rowcount
    except Exception:
        conn.rollback()
        raise


@contextmanager
def transaction(isolation: str | None = None) -> Iterator[Any]:
    """An explicit ACID transaction: all the statements inside, or none of them.

    Used by the booking write path, where a session row and its attempt rows
    have to land together -- a session with no attempts, or attempts with no
    session, is a record of something that never happened. Rolling back on any
    exception is what makes that atomic (Unit 4, Lecture 43).

    `isolation` accepts the four SQL levels from Lecture 45
    ('READ UNCOMMITTED', 'READ COMMITTED', 'REPEATABLE READ', 'SERIALIZABLE').
    Left as None it uses MySQL's own default, REPEATABLE READ.
    """
    with connection() as conn:
        conn.start_transaction(isolation_level=isolation)
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
