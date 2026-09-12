// ---------------------------------------------------------------------------
// JourneyMind — the Cypher this project runs.
//
// The first two blocks are READ BY THE APPLICATION at runtime:
// `backend/app/graph/neo4j_topology.py` loads them from this file by name, so
// the queries the product runs and the queries the documentation shows are
// the same text. The rest are the traversals `database/verify.py` executes and
// the ones ARCHITECTURE.md walks through.
//
// Everything here stays inside the Cypher taught in Unit 4, Lectures 50 and
// 51: MATCH / WHERE / RETURN / ORDER BY / LIMIT, node labels and properties,
// relationship types and their properties, paths, MERGE, and the built-in
// shortest-path algorithm named on Lecture 50 slide 3.
// ---------------------------------------------------------------------------

// @ride_hubs
// WHERE A HAILED VEHICLE CAN PLAUSIBLY PICK YOU UP OR DROP YOU.
//
// Run once when the router's graph is built; the answer shapes every
// recommendation, because it decides which stops a bike taxi is allowed to
// run you to on the way to a train.
//
// The rule is "every metro station, every named place, and every bus stop
// served by two or more routes". That last clause is a DEGREE QUESTION about
// the network — how many distinct routes touch this vertex — and it is the
// reason this lives in a graph database. In MySQL it is a GROUP BY over a
// transit-edge table with a DISTINCT on the route; here the relationships are
// the answer.
MATCH (s:Stop)
OPTIONAL MATCH (s)-[t:TRANSIT_LINK]-()
WITH s, count(DISTINCT t.route_id) AS routes_served
WHERE s.kind IN ['metro_station', 'place'] OR routes_served >= 2
RETURN s.stop_id AS stop_id
ORDER BY stop_id

// @route_topology
// EVERY TRANSIT ROUTE, AS AN ORDERED SEQUENCE OF STOPS.
//
// What GET /api/city returns and what the map draws one polyline from. Only
// the forward running direction is walked, so the outbound sequence comes back
// once rather than there and back again.
MATCH (u:Stop)-[r:TRANSIT_LINK]->(v:Stop)
WHERE r.direction = 'forward'
RETURN r.route_id AS route_id,
       r.seq      AS seq,
       u.stop_id  AS from_id,
       v.stop_id  AS to_id
ORDER BY route_id, seq

// @interchanges
// THE STOPS WHERE THE NETWORK ACTUALLY CONNECTS.
//
// Ranked by how many distinct routes meet there, then by how many walking
// transfers reach them. A rider changing vehicles does it at one of these, and
// the top of this list is where a disruption hurts most.
MATCH (s:Stop)-[t:TRANSIT_LINK]-()
WITH s, count(DISTINCT t.route_id) AS routes_served
WHERE routes_served >= 2
OPTIONAL MATCH (s)-[:TRANSFER_LINK]-(n:Stop)
RETURN s.stop_id AS stop_id, s.name AS name, s.kind AS kind,
       routes_served, count(DISTINCT n) AS walkable_neighbours
ORDER BY routes_served DESC, walkable_neighbours DESC, name

// @shortest_transit_path
// THE FEWEST HOPS FROM ONE STOP TO ANOTHER, RIDING AND CHANGING.
//
// `shortestPath` is the built-in graph algorithm Lecture 50 names. The pattern
// says: any number of TRANSIT_LINK or TRANSFER_LINK relationships, up to
// fifteen, in either direction.
//
// This is the query that answers "why a graph database and not a join?". The
// same question in SQL is a recursive CTE over an edge table that has to
// re-derive the frontier at every depth; here the depth is a number in the
// pattern.
MATCH (a:Stop {stop_id: $from_id}), (b:Stop {stop_id: $to_id})
MATCH p = shortestPath((a)-[:TRANSIT_LINK|TRANSFER_LINK*..15]-(b))
RETURN [n IN nodes(p) | n.name]           AS stop_names,
       [r IN relationships(p) | type(r)]  AS hop_types,
       length(p)                          AS hops

// @reachable_within
// EVERYWHERE YOU CAN GET TO FROM HERE IN AT MOST $hops CHANGES.
//
// The escalation question, asked of the network: a rider is stuck at a stop
// and wants to know what is still open to them. A variable-length pattern
// walks it directly; the equivalent SQL is a self-join per hop.
MATCH (start:Stop {stop_id: $from_id})
MATCH (start)-[:TRANSIT_LINK|TRANSFER_LINK*1..3]-(reached:Stop)
WHERE reached.stop_id <> start.stop_id
RETURN DISTINCT reached.stop_id AS stop_id, reached.name AS name,
       reached.kind AS kind
ORDER BY kind, name

// @busiest_corridors
// THE LONGEST SCHEDULED HOPS ON THE NETWORK, BY MODE.
//
// A property query rather than a traversal, kept here because it is the
// cheapest way to sanity-check that relationship properties survived the seed.
MATCH ()-[r:TRANSIT_LINK]->()
WHERE r.direction = 'forward'
RETURN r.mode AS mode, r.route_name AS route,
       round(sum(r.distance_km), 2) AS route_km,
       count(r) AS hops
ORDER BY route_km DESC

// @counts_nodes
// Data-volume verification: how many Stop nodes exist.
MATCH (n:Stop) RETURN count(n) AS n

// @counts_road
MATCH ()-[r:ROAD_LINK]->() RETURN count(r) AS n

// @counts_transit
MATCH ()-[r:TRANSIT_LINK]->() RETURN count(r) AS n

// @counts_transfer
MATCH ()-[r:TRANSFER_LINK]->() RETURN count(r) AS n
