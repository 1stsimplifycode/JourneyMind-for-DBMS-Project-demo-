// ---------------------------------------------------------------------------
// JourneyMind — Neo4j schema
//
// Neo4j is optionally schema-less (Unit 4, Lecture 50: "A schema is optional
// in Neo4j"). The two features that version 2.0 added and that are worth using
// are the ones below: a uniqueness constraint, which is the graph equivalent of
// a key constraint, and indexes on the properties nodes are actually looked up
// by.
//
// Run with:  cypher-shell -u neo4j -p <password> -f database/neo4j/schema.cypher
// ---------------------------------------------------------------------------

// A stop id is the business key. Two nodes with the same stop_id would be the
// same physical place recorded twice, and every traversal would then return
// duplicate paths. The constraint creates its own index, so stop_id needs no
// separate one.
CREATE CONSTRAINT stop_id_unique IF NOT EXISTS
FOR (s:Stop) REQUIRE s.stop_id IS UNIQUE;

// Looked up by kind whenever the application asks for "every metro station" or
// "every named place" — which is exactly how ride hubs are chosen.
CREATE INDEX stop_kind IF NOT EXISTS
FOR (s:Stop) ON (s.kind);

// The map draws one polyline per route, so the ordered stop sequence of a
// single route is a hot read. Indexing the relationship property is what keeps
// it from scanning every transit link in the corridor.
CREATE INDEX transit_route IF NOT EXISTS
FOR ()-[r:TRANSIT_LINK]-() ON (r.route_id);
