"""Redis access: one client, one key namespace, four data families.

WHAT REDIS IS FOR HERE
----------------------
Everything in this file is data that is either (a) hot and re-derivable, or
(b) short-lived by nature. Nothing here is a system of record -- lose the whole
Redis instance and the product keeps working, a little slower, because every
caller falls back to the source it was reading before.

    family                      structure     why this structure
    --------------------------  ------------  ------------------------------
    jm:session:<id>             HASH          a session is a flat record of
                                              fields, and HINCRBY/HSET update
                                              one field without rewriting it
    jm:geo:<name>               STRING + TTL  one lookup, one answer; SET/GET
                                              is the whole access pattern
    jm:agg:<digest>             STRING + TTL  a whole JSON payload cached under
                                              the filter combination that
                                              produced it
    jm:audit:recent             LIST          LPUSH + LTRIM is the textbook
                                              bounded activity log

Key names use ':' to show hierarchy, as taught in Unit 4 Lecture 49.

FAILURE BEHAVIOUR
-----------------
Every helper in this module returns None / False rather than raising, and logs
at WARNING the first time a call fails. A cache that is down must not take the
application down with it -- that is the entire point of a cache.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Iterator

from .settings import get_db_settings

log = logging.getLogger("journeymind.db.redis")

_client = None
_lock = threading.Lock()
_unavailable_reason: str | None = None
_warned = False


def _make_client():
    global _unavailable_reason
    s = get_db_settings().redis
    if not s.configured:
        _unavailable_reason = "REDIS_HOST is not set"
        return None
    try:
        import redis                           # type: ignore
    except ImportError:
        _unavailable_reason = "the redis package is not installed"
        log.warning("Redis disabled: %s", _unavailable_reason)
        return None
    try:
        client = redis.Redis(
            host=s.host, port=s.port, db=s.db, password=s.password,
            decode_responses=True,             # everything here is text or JSON
            socket_connect_timeout=3,
            socket_timeout=3,
            health_check_interval=30,
        )
        client.ping()
        log.info("Redis ready - %s:%d db %d (prefix %r)", s.host, s.port, s.db, s.prefix)
        _unavailable_reason = None
        return client
    except Exception as exc:
        _unavailable_reason = f"{type(exc).__name__}: {exc}"
        log.warning("Redis unavailable (%s) - callers will fall back",
                    _unavailable_reason)
        return None


def get_client():
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = _make_client()
    return _client


def reset_client() -> None:
    """Drop the client. Test hook, seeding hook, and the shutdown path.

    The old client is closed rather than merely dereferenced: it owns a
    connection pool, and a pool that is only unreferenced keeps its sockets
    until the garbage collector gets to it.
    """
    global _client, _warned
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:       # already broken, which is why we are here
                pass
        _client = None
        _warned = False
    get_db_settings.cache_clear()


def available() -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        client.ping()
        return True
    except Exception as exc:
        log.debug("Redis availability check failed: %s", exc)
        return False


def unavailable_reason() -> str | None:
    return _unavailable_reason


def key(*parts: str) -> str:
    """`jm:session:bk_abc`. The one place key names are assembled."""
    return get_db_settings().redis.key(*parts)


def _degrade(op: str, exc: Exception) -> None:
    """Log the first failure loudly, the rest quietly. Never raise."""
    global _warned
    if not _warned:
        _warned = True
        log.warning("Redis %s failed (%s: %s) - falling back for this request "
                    "and further failures will log at DEBUG",
                    op, type(exc).__name__, exc)
    else:
        log.debug("Redis %s failed: %s", op, exc)


# --------------------------------------------------------------------------
# strings  (caching, Unit 4 Lecture 49 "Caching" use case)
# --------------------------------------------------------------------------
def get_json(name: str) -> Any | None:
    client = get_client()
    if client is None:
        return None
    try:
        raw = client.get(name)
    except Exception as exc:
        _degrade("GET", exc)
        return None
    if raw is None:
        return None                            # an ordinary cache miss
    try:
        return json.loads(raw)
    except ValueError:
        log.warning("discarding unparseable cached value at %s", name)
        return None


def set_json(name: str, value: Any, ttl_s: int | None = None) -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        payload = json.dumps(value, default=str)
    except (TypeError, ValueError) as exc:
        log.warning("refusing to cache a non-serialisable value at %s: %s", name, exc)
        return False
    try:
        # SET key value EX seconds -- the form taught in Lecture 49.
        client.set(name, payload, ex=ttl_s if ttl_s and ttl_s > 0 else None)
        return True
    except Exception as exc:
        _degrade("SET", exc)
        return False


def delete(*names: str) -> int:
    client = get_client()
    if client is None or not names:
        return 0
    try:
        return int(client.delete(*names))
    except Exception as exc:
        _degrade("DEL", exc)
        return 0


# --------------------------------------------------------------------------
# hashes  (structured records: the live booking sessions)
# --------------------------------------------------------------------------
def hset_all(name: str, mapping: dict[str, str], ttl_s: int | None = None) -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        pipe = client.pipeline()
        pipe.delete(name)                      # a rewrite, not a merge
        pipe.hset(name, mapping=mapping)
        if ttl_s and ttl_s > 0:
            pipe.expire(name, ttl_s)           # EXPIRE key seconds
        pipe.execute()
        return True
    except Exception as exc:
        _degrade("HSET", exc)
        return False


def hgetall(name: str) -> dict[str, str] | None:
    client = get_client()
    if client is None:
        return None
    try:
        out = client.hgetall(name)
    except Exception as exc:
        _degrade("HGETALL", exc)
        return None
    return out or None


def ttl(name: str) -> int | None:
    """Remaining life in seconds. -1 = no expiry set, -2 = the key is gone."""
    client = get_client()
    if client is None:
        return None
    try:
        return int(client.ttl(name))
    except Exception as exc:
        _degrade("TTL", exc)
        return None


# --------------------------------------------------------------------------
# lists  (bounded activity log: LPUSH + LTRIM)
# --------------------------------------------------------------------------
def lpush_trim(name: str, value: Any, keep: int) -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        pipe = client.pipeline()
        pipe.lpush(name, json.dumps(value, default=str))
        pipe.ltrim(name, 0, max(0, keep - 1))  # newest `keep` entries only
        pipe.execute()
        return True
    except Exception as exc:
        _degrade("LPUSH", exc)
        return False


def lrange_json(name: str, start: int = 0, stop: int = -1) -> list[Any] | None:
    client = get_client()
    if client is None:
        return None
    try:
        raw = client.lrange(name, start, stop)
    except Exception as exc:
        _degrade("LRANGE", exc)
        return None
    out = []
    for item in raw:
        try:
            out.append(json.loads(item))
        except ValueError:
            continue
    return out


def llen(name: str) -> int | None:
    client = get_client()
    if client is None:
        return None
    try:
        return int(client.llen(name))
    except Exception as exc:
        _degrade("LLEN", exc)
        return None


# --------------------------------------------------------------------------
# housekeeping
# --------------------------------------------------------------------------
def scan_keys(pattern: str, count: int = 500) -> Iterator[str]:
    """SCAN, never KEYS.

    `KEYS jm:session:*` walks the whole keyspace in one blocking call and stalls
    every other client while it does. SCAN returns a cursor and gives the server
    room to breathe between batches, which matters as soon as the keyspace is
    larger than a demonstration.
    """
    client = get_client()
    if client is None:
        return
    try:
        yield from client.scan_iter(match=pattern, count=count)
    except Exception as exc:
        _degrade("SCAN", exc)
        return


def count_keys(pattern: str) -> int:
    return sum(1 for _ in scan_keys(pattern))
