"""Network questions the application asks Neo4j.

Two of them, both about CONNECTIVITY rather than about rows:

    ride_hubs()        which stops are worth hailing a vehicle to, which
                       depends on how many distinct routes touch each stop
    route_topology()   each transit route as an ordered sequence of stops,
                       rebuilt by walking its forward relationships

WHY NOT THE WHOLE ROUTER
------------------------
The k-shortest-path search in `app/routing/` runs twenty Dijkstras per request
over integer-indexed arrays and finishes in tens of milliseconds. Moving that
into Neo4j would add a network round trip per expansion and make the product
slower for no gain: the search needs the *same* small graph thousands of times
in a second, which is the one access pattern an in-process array wins outright.

What Neo4j is for here is the other kind of question -- the ones asked once,
about the shape of the network, where the answer is a property of how things
connect. Those are the two above, and both of them are cached by the caller.

EVERY FUNCTION CAN RETURN None
------------------------------
None means "Neo4j had nothing to say" -- unreachable, unseeded, or answering
something that does not match the bundled topology. Callers fall back to the
in-process graph, which is the behaviour they had before this module existed.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from ..db import neo4j_store

log = logging.getLogger("journeymind.graph.neo4j")

#: The Cypher lives in database/neo4j/queries.cypher, beside the schema and the
#: seed, so the queries the product runs are the queries the documentation
#: shows. Loading them from disk is what makes that a guarantee rather than a
#: promise to keep two copies in step.
QUERIES_PATH = (Path(__file__).resolve().parents[3]
                / "database" / "neo4j" / "queries.cypher")


@lru_cache(maxsize=1)
def _blocks() -> dict[str, str]:
    """Parse `// @name` blocks out of queries.cypher."""
    try:
        text = QUERIES_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("cannot read %s (%s) — Neo4j-backed lookups are disabled",
                    QUERIES_PATH, exc)
        return {}
    out: dict[str, str] = {}
    name: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^//\s*@(\w+)\s*$", line)
        if m:
            if name:
                out[name] = "\n".join(buf).strip()
            name, buf = m.group(1), []
            continue
        if name is not None and not line.lstrip().startswith("//"):
            buf.append(line)
    if name:
        out[name] = "\n".join(buf).strip()
    return {k: v for k, v in out.items() if v}


def cypher(name: str) -> str | None:
    return _blocks().get(name)


# --------------------------------------------------------------------------
def ride_hubs() -> list[str] | None:
    """Stop ids a hailed vehicle can pick up from or drop at.

    Returns None when Neo4j is unreachable, and None when the graph answers
    with nothing -- an empty corridor is not a plausible answer and almost
    always means the graph has not been seeded, so falling back is safer than
    serving a product with no ride hubs at all.
    """
    q = cypher("ride_hubs")
    if q is None:
        return None
    rows = neo4j_store.run(q)
    if not rows:
        return None
    hubs = sorted({str(r["stop_id"]) for r in rows if r.get("stop_id")})
    log.info("ride hubs from Neo4j: %d", len(hubs))
    return hubs or None


def route_topology() -> dict[str, list[str]] | None:
    """route_id -> ordered stop ids, rebuilt from the forward relationships.

    Each route's forward links are already ordered by `seq`, so the sequence is
    the `from_id` of every hop plus the `to_id` of the last one.
    """
    q = cypher("route_topology")
    if q is None:
        return None
    rows = neo4j_store.run(q)
    if not rows:
        return None

    by_route: dict[str, list[tuple[int, str, str]]] = {}
    for r in rows:
        rid = str(r["route_id"])
        by_route.setdefault(rid, []).append(
            (int(r["seq"]), str(r["from_id"]), str(r["to_id"])))

    out: dict[str, list[str]] = {}
    for rid, hops in by_route.items():
        hops.sort(key=lambda h: h[0])
        # A route whose hops do not chain end to end -- where one hop's
        # destination is not the next hop's origin -- is a rebuild this code
        # got wrong. Say so and let the caller use the bundled sequence rather
        # than draw a broken line on the map.
        if any(a[2] != b[1] for a, b in zip(hops, hops[1:])):
            log.warning("route %s does not chain in Neo4j - using the bundle", rid)
            continue
        out[rid] = [h[1] for h in hops] + [hops[-1][2]]
    return out or None
