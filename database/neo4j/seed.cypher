// ---------------------------------------------------------------------------
// JourneyMind — Neo4j seed statements
//
// These are the exact statements `database/seed_neo4j.py` runs; the script
// reads this file rather than holding a second copy of the Cypher. Each block
// is named by the `// @name` line above it and is parameterised with $rows,
// a list of maps built from the bundled study-area files:
//
//     data/city/<city>/nodes.csv           -> (:Stop)
//     data/city/<city>/road_edges.csv      -> [:ROAD_LINK]
//     data/city/<city>/transit_edges.csv   -> [:TRANSIT_LINK]
//     data/city/<city>/transfer_edges.csv  -> [:TRANSFER_LINK]
//
// WHY UNWIND AND NOT ONE CREATE PER ROW
// -------------------------------------
// 1,442 separate CREATE statements are 1,442 round trips. `UNWIND $rows AS row`
// sends the whole batch once and the pattern after it is the same pattern a
// single-row CREATE would use, so nothing is lost in readability.
//
// WHY MERGE AND NOT CREATE
// ------------------------
// Re-running the seed must produce the same graph, not a second copy of it.
// MERGE matches on the business key and creates only what is missing (Unit 4,
// Lecture 51).
// ---------------------------------------------------------------------------

// @wipe
// Detach-delete only what this application owns. `MATCH (n) DETACH DELETE n`
// would take any other graph in the same database with it.
MATCH (s:Stop) DETACH DELETE s

// @stops
// One node per vertex the router can stand on: metro stations, bus stops,
// road junctions and the named places the UI offers as From / To.
// `degree` and `observed_congestion` are the features the travel-time model
// was trained on, kept on the node so a traversal can rank by them without
// going back to another store.
UNWIND $rows AS row
MERGE (s:Stop {stop_id: row.stop_id})
SET s.name                = row.name,
    s.kind                = row.kind,
    s.lat                 = row.lat,
    s.lon                 = row.lon,
    s.is_interchange      = row.is_interchange,
    s.degree              = row.degree,
    s.observed_congestion = row.observed_congestion,
    s.latent_congestion   = row.latent_congestion

// @road_links
// A street segment, walkable in either direction. Both directions are stored
// because graph/builder.py builds both: the router holds two directed edges
// per street and the graph database should not disagree with it.
UNWIND $rows AS row
MATCH (u:Stop {stop_id: row.u})
WITH row, u
MATCH (v:Stop {stop_id: row.v})
MERGE (u)-[r:ROAD_LINK {edge_id: row.edge_id, u: row.u, v: row.v}]->(v)
SET r.distance_km     = row.distance_km,
    r.road_class      = row.road_class,
    r.free_speed_kmph = row.free_speed_kmph,
    r.lanes           = row.lanes

// @transit_links
// One stop to the next on a metro or bus route. The route is a PROPERTY, not
// a node: ten routes would make a ten-node label, and `route_id` on the
// relationship answers every question a (:Route) node could.
// `seq` preserves the running order, which is what lets the map redraw a line
// from the graph.
UNWIND $rows AS row
MATCH (u:Stop {stop_id: row.u})
WITH row, u
MATCH (v:Stop {stop_id: row.v})
MERGE (u)-[r:TRANSIT_LINK {edge_id: row.edge_id, u: row.u, v: row.v}]->(v)
SET r.route_id      = row.route_id,
    r.route_name    = row.route_name,
    r.mode          = row.mode,
    r.colour        = row.colour,
    r.seq           = row.seq,
    r.direction     = row.direction,
    r.distance_km   = row.distance_km,
    r.scheduled_min = row.scheduled_min

// @transfer_links
// A walk between two nearby transit nodes — the metro exit to the bus stop.
// This is the relationship that makes "Walk then Metro then Bus" a path rather
// than a special case somebody had to code.
UNWIND $rows AS row
MATCH (u:Stop {stop_id: row.u})
WITH row, u
MATCH (v:Stop {stop_id: row.v})
MERGE (u)-[r:TRANSFER_LINK {edge_id: row.edge_id, u: row.u, v: row.v}]->(v)
SET r.distance_km = row.distance_km,
    r.walk_min    = row.walk_min
