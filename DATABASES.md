# Databases

JourneyMind uses three databases. Each one holds the data whose *access
pattern* it is good at, and each data category has exactly one authoritative
home. Some bundled network entities are projected into two stores for two
different query models — that overlap is deliberate and is set out in §5.

```
                              JourneyMind
                          (FastAPI + Next.js)
                                   |
          +------------------------+------------------------+
          |                        |                        |
        MySQL                    Redis                    Neo4j
          |                        |                        |
  structured relational    hot key-value and         the network as a
  records, queried by      short-lived state,        graph, queried by
  attribute and joined     read by key               how things connect
          |                        |                        |
  zones, booking history   geocode cache, live       stops, and the road /
  bookings this instance   sessions, cached          transit / transfer
  made, the audit trail    dashboards, hot audit     links between them
```

Every store is **optional at runtime**. The application starts, serves and
passes its 243-test unit suite with all three switched off; each integration
point falls back to the behaviour it had before the databases existed — with
one measured exception for free-text geocoding, set out honestly in §7.
`/health` reports which stores are actually live, so a reader can tell whether
the enterprise history came from MySQL or from the bundled CSV.

A separate layer, `tests/integration/` (25 tests), does the opposite: it
requires all three services and proves they are genuinely read and written by
real requests. It skips with a reason when a store is unreachable, so a skip
there means "not verified", never "passed".

---

## 1. Database responsibility matrix

Derived from the features the product already had — nothing here was invented
to give a database something to do.

| Existing feature / data | Store | Why that store | Volume |
|---|---|---|---|
| Pickup/drop zones the demand history refers to | MySQL | A dimension with real attributes (position, congestion) that 60,000 booking rows should not repeat | 105 rows |
| Enterprise booking history (the fact table behind the dashboard) | MySQL | "Total spend for Engineering at Sarjapur, excluding cancellations" is a filtered aggregate over 60,000 rows — SQL's own job | 60,000 rows |
| Bookings this instance actually made | MySQL | A durable business record: what was advertised, what was paid, what the failures cost | 130 rows, and grows |
| Each attempt inside a booking | MySQL | The child side of the one genuine one-to-many relationship; written in the same transaction as its parent | 231 rows, and grows |
| Every AI decision and its evidence | MySQL | A compliance question — every row, indexed by time and kind, still there next year | 371 rows, and grows |
| Place-name → coordinate lookups | Redis | One name in, one answer out; shared by every process; avoids a rate-limited third party | 222 keys |
| Booking sessions while the rider is still pressing buttons | Redis | Read by key, rewritten per retry, worthless in 45 minutes — a hash with a TTL | 130 keys, TTL-backed |
| Dashboard payloads an analyst asked for | Redis | ~40 aggregations over 44,876 rows reduced to one GET on the second visit | 111 keys, TTL-backed |
| "What has this system just been doing?" | Redis | A bounded activity log: LPUSH + LTRIM, constant memory, no cleanup job | 371 entries, capped at 500 |
| The multimodal network's vertices | Neo4j | Entities whose *connections* are the interesting part | 223 nodes |
| Streets, transit hops and walking transfers | Neo4j | The relationships **are** the data; traversal depth is a number in the pattern, not a recursive join | 1,442 relationships |

Counts are the **clean-seed** figures from `python database/verify.py`,
immediately after `python database/init_all.py` — see §8 for the full table and
for how each one moves afterwards.

---

## 2. MySQL — structured relational data

### What is stored

Five tables, defined in [`database/mysql/schema.sql`](database/mysql/schema.sql).

```
zones ────────────┐
  zone_id (PK)    │ FK: bookings.zone_id → zones.zone_id
  name, kind      │      ON DELETE RESTRICT, ON UPDATE CASCADE
  lat, lon        │
  congestion      │
                  ▼
bookings                         booking_sessions ────────┐
  booking_id (PK)                  session_id (PK)        │ FK: ON DELETE CASCADE
  ts, hour, dow, rain              created_at, departure  │
  provider_id, mode                provider_id, fares     │
  campus_id, employee_group        p_match/accept/cancel  │
  cost_centre                      settled, exhausted     │
  zone_id (FK) ────────────┘       demo                   │
  distance_km, pickup_km                                  ▼
  matched/accepted/                booking_attempts
  cancelled/completed                attempt_id (PK, AUTO_INCREMENT)
                                     session_id (FK)
audit_events                         attempt_no
  event_id (PK, AUTO_INCREMENT)      UNIQUE (session_id, attempt_no)
  at, kind, actor                    fare, outcome, succeeded
  request   JSON                     wasted_min, driver_name, eta_min
  decision  JSON
  model_versions JSON
  confidence
```

### Why this data is relational

It is *related by key* and *queried by attribute*. A booking names a zone; an
attempt belongs to a session. The dashboard's every question is a filtered
aggregate — group by campus, restrict to a date range, sum a column, exclude
cancellations. That is what a relational engine with indexes is built to do.

### Constraints, and what each one prevents

| Constraint | Prevents |
|---|---|
| `fk_bookings_zone` (RESTRICT) | A booking naming a zone that does not exist; a zone being deleted out from under 60,000 facts |
| `fk_attempts_session` (CASCADE) | Attempt rows outliving the session they describe |
| `uq_attempt_per_session` | Two rows claiming to be attempt #2 of the same booking |
| `chk_bookings_lifecycle` | A trip that completed without being accepted, or was accepted without being matched — the state machine in `lifecycle/states.py`, enforced by the database |
| `chk_sessions_terminal` | A session that is both settled and exhausted |
| `chk_bookings_dow`, `chk_bookings_hour`, `chk_zones_lat/lon` | Values outside their physical range |

`ON DELETE CASCADE` on attempts and `RESTRICT` on `bookings.zone_id` is not an
inconsistency: an attempt has no meaning without its session, whereas a booking
has plenty of meaning without its zone row.

### Indexes, and the query each one serves

| Index | Query |
|---|---|
| `idx_bookings_ts` | The dashboard's date-range filter |
| `idx_bookings_campus_provider` | The "one campus, one provider" drill-down, without a second lookup |
| `idx_bookings_group`, `idx_bookings_hour`, `idx_bookings_zone` | The remaining single-facet filters |
| `idx_audit_kind_at` | "Show me the recommendations from last Tuesday" |

### Transactions

`booking/mysql_sessions.py` writes a session and its attempts inside one
explicit transaction. A session with no attempts, or attempts with no session,
is a record of something that never happened — so both land or neither does
(Unit 4, Lecture 43: atomicity). The isolation level is left at MySQL's default
REPEATABLE READ, and `db/mysql.py::transaction()` accepts any of the four SQL
levels from Lecture 45 for callers that need something else.

### Why Redis would be the wrong primary store here

"Total spend for Engineering at the Sarjapur campus between two dates,
excluding cancelled trips" is a filtered aggregate over 60,000 rows. A
key-value store has no way to answer it except by reading every value and doing
the work in the client — which is to say, by not being a database for this.
There is also no key that question could be looked up by: the filters are
chosen at query time.

### Why Neo4j would be the wrong primary store here

Neo4j *could* hold these records. But nothing here is a path. No question the
dashboard asks traverses from one booking to another; they are independent
facts aggregated by attribute. Paying for relationship storage and getting no
traversal in return is the wrong trade.

### Why there is no `campuses` / `providers` / `employee_groups` table

Those columns have 5, 3 and 5 distinct values across 60,000 rows, carry no
further attributes, and have no integrity risk a `CHECK` does not cover. A
five-row dimension table would add a join to every query and buy nothing.
`zones` **is** normalised out, because it carries real attributes the booking
row has no business repeating 60,000 times. Normalising is a judgement, not a
reflex.

---

## 3. Redis — genuine key-value use

Full detail, including every key pattern and its value shape, is in
[`database/redis/keyspace.md`](database/redis/keyspace.md). In summary:

| Family | Structure | Value | TTL | Unit 4 use case |
|---|---|---|---|---|
| `jm:geo:<name>` | STRING | `[lat, lon, name]`, or `[]` for a cached miss | none | Caching (L49) |
| `jm:session:<id>` | HASH | A live booking session's fields | `REDIS_SESSION_TTL` = 45 min | Session storage (L49) |
| `jm:agg:<digest>` | STRING | A whole dashboard payload as JSON | `REDIS_AGGREGATE_TTL` = 1 day | Caching (L49) |
| `jm:audit:recent` | LIST | Newest decisions, LPUSH + LTRIM | none — bounded instead | Logging / task list (L49) |

`:` separates the hierarchy exactly as Lecture 49 teaches (`PES:Department`),
and every key is prefixed with `REDIS_PREFIX` so the app can share an instance.

### Why Redis rather than querying MySQL again

* **The dashboard cache.** One payload is ~40 aggregations over 44,876 rows. An
  analyst clicks campus → team → provider → back, and each click asks for a
  payload a previous click may already have computed. The second visit becomes
  a single `GET`.
* **The geocode cache.** The alternative is not MySQL, it is *Nominatim over
  the public internet* — rate-limited to one request a second, and a third
  party that can be slow or down. Every name in the study area is pre-loaded,
  so the common case never leaves the building.

  Worth being exact about which names actually reach Redis, because the
  resolver tries three sources in order (`api/routes.py::resolve_point` →
  `services/geocode.py`):

  1. **The 15 bundled places** — matched locally by name. `"Indiranagar"`
     resolves to *Indiranagar 100ft Road* here and **never touches Redis**.
  2. **Everything else in the corridor** — the other ~207 named stations, bus
     stops and junctions. These fall past the local match into `geocode()`,
     which reads `jm:geo:*` **first**. `"Cubbon Park"` and `"Trinity"` are the
     worked examples, verified by capturing the live `GET jm:geo:cubbon park`
     against a running server and covered by
     `tests/integration/test_redis_integration.py`.
  3. **Names the study area has never heard of** — a Redis miss, then the
     on-disk cache, then Nominatim.
* **Live sessions.** Fetched by key, rewritten on every retry, gone within the
  hour. In MySQL that is a table whose rows are all deleted within the hour,
  plus a cleanup job to do the deleting, on a storage engine built for
  durability nobody here needs.

### Invalidation

`jm:agg:*` expires by time, and only by time — correct here because the booking
history is an immutable record of things that already happened. `jm:session:*`
has its TTL reset on every write, so an active session does not expire under
the rider. `jm:audit:recent` is bounded by `LTRIM` rather than expiring, so
memory is constant and the newest entries are never the ones discarded.

### Why Neo4j is inappropriate for this

Nothing in this list is a path. A session is a record; a cached payload is a
blob; a coordinate is a value. Asking a graph database to store them would mean
paying for relationship machinery that nothing would traverse.

---

## 4. Neo4j — genuine graph use

### The model

```
(:Stop {stop_id, name, kind, lat, lon,
        is_interchange, degree,
        observed_congestion, latent_congestion})

(:Stop)-[:ROAD_LINK     {edge_id, distance_km, road_class,
                         free_speed_kmph, lanes}]->(:Stop)

(:Stop)-[:TRANSIT_LINK  {edge_id, route_id, route_name, mode, colour,
                         seq, direction, distance_km, scheduled_min}]->(:Stop)

(:Stop)-[:TRANSFER_LINK {edge_id, distance_km, walk_min}]->(:Stop)
```

A `:Stop` is any vertex the router can stand on — a metro station, a bus stop,
a road junction, or a named place the UI offers as From/To. Both traversal
directions of every link are stored, because that is what `graph/builder.py`
builds in memory: a street and a metro line are traversable each way. Keeping
the two graphs identical means Neo4j answers questions about the *same* network
the router actually searches.

### What is deliberately not a node label

Route, Place, Campus and Provider. The corridor has ten routes, fifteen named
places, five campuses and three providers. Making any of them a label would
create a label with an order of magnitude less data than the graph it sits in,
and every question they could answer is already answered by a property on the
relationship (`route_id`, `route_name`, `mode`) or by a column in MySQL. A
graph model is judged on whether the edges carry the meaning — not on how many
labels it can name.

### Which queries are live, and which are demonstrations

All of them live in [`database/neo4j/queries.cypher`](database/neo4j/queries.cypher),
and `graph/neo4j_topology.py` **loads them from that file at runtime** — so the
Cypher the product runs and the Cypher this document points at cannot drift.

**Only two of the ten blocks are executed by the running application.** The
rest are demonstration and verification Cypher. Saying otherwise would describe
product features that do not exist:

| Block | Executed by | Classification |
|---|---|---|
| `@ride_hubs` | `graph/builder.py` at graph build | **Live application query** |
| `@route_topology` | `GET /api/city` | **Live application query** |
| `@shortest_transit_path` | `database/verify.py`, integration tests | Verification / demonstration |
| `@reachable_within` | integration tests, this document | Demonstration |
| `@interchanges` | integration tests, this document | Demonstration |
| `@busiest_corridors` | integration tests, this document | Demonstration |
| `@counts_nodes` / `@counts_road` / `@counts_transit` / `@counts_transfer` | `database/verify.py` | Verification |

The demonstration blocks are not dead text: `tests/integration/test_neo4j_integration.py`
runs every one of them, so this document cannot end up describing Cypher that
no longer parses. They are simply not on a user's path.

### Is Neo4j load-bearing? No — and that is deliberate

Turning Neo4j off changes **nothing** a user sees. `@ride_hubs` returns 44 stop
ids, and the in-process rule in `graph/builder.py::_pick_ride_hubs` returns the
same 44; `@route_topology` rebuilds the sequences the bundle already contains.
The two graphs are the same graph on purpose — if they disagreed, Neo4j would
be answering questions about a different network from the one the router
searches.

That is a fallback, not a pretence, and it is not removed to manufacture a
dependency. The academic case for Neo4j here is **query-model suitability**,
not service dependence: "which stops are served by two or more distinct routes"
is a degree question over relationships, and it is one line of Cypher against
a nested Python loop over `route_count`. Which representation expresses the
question better is the interesting comparison — not which one the product would
fail without.

**`@ride_hubs`** — where a hailed vehicle can plausibly pick you up. The rule is
"every metro station, every named place, and every bus stop served by two or
more routes". That last clause is a *degree question about the network*, and it
is the reason this lives in a graph database:

```cypher
MATCH (s:Stop)
OPTIONAL MATCH (s)-[t:TRANSIT_LINK]-()
WITH s, count(DISTINCT t.route_id) AS routes_served
WHERE s.kind IN ['metro_station', 'place'] OR routes_served >= 2
RETURN s.stop_id AS stop_id
ORDER BY stop_id
```

**`@route_topology`** — each route as an ordered stop sequence, which is what
`GET /api/city` returns and what the map draws one polyline from. Rebuilt by
walking each route's forward relationships in `seq` order.

**`@shortest_transit_path`** *(demonstration block — not run by the application)*
— the traversal that answers "why a graph database and not a join?":

```cypher
MATCH (a:Stop {stop_id: $from_id}), (b:Stop {stop_id: $to_id})
MATCH p = shortestPath((a)-[:TRANSIT_LINK|TRANSFER_LINK*..15]-(b))
RETURN [n IN nodes(p) | n.name] AS stop_names, length(p) AS hops
```

Verified live: Majestic → Central Silk Board comes back in **11 hops**.

**`@interchanges`** and **`@reachable_within`** rank the stops where the network
actually connects, and answer "everywhere a stuck rider can still get to in at
most three changes".

### Why graph traversal is useful here

The depth of the search is *a number in the pattern*. The same question in SQL
is a recursive CTE over an edge table that has to re-derive the frontier at
every level, and "in either direction, along either of two relationship types,
up to fifteen hops" turns into a query that is substantially harder to read
than the network it describes.

### Why Neo4j is different from Redis

Redis answers "what is the value at this key?" in constant time and has no
concept of one record referring to another. Neo4j's entire value is that
records refer to each other and that following those references is cheap. They
are not competing options here — they hold different data because the product
asks two different kinds of question.

### Why not move the whole router into Neo4j?

The k-shortest-path search in `app/routing/` runs twenty Dijkstras per request
over integer-indexed NumPy arrays and finishes in tens of milliseconds. Moving
that into Neo4j would add a network round trip per expansion and make the
product *slower*. Neo4j gets the other kind of question — asked once, about the
shape of the network — and the caller caches the answer.

---

## 5. SQL vs NoSQL, in this application

| | MySQL | Redis | Neo4j |
|---|---|---|---|
| Data model | Tables, rows, typed columns | Key → value | Nodes and relationships |
| Schema | Fixed, enforced | None | Optional (L50) — constraints + indexes only |
| Queried by | Attribute, joined, aggregated | Key | Pattern and path |
| Consistency | ACID transactions | Single-command atomicity | ACID within a transaction |
| What it holds here | Facts that must survive and be aggregated | Data that is hot or short-lived | The shape of the network |
| If it is lost | Business records are gone | The product is slower | The bundled topology answers instead |

The dividing line in this project is not "old vs new" — it is **how the data is
asked for**. Attribute-and-aggregate goes to MySQL. Fetch-by-key goes to Redis.
Follow-the-connections goes to Neo4j.

### On overlap: purposeful polyglot representation, not zero duplication

Each data category has one **authoritative** representation, and each store is
queried for the access pattern it is good at. That is not the same as claiming
nothing appears twice, and it would be easy to disprove if it were:

| What overlaps | Where | Relationship | Is it a second source of truth? |
|---|---|---|---|
| The 105 zone/stop entities (`zone_id` == `stop_id`, same name, lat, lon, congestion) | MySQL `zones` **and** Neo4j `(:Stop)` | Both are **loaded independently from the same bundled files** (`data/mobility/zones.json`, `data/city/.../nodes.csv`) | **No.** The bundled file is the source of truth; both stores are projections of it, rebuilt by re-running the seeds. Neither is written at runtime. |
| A finished booking session | Redis `jm:session:*` **and** MySQL `booking_sessions` | Redis holds it while it is in flight and lets it expire; MySQL receives it once, when it reaches a terminal state | **No.** MySQL is authoritative. Redis is operational state with a TTL. |
| The newest audit entries | Redis `jm:audit:recent` **and** MySQL `audit_events` | Redis keeps a bounded tail of what MySQL keeps in full | **No.** MySQL is authoritative; the list is a hot cache of its head. |

So: **MySQL** maintains the structured relational representation, **Redis**
holds cached, derived and short-lived operational state, and **Neo4j**
maintains the traversal-oriented representation of the transport network. The
105 network entities present in both MySQL and Neo4j originate from the same
bundled source data and are modelled for *different query models* — a zone is a
foreign-key dimension that 60,000 booking rows aggregate over, a stop is a
vertex that relationships hang off. They are not competing independent sources
of truth, and neither is edited at runtime.

What is genuinely *not* duplicated is the runtime-written data: a booking's
durable record exists only in MySQL, a live session's mutable state only in
Redis, and no booking, fare or audit entry is ever written into Neo4j.

---

## 6. Unit 4 syllabus mapping

Every NoSQL concept used, and where it is taught.

| Concept used | Where in Unit 4 | Where in this project |
|---|---|---|
| NoSQL categories; key-value and graph are two of the four | L46 slide 12 | Redis is the key-value store, Neo4j the graph store |
| Redis is in-memory, used as cache / session store | L46 slide 6, L49 slides 3–4 | `db/redis_store.py` |
| **Caching** as a Redis use case | L49 slide 4 | `enterprise/cache.py`, `services/geocode.py` |
| **Session storage** as a Redis use case | L49 slide 4 | `booking/redis_sessions.py` |
| `:` for key hierarchy / namespacing | L49 slide 6 | `jm:session:<id>`, `redis_store.key()` |
| `EXPIRE key seconds`, `TTL key`, `DEL` | L49 slide 8 | Session TTL, aggregate TTL, `--flush` in the seeder |
| STRING: `SET key value EX seconds`, `GET` | L49 slides 9, 11 | `jm:geo:*`, `jm:agg:*` |
| HASH: `HSET … mapping`, `HGETALL` | L49 slides 9, 13 | `jm:session:*` |
| LIST: `LPUSH`, `LRANGE`, `LLEN`, `LTRIM` | L49 slides 9, 12 | `jm:audit:recent` |
| Redis limitation: not for complex joins or aggregations | L49 slide 5 | Exactly why the booking history is in MySQL |
| Graph = nodes + edges; edges are relationships | L50 slides 2, 6 | `(:Stop)`, `[:ROAD_LINK]` etc. |
| Nodes ≈ entities, labels ≈ entity types, properties ≈ attributes | L50 slide 7 | `:Stop` with 9 properties |
| Relationships are **directed**, have a **type**, and may carry properties | L50 slides 6, 11 | Both directions stored; `route_id`, `seq`, `scheduled_min` on `[:TRANSIT_LINK]` |
| A **path** is a traversal specified as a pattern | L50 slides 14–15 | `@shortest_transit_path`, `@reachable_within` |
| Optional schema: indexes and key constraints (v2.0 features) | L50 slides 16–17 | `database/neo4j/schema.cypher` |
| Built-in graph algorithms *named* — shortest path | L50 slide 3 (capability bullet; **syntax not shown**) | `shortestPath(...)` in `@shortest_transit_path` — demonstration only, see the deviation table below |
| Read clauses: `MATCH`, `OPTIONAL MATCH`, `WHERE` | L50 slide 29 | Every query in `queries.cypher` |
| Write clauses: `MERGE`, `SET`, `DELETE` | L50 slide 30, L51 slide 24 | `database/neo4j/seed.cypher` |
| General clauses: `RETURN`, `ORDER BY`, `LIMIT`, `WITH`, `UNWIND` | L50 slide 31 | `UNWIND $rows AS row` in the seed; `WITH` in `@ride_hubs` |
| `MERGE` creates only what is missing | L51 slide 24 | Re-running the seed produces the same graph, not a second copy |
| Filtering nodes by property and by boolean operators | L51 slides 20–21 | `WHERE s.kind IN [...]`, `WHERE r.direction = 'forward'` |
| ACID and transactions | L43 | `db/mysql.py::transaction()`, `booking/mysql_sessions.py` |
| Isolation levels | L45 | `transaction(isolation=...)`, defaulting to REPEATABLE READ |

### Everything used that Unit 4 does not explicitly show

An earlier version of this document claimed there were two. Searching the
supplied material directly — the four decks, the notes, and the clause-table
slide images — there are **seven**. All are declared here rather than hidden.
None is an additional NoSQL *concept*; every one is either driver/setup
mechanics or the production-safe spelling of an operation the syllabus does
teach.

| Used | Unit 4 status | Why it is here anyway | Classification |
|---|---|---|---|
| `SCAN` | Not present. L49 s18 teaches `KEYS *` | Same question, same answer, but non-blocking. The counting code runs against a keyspace of unknown size, where `KEYS` stalls every other client | Implementation of a taught operation |
| `DETACH DELETE` | Not present. L50 s30 teaches `DELETE`; L51 s5 notes `MATCH (n) DELETE n` works only "if there are no relationships in the graph" | That caveat is exactly the case the seed hits — a `:Stop` cannot be deleted while links point at it. `DETACH` is the named solution to the limitation the material describes | Implementation of a taught operation |
| Redis pipelines (`client.pipeline()`) | Not present | Groups `DEL`+`HSET`+`EXPIRE` (and `LPUSH`+`LTRIM`) into one round trip. Every command inside is taught; only the batching is not | Driver mechanics |
| Variable-length patterns `*..15`, `*1..3` | Not present. Paths *are* taught (L50 s14–15), the quantifier is not | Used only in `@shortest_transit_path` and `@reachable_within` — both **demonstration** blocks, not application code | Demonstration only |
| `shortestPath()` | Named as a capability on L50 s3 ("Built-in graph algorithms → shortest path"); the **function syntax is never shown** | Demonstration block only. The claim "Unit 4 names shortest path" is true; "Unit 4 teaches `shortestPath()`" is not | Demonstration only |
| `CREATE CONSTRAINT … REQUIRE … IS UNIQUE`, `CREATE INDEX … FOR … ON` | The **concepts** are taught (L50 s16–17: optional schema, indexes, "the equivalent of a key constraint"); the **syntax is never shown** | One-time schema setup. The concept being demonstrated is exactly the one L50 s16–17 describes | Setup syntax for a taught concept |
| Path functions and list comprehension — `nodes(p)`, `relationships(p)`, `length(p)`, `type(r)`, `[n IN nodes(p) \| n.name]` | Not present | Only shape the **return value** of the demonstration traversals | Demonstration only |

Four of the seven appear **only in demonstration blocks the application never
runs**, so the live Neo4j path (`@ride_hubs`, `@route_topology`) uses nothing
beyond `MATCH`, `OPTIONAL MATCH`, `WHERE`, `WITH`, `RETURN`, `ORDER BY`,
`count(DISTINCT …)` and `IN` — all of them on L50 slides 29–31 and L51.

Vector search (L55–57) is **not** used: nothing in this product is a similarity
query, and adding one to look sophisticated is exactly what the brief warns
against.

---

## 7. Failure behaviour

| Failure | What happens |
|---|---|
| MySQL unreachable | Enterprise history reads the bundled `data/mobility/bookings.csv`; finished bookings are not persisted (logged, not silently dropped); the audit trail falls back to Redis, then to the in-memory ring buffer |
| Redis unreachable | Sessions use the in-process TTL'd dictionary; dashboard payloads are recomputed per request; **geocoding degrades — see below** |
| Neo4j unreachable | Ride hubs and route topology come from the in-process graph built by `graph/builder.py` |
| Redis cache **miss** | Compute and store — ordinary cache-aside, not an error |
| Neo4j returns **empty** | Treated as "not seeded" and the bundle answers; `run()` returns `None` for "unreachable" and `[]` for "no such stop", and only the first triggers a fallback |
| Duplicate record | `REPLACE INTO` for seeded rows, `MERGE` in Cypher — re-running a seed is idempotent |
| Foreign-key violation | Refused by InnoDB and logged; this is the integrity working, not a bug to route around |
| Bad configuration | Each store reports itself unconfigured and the feature falls back; `/health` says which |

Nothing is silently swallowed. Redis helpers log the first failure at WARNING
and subsequent ones at DEBUG, so a flapping cache does not drown the log while
still being visible. One optional caching call failing never takes an unrelated
page down — that is the entire point of the fallback table above.

### Geocoding with Redis down: not equivalent, and the difference is visible

This is the one place where losing a store changes what a user sees, so it is
worth stating precisely rather than claiming a clean fallback. Measured against
a running instance with `JM_REDIS_ENABLED=0`:

| Typed name | Redis up | Redis down |
|---|---|---|
| One of the 15 bundled places (`Indiranagar`, `Majestic Bus Station`, …) | Resolved locally | **Identical** — Redis was never involved |
| A `place_id` from the From/To dropdowns | Resolved locally | **Identical** |
| A corridor name in the on-disk cache (`Whitefield`, `BTM Layout`) | Redis hit | Same answer, from `data/_geocode_cache.json` |
| A corridor name **only** in Redis (`Cubbon Park`) | The corridor's own node coordinate | **Different coordinate** — falls through to Nominatim, which returns its own nearby match |
| A corridor name Nominatim cannot place (`Trinity`) | Resolved from Redis | **HTTP 422 `unknown_place`** — the rider is told the study area does not know that name |
| No network either | — | Every name in rows 4–5 fails with 422 |

So the honest statement is: **the dropdown and the fifteen named places are
unaffected; free-text names outside them degrade, and some of them stop
resolving.** Redis is a genuine dependency for that one feature rather than a
pure optimisation. It is still not a dependency for booking, comparison, the
map, the dashboard or the audit trail, all of which are unaffected.

---

## 8. Data volume verification

> Run `python database/verify.py`. Every number below is a `COUNT(*)`, a
> `SCAN` count, an `LLEN` or a Cypher `count()` executed against the running
> database — nothing is stored, remembered or inferred from a file size.

```
$ python database/verify.py
```

| Technology | Structure | Count | Requirement | Result |
|---|---|---:|---:|---|
| MySQL | `zones` | 105 | >=100 | **PASS** |
| MySQL | `bookings` | 60,000 | >=100 | **PASS** |
| MySQL | `booking_sessions` | 230 | >=100 | **PASS** |
| MySQL | `booking_attempts` | 405 | >=100 | **PASS** |
| MySQL | `audit_events` | 763 | >=100 | **PASS** |
| Redis | cached place lookups (`jm:geo:*`) | 222 | >=100 | **PASS** |
| Redis | live booking sessions (`jm:session:*`) | 312 | >=100 | **PASS** |
| Redis | cached dashboard payloads (`jm:agg:*`) | 111 | >=100 | **PASS** |
| Redis | recent decisions (`LLEN jm:audit:recent`) | 500 | >=100 | **PASS** |
| Neo4j | `(:Stop)` nodes | 223 | >=100 | **PASS** |
| Neo4j | `[:ROAD_LINK]` relationships | 1,052 | >=100 | **PASS** |
| Neo4j | `[:TRANSIT_LINK]` relationships | 174 | >=100 | **PASS** |
| Neo4j | `[:TRANSFER_LINK]` relationships | 216 | >=100 | **PASS** |

**All 13 structures meet the 100+ requirement.** `verify.py` exited `0`.

This is a snapshot taken immediately after `python database/init_all.py`.
**Counts move in both directions afterwards, and two Redis families fall to
zero on their own.** Re-run the script rather than trusting this table; the
script is the claim, this is a record of it.

| Structure | After the documented seed | What happens next |
|---|---|---|
| MySQL `zones`, `bookings` | 105 / 60,000 | Fixed. Loaded from the bundled files. |
| MySQL `booking_sessions`, `booking_attempts`, `audit_events` | 130 / 231 / 371 | **Grow** — the application appends as it is used. |
| Neo4j nodes and relationships | 223 / 1,442 | Fixed. Rebuilt only by re-seeding. |
| Redis `jm:geo:*` | 222 | Persistent — no TTL. |
| Redis `jm:audit:recent` | 371 | Grows to the `LTRIM` cap (500), then stays there. |
| Redis `jm:session:*` | 130 | **Falls to 0 after 7 days** (`REDIS_DEMO_TTL`); live sessions expire in 45 minutes. |
| Redis `jm:agg:*` | 111 | **Falls to 0 after 1 day** (`REDIS_AGGREGATE_TTL`). |

The last two are supposed to do that. A booking session is worthless an hour
after the rider stops pressing buttons, and a cached aggregate goes stale —
expiry is the correct behaviour for both, not data loss, and nothing in MySQL
is affected. Removing the TTLs to keep a count above 100 would make the design
worse, so the reproducibility is handled where it belongs, in the seed:

```bash
python database/init_all.py --warm    # restores jm:session:* and jm:agg:*, then verifies
```

`verify.py` says so itself when either family is short, rather than leaving a
reader to guess whether a low count means "expired" or "never seeded".

The same run also asserts the data is *usable* rather than merely present:

```
referential integrity: 0 bookings with no zone, 0 attempts with no session
                       (both zero, as the foreign keys require)
disconnected (:Stop) nodes: 0 — every node is on the network
sample traversal (Majestic -> Silk Board): 11 hops,
    Majestic (Kempegowda) -> Chickpete -> Krishna Rajendra Market -> ...
TTL sample: jm:session:bk_dUbIygJpxhYg expires in 1153 s
```

Those four lines are the answer to "are these 100+ rows *meaningful*?" — no
orphaned foreign keys, no node sitting outside the graph, a real traversal
returning a real path, and a session key that genuinely expires.

`verify.py` exits `0` when everything passes, `1` when a structure is short,
and `2` when a store could not be reached — an unknown is reported as an
unknown, never as a pass.

### Where the data comes from

No row anywhere is `Test1 … Test100`.

| Structure | Source |
|---|---|
| `zones`, `bookings` | `data/mobility/*` — the files the product has always shipped with, loaded by `seed_mysql.py`. Not re-expressed as INSERT statements, because that would put a second, immediately-divergent copy of the same facts under version control |
| `(:Stop)` and all three link types | `data/city/bengaluru_south/*.csv` — the same files `graph/builder.py` reads, so the graph in Neo4j and the graph in memory are the same object |
| `jm:geo:*` | Every named vertex of that network, so the geocoder resolves the corridor's own names offline |
| `booking_sessions`, `booking_attempts`, `audit_events`, `jm:session:*`, `jm:agg:*`, `jm:audit:recent` | **Produced by running the product.** `seed_demo_traffic.py` drives the real `/api/compare`, `/api/book`, `/api/book/{id}/retry` and `/api/enterprise/overview` handlers in-process. Nothing constructs a row directly, so there is no seeding-only code path that could drift from the serving one |

The booking *outcomes* are genuine draws from the reliability model's own
probabilities, not fixed — forcing 130 identical failures would misrepresent
the very distribution the product exists to measure. Every sixth trip is booked
with `demo: true`, which fixes the dice for a reproducible stage demo; those
rows carry `demo = TRUE` in MySQL so seeded rows are always distinguishable
from rows a person created.

---

## 9. Setup

All three databases are optional; skip any block and the app still runs.

```bash
# 0. Python dependencies. The three database drivers are in requirements.txt,
#    so a fresh virtualenv can reach a configured database.
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt     # or requirements-dev.txt

# 1. Configure. Never commit real credentials — .env is gitignored.
cp .env.example .env
#    Fill in MYSQL_PASSWORD / REDIS_PASSWORD / NEO4J_PASSWORD.

# 2. Start MySQL, Redis and Neo4j.
#    On Windows, portable no-installer copies on non-default ports:
powershell -ExecutionPolicy Bypass -File scripts/databases.ps1 -Action setup
powershell -ExecutionPolicy Bypass -File scripts/databases.ps1 -Action start
#    Elsewhere, use your own instances and point .env at them.

# 3. Initialise all three stores AND verify, in one documented command.
python database/init_all.py
#    Runs, in order: seed_mysql.py, seed_neo4j.py, seed_redis.py,
#    seed_demo_traffic.py, then verify.py as a separate process.

# 4. Frontend. The build output is NOT committed, so this step is required
#    before the backend can serve the UI.
cd frontend && npm install && npm run build && cd ..
#    → writes backend/app/static/ (index.html + _next/)

# 5. Backend. Serves the API and the built UI from one origin.
cd backend && uvicorn app.main:app --port 8011      # → http://127.0.0.1:8011
```

**Later, when the TTL-backed Redis families have expired** (`jm:agg:*` after a
day, `jm:session:*` after a week), one command brings them back and re-verifies:

```bash
python database/init_all.py --warm
```

Two processes with hot reload instead of step 5:

```bash
cd backend   && uvicorn app.main:app --port 8011 --reload
cd frontend  && npm run dev            # → http://127.0.0.1:5173, proxies /api
```

### Verifying and testing

```bash
python database/verify.py            # the count report; exit 0 / 1 / 2
pytest tests -q                      # 243 unit + API tests (no DB required)
pytest tests/integration -v          # 25 tests that REQUIRE all three stores
```

`init_all.py --reset` drops the MySQL tables first. The individual scripts can
still be run alone: `seed_neo4j.py --keep` leaves the existing subgraph alone,
`seed_redis.py --flush` removes this application's keys before seeding. The
file-derived seeds are idempotent — run them twice and the database is the
same, because it is derived from the same source both times. `seed_demo_traffic.py`
appends, because it works by actually running the product.

### Environment variables

Every value is read from the environment; nothing is hard-coded to a host, a
port or a password. See `.env.example` for the annotated list.

```
MYSQL_HOST  MYSQL_PORT  MYSQL_DATABASE  MYSQL_USER  MYSQL_PASSWORD  MYSQL_POOL_SIZE
REDIS_HOST  REDIS_PORT  REDIS_PASSWORD  REDIS_DB    REDIS_PREFIX
            REDIS_SESSION_TTL  REDIS_DEMO_TTL  REDIS_AGGREGATE_TTL  REDIS_AUDIT_LIST_MAX
NEO4J_URI   NEO4J_USERNAME  NEO4J_PASSWORD  NEO4J_DATABASE
```

`JM_MYSQL_ENABLED=0`, `JM_REDIS_ENABLED=0` and `JM_NEO4J_ENABLED=0` each make
the application behave as if that store were not installed at all — which is
how the fallback paths in §7 are exercised.

---

## 10. Tracing a request end to end

```
Rider presses BOOK NOW
    ↓  Next.js  src/Book.jsx  →  src/api.js  bookRide()
    ↓  POST /api/book
    ↓  api/booking.py :: book()
    ↓      resolve_point()  →  services/geocode.py
    ↓                              → Redis GET jm:geo:<name>          [cache]
    ↓      engine.compare()  →  routing/  (in-process NumPy graph)
    ↓                              ↑ ride hubs came from Neo4j @ride_hubs
    ↓      run_next_attempt(session)
    ↓      _save(session)
    ↓          → Redis HSET jm:session:<id> + EXPIRE                  [live]
    ↓          → MySQL  INSERT booking_sessions + booking_attempts    [durable,
    ↓                   in one transaction, only once terminal]        one txn]
    ↓      audit(...)
    ↓          → Redis LPUSH jm:audit:recent + LTRIM 0 499            [hot]
    ↓          → MySQL INSERT audit_events                            [all]
    ↓
    └→ JSON response → Book.jsx renders the attempt
```

Every arrow above is a real call in the code, and every one of the database
arrows is optional: remove any store and the line simply does not happen, while
the ones above and below it still do.
