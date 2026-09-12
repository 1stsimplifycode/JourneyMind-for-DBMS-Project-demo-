<div align="center">

# JourneyMind

### The price you're quoted is not the price you pay. JourneyMind tells you the difference.

A multimodal commute decision system for Bengaluru — it compares every practical way of making a
journey, prices the cost of *failure*, and records why it recommended what it did.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](#license)
![Next.js 14](https://img.shields.io/badge/Next.js-14-black)
![FastAPI](https://img.shields.io/badge/FastAPI-Python%203.12-009688)
![MySQL 8.0](https://img.shields.io/badge/MySQL-8.0-4479A1)
![Redis 8](https://img.shields.io/badge/Redis-8-DC382D)
![Neo4j 5.26](https://img.shields.io/badge/Neo4j-5.26-018BFF)

</div>

---

## The problem

You open an app. It quotes you a fare. You tap book.

No driver. You try again — the fare is higher now, because the market has just demonstrated that it is
tight. A driver accepts, then cancels. You try again, higher still. The minutes you spent waiting appear
on no receipt at all.

**Every app shows you the advertised price. None of them shows you what the journey will actually cost.**

The same blind spot exists at scale. An organisation paying for thousands of staff trips has no single
place to see which campus loses the most time to failed bookings, or which provider is genuinely reliable
rather than merely cheap.

## What JourneyMind does differently

**🎯 It prices failure, not just fares.** Every option is priced twice — the advertised fare, and what you
can expect to pay once cancellation and re-booking are taken into account.

**🚦 It checks feasibility before it checks price.** Walking is free, but walking across the city is not a
travel option. Modes impractical for the distance are removed *before* anything is ranked, so a cheap but
unreasonable option can never win.

**🔀 It plans journeys, not rides.** Bike taxi → metro → a short walk is one journey, scored end to end on
cost, time, number of changes, and how likely each leg is to actually happen.

**📋 It shows its working.** Every recommendation and every enterprise query is recorded with the model
version and confidence behind it — so any answer the system gave last month can be explained today.

## See it in action

| Screen | What it does |
|---|---|
| **Book a ride** | Every practical option, cheapest first, with fare, duration and availability |
| **Booking & retry** | A real attempt — driver found, accepted, then cancelled — with retries priced to the surge the market just demonstrated |
| **Intelligence** | Each option priced twice: advertised fare against expected cost |
| **Journey planner** | A complete multimodal route drawn on a map, transit lines served from the graph database |
| **Insights** | When and why bookings fail |
| **Enterprise dashboard** | Spend, success rate, cancellation rate and the cost of failed bookings — by campus, team and provider |

---

<div align="center">

# 📚 For students

</div>

> **New to DBMS?** This section is for you. JourneyMind was built as a 5th-semester DBMS project, and
> almost every topic in the course shows up somewhere in it — doing real work, on real data, not as a
> textbook exercise. Below is a guided tour: what each concept is, and where to go and look at it.

### Start with the one idea that shapes everything

**The right database depends on the question you're asking.** That is the whole project in one sentence.

JourneyMind uses three databases — not to show off, but because it asks three different kinds of question:

| You ask… | You want… | JourneyMind uses |
|---|---|---|
| *"How much did the North campus spend on cancelled bookings last month?"* | Structured records you can filter, join and total up | **MySQL** — a relational database |
| *"What are the coordinates of Cubbon Park?"* — asked over and over | The same answer back, instantly, by name | **Redis** — a key–value store |
| *"How do I get from Majestic to Central Silk Board, changing lines as needed?"* | To follow connections, however many hops it takes | **Neo4j** — a graph database |

You *could* force all three into MySQL. The point of the project is to feel why you wouldn't. And the proof
that none of them is decoration: **the application still runs when any one of them is switched off.**

### Concept map — where to find each topic

| Concept | What it is, in one line | Where it lives in this project |
|---|---|---|
| **E-R model** | A drawing of the things you store and how they relate, before any table exists | [`docs/er-diagram.png`](docs/er-diagram.png) — Chen notation |
| **Relational mapping** | Turning that drawing into actual tables | [`docs/relational-schema.png`](docs/relational-schema.png) |
| **Weak entity** | A thing that can't exist on its own | `booking_attempts` — an attempt is meaningless without its session |
| **DDL** | The statements that *define* structure | `database/mysql/schema.sql` |
| **Primary key** | The column that uniquely identifies a row | `zone_id`, `booking_id`, `session_id`, `attempt_id`, `event_id` |
| **Foreign key** | A column that must match a real row in another table | `bookings.zone_id`, `booking_attempts.session_id` |
| **UNIQUE constraint** | "No two rows may claim this" | `UNIQUE (session_id, attempt_no)` |
| **CHECK constraint** | A business rule the database itself refuses to break | `chk_bookings_lifecycle` |
| **Index** | A lookup structure that makes a filter fast | `idx_bookings_campus_provider` and five more |
| **View** | A saved query that behaves like a table but stores nothing | `v_campus_mobility` |
| **DML** | `INSERT` / `UPDATE` / `DELETE` / `SELECT` — working with the *data* | Report §6 |
| **Joins, `GROUP BY`, `HAVING`, subqueries** | Combining and summarising rows | The enterprise dashboard's queries |
| **Transactions** | All of it happens, or none of it does | Booking write — see below |
| **DCL** | `GRANT` / `CREATE USER` — who is allowed to do what | `scripts/databases.ps1` |
| **NoSQL: key–value** | No tables, no SQL — fetch by key, and let data expire | Redis |
| **NoSQL: graph** | Nodes and relationships; traversal at any depth | Neo4j |

---

## Architecture

The browser **never** connects to a database directly. Every request goes through the FastAPI back end,
which holds the credentials and the logic.

```
                      User (commuter or analyst)
                                 |
                                 v
              Next.js front end  (frontend/app, frontend/src)
                                 |
                        HTTP request  /api/...
                                 v
               FastAPI back end  (backend/app/api)
                                 |
                    JourneyMind application logic
            (feasibility -> routing -> optimisation -> ranking)
                                 |
        +------------------------+------------------------+
        |                        |                        |
        v                        v                        v
      MySQL                    Redis                    Neo4j
  permanent structured    fast temporary and         the transport
  records and history     cached key-value data    network as a graph
```

> **Why this matters:** if the browser held database credentials, anyone could read them from the page
> source. Putting a back end in the middle is the standard way to keep a database private.

## Tech stack

| Tool | Role |
|---|---|
| Next.js 14 (React 18) | Front end — App Router, exported as static files |
| FastAPI (Python 3.12) | Back end / API |
| MySQL 8.0.46 | SQL database |
| Redis 8.10.1 | NoSQL key–value database |
| Neo4j 5.26 Community | NoSQL graph database |
| Leaflet + OpenStreetMap | Mapping |
| NumPy | Journey search and serving-time arithmetic |
| pytest | Unit, API and database integration tests |
| Playwright | Browser-driven end-to-end tests and screenshots |

---

## Quick start

### 1. Prerequisites

MySQL 8.0 · Redis 8 · Neo4j 5.26 Community · Python 3.12 · Node.js

### 2. Configure

Database settings come from environment variables. Copy the committed `.env` template — it holds
placeholders only — and fill in your own values.

> **Rule you should carry into every project:** credentials never go in source code. The moment a password
> is committed, it is public forever, even if you delete it in the next commit.

The reference environment runs MySQL on port `3307` and Redis on `6380`.

### 3. Create the database and its user

The application never connects as `root`. It uses a dedicated account whose privileges stop at the
`journeymind` database — so a bug in the app can't touch anything else on the server.

```powershell
scripts/databases.ps1
```

```sql
CREATE DATABASE IF NOT EXISTS journeymind
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'journeymind'@'127.0.0.1'
    IDENTIFIED BY '<password supplied from .env, never hard-coded>';

GRANT ALL PRIVILEGES ON journeymind.* TO 'journeymind'@'127.0.0.1';
FLUSH PRIVILEGES;
```

> **This is DCL.** `GRANT` and `CREATE USER` don't define structure (that's DDL) or touch data (that's DML)
> — they control *permission*.

### 4. Initialise all three databases — one command

```bash
python database/init_all.py
```

It creates and populates MySQL, Redis and Neo4j, then runs the verification script as a separate process
and reports whether every structure passed.

Redis sessions and cached dashboard answers expire on purpose. Repopulate them any time with:

```bash
python database/init_all.py --warm
```

### 5. Build the front end

```bash
cd frontend
npm install
npm run build
```

`npm run build` produces static files which the FastAPI back end then serves — so the whole product runs
as a single service.

### 6. Run

<!-- TODO: fill in the command your repo uses to start the FastAPI back end. -->

```bash
# start the FastAPI back end, then open the app in a browser
```

---

## Database design

### The tables

| Relation | Primary key | Foreign keys |
|---|---|---|
| `zones` | `zone_id` | — |
| `bookings` | `booking_id` | `zone_id → zones(zone_id)` `ON DELETE RESTRICT ON UPDATE CASCADE` |
| `booking_sessions` | `session_id` | — |
| `booking_attempts` | `attempt_id` | `session_id → booking_sessions(session_id)` `ON DELETE CASCADE` |
| `audit_events` | `event_id` | — |
| `v_campus_mobility` | *(view)* | a saved query over `bookings ⋈ zones` — stores no rows of its own |

**`booking_attempts` is a weak entity.** An attempt cannot exist without its session, and is identified
within that session by `attempt_no` — which is why `UNIQUE (session_id, attempt_no)` exists: it stops two
rows both claiming to be attempt 2 of the same booking.

**Notice the two different `ON DELETE` rules — they are a design decision, not a default.**

- `booking_attempts` uses **`CASCADE`**: delete a session and its attempts go too, because an attempt has
  no meaning once its session is gone.
- `bookings` uses **`RESTRICT`**: the database *refuses* to delete a zone that bookings still reference.
  Silently deleting sixty thousand historical facts because someone removed a zone row would not be a
  repair.

### Constraints — rules the database refuses to break

```sql
-- coordinates must be physically possible
CONSTRAINT chk_zones_lat CHECK (lat BETWEEN  -90 AND  90),
CONSTRAINT chk_zones_lon CHECK (lon BETWEEN -180 AND 180),

-- a real rule of the domain: a trip cannot be completed unless it was
-- accepted, and cannot be accepted unless a driver was matched
CONSTRAINT chk_bookings_lifecycle CHECK (
        (accepted  = FALSE OR matched  = TRUE)
    AND (completed = FALSE OR accepted = TRUE)
    AND NOT (completed = TRUE AND cancelled = TRUE));
```

> **Why put rules in the database instead of the app?** Because application code is not the only thing that
> writes to a database. A script, a migration, a teammate's `INSERT` in a terminal — a `CHECK` constraint
> stops all of them. Validation in the app is a courtesy; a constraint is a guarantee.

### Indexes

```sql
KEY idx_bookings_ts               (ts),
KEY idx_bookings_zone             (zone_id),
KEY idx_bookings_hour             (hour),
KEY idx_bookings_campus_provider  (campus_id, provider_id),
KEY idx_bookings_group            (employee_group),
KEY idx_audit_at                  (at),
KEY idx_audit_kind_at             (kind, at)
```

Every index matches a filter the dashboard actually applies. `idx_bookings_campus_provider` is a
*composite* index, serving the common drill-down of one campus and one provider together.

> **Don't index everything.** Every index has to be updated on every write. Index the columns you filter
> and sort by — and be able to name the query each one serves.

### Transactions

A finished booking writes the session row *and* all of its attempt rows. These describe one event. Writing
the session and then failing on the attempts would leave a booking in the database that appeared to have
cost nothing.

```python
with mysql.transaction() as cur:
    cur.execute(INSERT_SESSION, (...))       # the booking itself
    cur.execute(DELETE_ATTEMPTS, (sid,))     # clear any previous attempts
    cur.executemany(INSERT_ATTEMPT, [...])   # every attempt that was made
# commit happens only if all three succeed; any error rolls back the lot
```

> **All or nothing.** That is the promise a transaction makes. It's the "A" in ACID — atomicity.

### Redis — data that is *supposed* to disappear

| Key pattern | Type | TTL |
|---|---|---|
| `jm:geo:<place>` | cached place lookup | never expires |
| `jm:session:<id>` | live booking session | expires |
| cached dashboard payloads | cache | expires |
| `jm:audit:recent` | recent decisions list | bounded by `LTRIM`, so memory use stays constant |

```
127.0.0.1:6380> GET jm:geo:cubbon park
  [12.9782, 77.596, "Cubbon Park"]

127.0.0.1:6380> TTL jm:session:bk_dCpyeGunr0By
  604800        (seconds remaining; -1 would mean never expires)
```

> **Expiry is a feature, not data loss.** Two of these four families are *meant* to vanish — that's correct
> behaviour for a session store and a cache, and nothing in MySQL is affected when they do.

### Neo4j — the network as a graph

Nodes `(:Stop)`; relationships `[:ROAD_LINK]`, `[:TRANSIT_LINK]`, `[:TRANSFER_LINK]`.

```cypher
CREATE CONSTRAINT stop_id_unique IF NOT EXISTS
FOR (s:Stop) REQUIRE s.stop_id IS UNIQUE;

CREATE INDEX stop_kind     IF NOT EXISTS FOR (s:Stop) ON (s.kind);
CREATE INDEX transit_route IF NOT EXISTS FOR ()-[r:TRANSIT_LINK]-() ON (r.route_id);
```

Here is the clearest single argument for a graph database in the whole project — **the depth of the search
is just a number in the pattern**:

```cypher
MATCH (a:Stop {stop_id: $from_id}), (b:Stop {stop_id: $to_id})
MATCH p = shortestPath((a)-[:TRANSIT_LINK|TRANSFER_LINK*..15]-(b))
RETURN length(p) AS hops, [n IN nodes(p) | n.name] AS stops_on_the_way;
```

> `*..15` means "follow up to fifteen hops." Expressing the same search in SQL means a recursive query or a
> chain of self-joins. That is what a graph database buys you.

---

## Security and access control

Applied at two levels.

**Database level** — a dedicated MySQL account restricted to the `journeymind` database; the password comes
from an environment variable.

**Application level** — FastAPI enforces role-based authorisation. Enterprise endpoints require an
`X-API-Key` header. Keys are never stored in plain text: only their **SHA-256 hash** is kept, and the
supplied key is hashed and compared.

| Role | Level | May access |
|---|---|---|
| `RIDER` | 10 | Journey search, comparison and booking |
| `ANALYST` | 20 | The above, plus the enterprise dashboard and the audit trail |
| `ADMIN` | 30 | The above; configuration and key management |

If no keys are configured and demonstration mode is off, the enterprise endpoints **refuse every request
rather than falling open.** Failing closed is the safe default.

## Proof it works

Claims are cheap. These are measured.

### Tests

316 automated tests. With all three databases running, **315 pass and 1 is skipped** — expected and
documented: one test needs a scheduled bus or metro service on a particular trip, and skips when none is
running at that hour.

| Layer | Command | Result |
|---|---|---|
| Unit and API | `pytest tests -q -m "not integration"` | 289 passed |
| Database integration | `pytest tests/integration -v` | 26 passed |
| Data volume verification | `python database/verify.py` | 13 of 13 structures PASS (exit code 0) |
| Full suite | `pytest tests -q` | 315 passed, 1 skipped |

The integration tests do more than open a connection. One temporarily replaces a value in Redis and checks
that the API's answer follows it — proving the application really reads Redis. Another inserts a
deliberately invalid row to confirm the foreign key refuses it.

### Data volume

Counts are read from the *running* databases by `database/verify.py` immediately after a clean
initialisation. None is stored or assumed. The script exits 0 only when every structure passes.

| Database | Structure | Count |
|---|---|---|
| MySQL | `zones` | 105 |
| MySQL | `bookings` | 60,000 |
| MySQL | `booking_sessions` | 130 |
| MySQL | `booking_attempts` | 227 |
| MySQL | `audit_events` | 371 |
| Redis | cached place lookups | 222 |
| Redis | live booking sessions | 130 |
| Redis | cached dashboard payloads | 111 |
| Redis | recent decisions list | 371 |
| Neo4j | `(:Stop)` nodes | 223 |
| Neo4j | `[:ROAD_LINK]` | 1,052 |
| Neo4j | `[:TRANSIT_LINK]` | 174 |
| Neo4j | `[:TRANSFER_LINK]` | 216 |

Integrity is checked alongside the counts: **0** bookings reference a missing zone, **0** attempts reference
a missing session, **0** disconnected `Stop` nodes — and a sample traversal from Majestic to Central Silk
Board returns a real 11-hop path.

## Project layout

```
backend/app/api            FastAPI routes
frontend/app               Next.js App Router (layout.jsx, page.jsx)
frontend/src               reused components
frontend/next.config.mjs   build settings
database/mysql/schema.sql  DDL, executed by seed_mysql.py
database/seed_mysql.py     MySQL seeding
database/init_all.py       one-command initialisation of all three databases
database/verify.py         data-volume and integrity verification
scripts/databases.ps1      creates the database and its dedicated user
tests/                     unit and API tests
tests/integration/         tests requiring live databases
```

## Roadmap

- Replace the simulated availability model with live operator data
- Full user accounts with individual travel history, in place of API keys
- Extend the study area beyond the current corridor — the graph model already scales; only the bundled data is limited
- Add database triggers to record an audit entry automatically on write, rather than from application code
- Learn each traveller's own cost/time trade-off from past choices instead of using fixed presets
- Use the graph to re-route around a disruption when a line is closed

---

## Team

Built by three 5th-semester CSE students at PES University, Bengaluru.

| SRN | Name |
|---|---|
| PES1UG23CS024 | Adishree Gupta |
| PES1UG23CS624 | Swathi S |
| PES1UG23CS366 | Mohammed Jawwaad Sheriff |

*Team code 23CS024 · 23CS624 · 23CS366*

### Acknowledgement

We built JourneyMind under the guidance of **Dr Nagasundari S**, whose direction shaped both the database
design and the standard we held the project to. Submitted for **UE24CS351A — Database Management Systems**,
Aug–Dec 2026, Department of Computer Science and Engineering, PES University.

## References

1. [Next.js Documentation](https://nextjs.org/docs) — Vercel
2. [FastAPI Documentation](https://fastapi.tiangolo.com) — Sebastián Ramírez
3. [MySQL 8.0 Reference Manual](https://dev.mysql.com/doc/refman/8.0/en/) — Oracle Corporation
4. [Redis Documentation — Data Types and Commands](https://redis.io/docs/latest/develop/data-types/) — Redis Ltd.
5. [Neo4j Cypher Manual](https://neo4j.com/docs/cypher-manual/current/) — Neo4j Inc.
6. [OpenStreetMap](https://www.openstreetmap.org/copyright) — map data licensed under the ODbL
7. Elmasri, R. and Navathe, S. B. *Fundamentals of Database Systems*
8. Course material: UE24CS351A Database Management Systems, Unit 4 — NoSQL Systems (L46), Redis (L49), Graph Databases and Neo4j (L50–51), PES University

## License

Released under the **MIT License** — see [`LICENSE`](LICENSE). Use it, learn from it, build on it.

Map data © OpenStreetMap contributors, licensed under the
[ODbL](https://www.openstreetmap.org/copyright).
