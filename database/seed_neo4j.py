"""Build the study-area network in Neo4j from the bundled city files.

    python database/seed_neo4j.py [--keep]

The graph that comes out of this is the same graph `app/graph/builder.py`
builds in memory: the same vertices, the same two traversal directions per
link, the same properties. That is deliberate. If the two disagreed, every
answer Neo4j gave about the network would be an answer about a different
network from the one the router actually searches.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import _bootstrap
from _bootstrap import banner, city_dir, setup

CYPHER_DIR = Path(__file__).resolve().parent / "neo4j"


def load_blocks(path: Path) -> dict[str, str]:
    """Parse a .cypher file into `// @name` -> statement."""
    blocks: dict[str, str] = {}
    name: str | None = None
    buf: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^//\s*@(\w+)\s*$", line)
        if m:
            if name:
                blocks[name] = "\n".join(buf).strip()
            name, buf = m.group(1), []
            continue
        if name is not None and not line.lstrip().startswith("//"):
            buf.append(line)
    if name:
        blocks[name] = "\n".join(buf).strip()
    return {k: v for k, v in blocks.items() if v}


# --------------------------------------------------------------------------
def read_stops() -> list[dict]:
    rows = []
    with open(city_dir() / "nodes.csv", newline="", encoding="utf-8") as fh:
        for n in csv.DictReader(fh):
            rows.append({
                "stop_id": n["node_id"],
                "name": n["name"],
                "kind": n["kind"],
                "lat": float(n["lat"]),
                "lon": float(n["lon"]),
                "is_interchange": n["is_interchange"] == "1",
                "degree": int(n["degree"] or 0),
                "observed_congestion": float(n["observed_congestion"] or 0.0),
                "latent_congestion": float(n["latent_congestion"] or 0.0),
            })
    return rows


def _both_ways(base: dict, u: str, v: str) -> list[dict]:
    """The two directed rows a single undirected link becomes.

    `edge_id` stays the same on both, so "which street is this" survives, and
    the MERGE key includes u and v so the two directions are two relationships
    rather than one overwriting the other.
    """
    a = {**base, "u": u, "v": v}
    b = {**base, "u": v, "v": u}
    return [a, b]


def read_road_links() -> list[dict]:
    out: list[dict] = []
    with open(city_dir() / "road_edges.csv", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out += _both_ways({
                "edge_id": r["edge_id"],
                "distance_km": float(r["distance_km"]),
                "road_class": r["road_class"],
                "free_speed_kmph": float(r["free_speed_kmph"]),
                "lanes": int(r["lanes"] or 1),
            }, r["u"], r["v"])
    return out


def read_transit_links() -> list[dict]:
    routes = {r["route_id"]: r for r in json.loads(
        (city_dir() / "transit_routes.json").read_text(encoding="utf-8"))}
    out: list[dict] = []
    with open(city_dir() / "transit_edges.csv", newline="", encoding="utf-8") as fh:
        for t in csv.DictReader(fh):
            route = routes.get(t["route_id"], {})
            base = {
                "edge_id": t["edge_id"],
                "route_id": t["route_id"],
                "route_name": route.get("name", t["route_id"]),
                "mode": t["mode"],
                "colour": route.get("colour", "#666666"),
                "seq": int(t["seq"]),
                "distance_km": float(t["distance_km"]),
                "scheduled_min": float(t["scheduled_min"]),
            }
            # A metro line runs both ways; `direction` records which running
            # direction this relationship is, so the map can rebuild the
            # outbound sequence without picking up the return leg as well.
            fwd = {**base, "direction": "forward", "u": t["u"], "v": t["v"]}
            rev = {**base, "direction": "reverse", "u": t["v"], "v": t["u"]}
            out += [fwd, rev]
    return out


def read_transfer_links() -> list[dict]:
    out: list[dict] = []
    with open(city_dir() / "transfer_edges.csv", newline="", encoding="utf-8") as fh:
        for t in csv.DictReader(fh):
            out += _both_ways({
                "edge_id": t["edge_id"],
                "distance_km": float(t["distance_km"]),
                "walk_min": float(t["walk_min"]),
            }, t["u"], t["v"])
    return out


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep", action="store_true",
                    help="do not delete the existing :Stop subgraph first")
    args = ap.parse_args()

    setup()
    banner("Neo4j — the study-area network graph")

    from app.db import neo4j_store

    neo4j_store.reset_driver()
    if not neo4j_store.available():
        print(f"  Neo4j is not reachable: {neo4j_store.unavailable_reason()}",
              file=sys.stderr)
        return 1

    # Constraints and indexes first: MERGE on an unindexed property scans every
    # node, which turns a 1,442-relationship load into a quadratic one.
    for stmt in [s for s in re.split(r";\s*\n", (CYPHER_DIR / "schema.cypher")
                                     .read_text(encoding="utf-8")) if s.strip()]:
        cleaned = "\n".join(l for l in stmt.splitlines()
                            if not l.lstrip().startswith("//")).strip()
        if cleaned:
            neo4j_store.write(cleaned)
    print("  constraints and indexes applied from neo4j/schema.cypher")

    blocks = load_blocks(CYPHER_DIR / "seed.cypher")

    if not args.keep:
        neo4j_store.write(blocks["wipe"])
        print("  existing :Stop subgraph removed")

    stops = read_stops()
    neo4j_store.write_batch(blocks["stops"], stops)
    print(f"  (:Stop) nodes: {len(stops)}")

    for name, reader, label in (
        ("road_links", read_road_links, "[:ROAD_LINK]"),
        ("transit_links", read_transit_links, "[:TRANSIT_LINK]"),
        ("transfer_links", read_transfer_links, "[:TRANSFER_LINK]"),
    ):
        rows = reader()
        neo4j_store.write_batch(blocks[name], rows)
        print(f"  {label} relationships: {len(rows)} "
              f"({len(rows) // 2} links, both traversal directions)")

    print("\n  done. database/neo4j/queries.cypher holds the traversals the")
    print("  application runs and the ones the documentation walks through.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
