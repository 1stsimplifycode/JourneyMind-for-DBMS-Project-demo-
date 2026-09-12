"""Neo4j access: one driver, and the Cypher the application actually runs.

WHAT LIVES IN THE GRAPH
-----------------------
The study area's multimodal network, and nothing else:

    (:Stop)                      a metro station, a bus stop, a road junction
                                 or a named place -- every vertex the router
                                 can stand on
    [:ROAD_LINK]                 a street segment you can walk along
    [:TRANSIT_LINK]              one stop to the next on a metro or bus route
    [:TRANSFER_LINK]             a walk between two nearby transit nodes

Both traversal directions of every link are stored, because that is what
``graph/builder.py`` builds: a street and a metro line are traversable each
way, and the router holds two directed edges for each. Mirroring it keeps the
graph in Neo4j and the graph in memory the same object rather than two things
that have to be reasoned about separately.

WHAT IS DELIBERATELY *NOT* A NODE LABEL
---------------------------------------
Route, Place, Campus and Provider. The corridor has ten routes, fifteen named
places, five campuses and three providers; making any of them a label would
create a label with an order of magnitude less data than the graph it sits in,
and every question they could answer is already answered by a property on the
relationship (`route_id`, `route_name`, `mode`) or by a column in MySQL. A
graph model is judged on whether the edges carry the meaning, not on how many
labels it can name.

FAILURE BEHAVIOUR
-----------------
`run()` returns None when Neo4j is unreachable, and every caller falls back to
the in-process graph it used before this module existed. The topology is
bundled with the application, so an unavailable graph database costs nothing
but the ability to ask it questions.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Sequence

from .settings import get_db_settings

log = logging.getLogger("journeymind.db.neo4j")

_driver = None
_lock = threading.Lock()
_unavailable_reason: str | None = None
_warned = False


def _make_driver():
    global _unavailable_reason
    s = get_db_settings().neo4j
    if not s.configured:
        _unavailable_reason = "NEO4J_URI/NEO4J_USERNAME are not set"
        return None
    try:
        from neo4j import GraphDatabase        # type: ignore
    except ImportError:
        _unavailable_reason = "the neo4j package is not installed"
        log.warning("Neo4j disabled: %s", _unavailable_reason)
        return None
    try:
        driver = GraphDatabase.driver(
            s.uri, auth=(s.username, s.password),
            connection_timeout=10,
            max_connection_lifetime=3600,
        )
        driver.verify_connectivity()
        log.info("Neo4j ready - %s (database %s)", s.uri, s.database)
        _unavailable_reason = None
        return driver
    except Exception as exc:
        _unavailable_reason = f"{type(exc).__name__}: {exc}"
        log.warning("Neo4j unavailable (%s) - callers will fall back",
                    _unavailable_reason)
        return None


def get_driver():
    global _driver
    if _driver is None:
        with _lock:
            if _driver is None:
                _driver = _make_driver()
    return _driver


def reset_driver() -> None:
    """Test hook, and how a seeding script picks up new settings."""
    global _driver, _warned
    with _lock:
        if _driver is not None:
            try:
                _driver.close()
            except Exception:
                pass
        _driver = None
        _warned = False
    get_db_settings.cache_clear()


def available() -> bool:
    driver = get_driver()
    if driver is None:
        return False
    try:
        driver.verify_connectivity()
        return True
    except Exception as exc:
        log.debug("Neo4j availability check failed: %s", exc)
        return False


def unavailable_reason() -> str | None:
    return _unavailable_reason


def run(cypher: str, params: dict[str, Any] | None = None) -> list[dict] | None:
    """Run one read query. Returns None -- not [] -- when Neo4j is unreachable.

    The distinction matters: an empty list is "the graph has no such stop" and
    a None is "there is no graph to ask", and only the second one should make a
    caller fall back to the bundled topology.
    """
    global _warned
    driver = get_driver()
    if driver is None:
        return None
    s = get_db_settings().neo4j
    try:
        with driver.session(database=s.database) as session:
            return [dict(record) for record in session.run(cypher, params or {})]
    except Exception as exc:
        if not _warned:
            _warned = True
            log.warning("Neo4j query failed (%s: %s) - falling back; further "
                        "failures log at DEBUG", type(exc).__name__, exc)
        else:
            log.debug("Neo4j query failed: %s", exc)
        return None


def write(cypher: str, params: dict[str, Any] | None = None) -> int:
    """Run one write query and return the number of records it produced.

    Only the seeding scripts write. The serving application reads.
    """
    driver = get_driver()
    if driver is None:
        raise Neo4jUnavailable(_unavailable_reason or "Neo4j is not configured")
    s = get_db_settings().neo4j
    with driver.session(database=s.database) as session:
        result = session.run(cypher, params or {})
        summary = result.consume()
        counters = summary.counters
        return (counters.nodes_created + counters.relationships_created
                + counters.properties_set)


def write_batch(cypher: str, rows: Sequence[dict], batch: int = 1000) -> int:
    """UNWIND a list of parameter maps through one statement, in batches.

    One `CREATE` per row means one round trip per row; `UNWIND $rows AS row`
    turns 1,442 relationships into two round trips. The Cypher stays readable
    because the pattern after the UNWIND is the same pattern a single-row
    CREATE would use.
    """
    driver = get_driver()
    if driver is None:
        raise Neo4jUnavailable(_unavailable_reason or "Neo4j is not configured")
    s = get_db_settings().neo4j
    total = 0
    with driver.session(database=s.database) as session:
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            result = session.run(cypher, {"rows": chunk})
            c = result.consume().counters
            total += c.nodes_created + c.relationships_created
    return total


class Neo4jUnavailable(RuntimeError):
    """Raised by the write path, which has no meaningful fallback."""
