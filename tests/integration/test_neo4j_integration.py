"""Neo4j is really connected, really populated, and really queried by the app.

The application runs exactly two of the blocks in queries.cypher -- `ride_hubs`
and `route_topology`. Those are the two tested here as live paths. The rest of
the file is verification/demonstration Cypher, and `test_demonstration_queries`
covers it as such rather than pretending it is a product feature.
"""

import pytest

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------- connect
def test_connection_round_trips(neo4j):
    assert neo4j.run("RETURN 1 AS n")[0]["n"] == 1


def test_graph_model_is_what_the_documentation_claims(neo4j):
    labels = neo4j.run("CALL db.labels() YIELD label RETURN collect(label) AS l")[0]["l"]
    types = neo4j.run(
        "CALL db.relationshipTypes() YIELD relationshipType AS t "
        "RETURN collect(t) AS l")[0]["l"]
    assert set(labels) == {"Stop"}
    assert set(types) == {"ROAD_LINK", "TRANSIT_LINK", "TRANSFER_LINK"}


def test_graph_is_populated_and_well_formed(neo4j):
    assert neo4j.run("MATCH (s:Stop) RETURN count(s) AS n")[0]["n"] >= 100
    for rel in ("ROAD_LINK", "TRANSIT_LINK", "TRANSFER_LINK"):
        n = neo4j.run(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS n")[0]["n"]
        assert n >= 100, f"{rel} has only {n}"

    # Meaningful means connected and unduplicated, not merely numerous.
    assert neo4j.run(
        "MATCH (s:Stop) WHERE NOT (s)--() RETURN count(s) AS n")[0]["n"] == 0
    assert neo4j.run(
        "MATCH (s:Stop) WITH s.stop_id AS id, count(*) AS c "
        "WHERE c > 1 RETURN count(*) AS n")[0]["n"] == 0
    assert neo4j.run("MATCH (a)-[r]->(a) RETURN count(r) AS n")[0]["n"] == 0


def test_uniqueness_constraint_exists(neo4j):
    rows = neo4j.run("SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties "
                     "RETURN name, labelsOrTypes, properties")
    assert any(r["labelsOrTypes"] == ["Stop"] and r["properties"] == ["stop_id"]
               for r in rows), "the stop_id key constraint is missing"


# ------------------------------------------------------------- live app paths
def test_ride_hubs_query_answers(neo4j):
    """The degree question the router asks once, when it builds its graph."""
    from app.graph.neo4j_topology import ride_hubs

    hubs = ride_hubs()
    assert hubs, "Neo4j returned no ride hubs"
    assert len(hubs) >= 10
    assert all(isinstance(h, str) for h in hubs)

    # Every hub must be a real stop -- a hub the router cannot stand on is not
    # a hub, which is the reason builder.py filters the result.
    known = {r["s"] for r in neo4j.run("MATCH (s:Stop) RETURN s.stop_id AS s")}
    assert set(hubs) <= known


def test_ride_hubs_obey_the_documented_rule(neo4j):
    """Every metro station and named place, plus bus stops on 2+ routes."""
    from app.graph.neo4j_topology import ride_hubs

    hubs = set(ride_hubs())
    required = {r["s"] for r in neo4j.run(
        "MATCH (s:Stop) WHERE s.kind IN ['metro_station','place'] "
        "RETURN s.stop_id AS s")}
    assert required <= hubs, "a metro station or named place is missing"

    single_route = {r["s"] for r in neo4j.run(
        "MATCH (s:Stop)-[t:TRANSIT_LINK]-() WHERE s.kind = 'bus_stop' "
        "WITH s, count(DISTINCT t.route_id) AS routes WHERE routes < 2 "
        "RETURN s.stop_id AS s")}
    assert not (single_route & hubs), "a one-route bus stop was made a hub"


def test_route_topology_feeds_the_city_endpoint(neo4j, client):
    """GET /api/city draws its polylines from the ordered transit links."""
    from app.graph.neo4j_topology import route_topology

    topo = route_topology()
    assert topo, "Neo4j returned no route topology"
    assert len(topo) >= 5

    # Every rebuilt route must chain end to end, or the map draws a broken line.
    for route_id, stops in topo.items():
        assert len(stops) >= 2
        assert len(set(stops)) == len(stops), f"{route_id} repeats a stop"

    city = client.get("/api/city")
    assert city.status_code == 200
    served = {r["route_id"]: [s["node_id"] for s in r["stops"]]
              for r in city.json()["routes"]}
    shared = set(topo) & set(served)
    assert shared, "no route in /api/city matches the graph"
    for route_id in shared:
        assert served[route_id] == topo[route_id], \
            f"/api/city disagrees with Neo4j about route {route_id}"


# ------------------------------------------------ demonstration-only traversals
def test_demonstration_queries_run(neo4j):
    """queries.cypher blocks the APPLICATION does not run, but which must work.

    These back the graph explanation in DATABASES.md and the verifier's sample
    traversal. They are exercised here so the documentation cannot quietly
    describe Cypher that no longer parses.
    """
    from app.graph.neo4j_topology import cypher

    hops = neo4j.run(cypher("shortest_transit_path"),
                     {"from_id": "mg_majestic", "to_id": "mg_silkboard"})
    assert hops and hops[0]["hops"] >= 1
    assert len(hops[0]["stop_names"]) == hops[0]["hops"] + 1

    near = neo4j.run(cypher("reachable_within"), {"from_id": "mg_majestic"})
    assert near, "nothing is reachable from Majestic within three changes"

    inter = neo4j.run(cypher("interchanges"))
    assert inter and all(r["routes_served"] >= 2 for r in inter)

    corridors = neo4j.run(cypher("busiest_corridors"))
    assert corridors and all(r["route_km"] > 0 for r in corridors)
