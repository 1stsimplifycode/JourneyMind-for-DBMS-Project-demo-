# Redis keyspace

Every key this application writes begins with `REDIS_PREFIX` (`jm` by default),
so JourneyMind can share a Redis instance without colliding with anything else
on it. Within that prefix, `:` separates the hierarchy — the namespacing
convention taught in Unit 4, Lecture 49 (`PES:Department`, `user:session:123`).

There are four key families and no others. Each is listed below with the Redis
data structure it uses, why *that* structure, what the value means, whether it
expires, and what happens when it is not there.

| Family | Role | Structure | Lifetime | Count after `init_all.py` |
|---|---|---|---|---|
| `jm:geo:*` | geocode lookup cache | STRING | **Persistent** — no TTL; cleared only by `seed_redis.py --flush` | 222 |
| `jm:session:*` | live / recent booking session state | HASH | **TTL** — 45 min live, `REDIS_DEMO_TTL` (7 days) for seeded demo sessions | 130 |
| `jm:agg:*` | cached dashboard aggregates | STRING | **TTL** — `REDIS_AGGREGATE_TTL`, 1 day | 111 |
| `jm:audit:recent` | bounded recent-decision log | LIST | **Capped, not expiring** — `LTRIM` holds it at 500 | 371 |

The two TTL-backed families fall to zero on their own, which is what a session
store and a cache are supposed to do — it is expiry, not data loss, and nothing
in MySQL is affected. Both are above 100 immediately after the documented seed,
and `python database/init_all.py --warm` restores them at any time.

---

## 1. `jm:geo:<lowercased place name>` — STRING, no TTL

```
jm:geo:cubbon park          ->  [12.9782, 77.5960, "Cubbon Park"]
jm:geo:trinity              ->  [12.9731, 77.6169, "Trinity"]
jm:geo:asdfgh               ->  []
```

**Structure.** A STRING holding a JSON array. One name in, one answer out —
`SET`/`GET` is the entire access pattern, and nothing ever reads part of a
value or updates one field of it. That is the definition of a string key.

**Why it is worth caching.** `services/geocode.py` resolves a typed place name
in three steps: the fifteen named places in the bundle, then this cache, then
Nominatim over the public internet. Nominatim is rate-limited to one request a
second and is a third party that can be slow or down. Every name in the study
area's own network is pre-loaded here by `seed_redis.py`, so a rider typing
"Cubbon Park" gets the corridor's own coordinate immediately and offline.

**Which names actually reach this cache.** Only names that are *not* one of the
fifteen bundled places. `api/routes.py::resolve_point` matches those locally
first, so typing "Indiranagar" resolves to the bundled place *Indiranagar 100ft
Road* and **never reads Redis** — even though `jm:geo:indiranagar` exists for
the metro station of that name. The worked examples here are "Cubbon Park" and
"Trinity" because both were verified against a running server by capturing the
live `GET jm:geo:cubbon park`, and both are covered by
`tests/integration/test_redis_integration.py`.

**The empty array is not a bug.** `[]` is the cached *negative* answer: this
name was looked up and does not resolve. Caching that is the whole point — a
name nobody can find should not cost a network round trip every time somebody
types it.

**TTL: none.** These are positions of physical infrastructure in a bundled
dataset. They do not go stale, and expiring them would only send the next rider
who types the name back out to the internet.

**When Redis is down.** `geocode.py` falls back to the on-disk JSON cache at
`data/_geocode_cache.json`, and then to Nominatim. That is the behaviour it had
before Redis existed, but it is **not equivalent**: the on-disk cache holds only
a handful of names, so a corridor name that lives only in Redis either resolves
to a different OpenStreetMap coordinate or fails with HTTP 422. Measured
table in `DATABASES.md` §7.

---

## 2. `jm:session:<session_id>` — HASH, TTL

```
jm:session:bk_ePY4CiQNB-jX
    session_id     bk_ePY4CiQNB-jX
    provider_id    bike_taxi
    display_name   Rapido
    origin_label   Wipro Campus, Doddakannelli
    dest_label     PES University, RR Campus
    base_fare      215.0
    p_match        0.91
    attempt_count  3
    settled        0
    attempts       [{"number": 1, "fare": 215.0, "outcome": "no_supply", ...}]
    rng_state      {"bit_generator": "PCG64", "state": {...}}
```

**Structure.** A HASH, because a booking session is a flat record of named
fields. `HGETALL` reads the whole thing in one call, and a field can be
rewritten without resending the rest. A single JSON string would work but would
make the session opaque at a `redis-cli` prompt; this way `HGET
jm:session:bk_… provider_id` answers a question a person might actually ask
while debugging. The two genuinely nested parts (`attempts`, `comparison`) are
JSON inside their own fields.

**Why Redis and not MySQL.** This is Lecture 49's "session storage" use case
almost exactly. The record is read by key and never searched, so no index is
wanted; it is rewritten on every retry; it is worthless forty-five minutes
later; and losing it costs one booking flow, not a business record. In MySQL it
would be a table whose rows are all deleted within the hour, plus a cleanup job
to do the deleting.

**TTL: `REDIS_SESSION_TTL`, 2700 s (45 minutes).** Set with `EXPIRE` on every
write, so the clock restarts each time the rider presses TRY AGAIN — an active
session does not expire underneath them. Seeded demonstration sessions get
`REDIS_DEMO_TTL` (7 days) instead, so the data-volume verification stays
repeatable for a week after seeding rather than for forty-five minutes; those
rows carry `demo=1`. After that week the family is empty until
`python database/init_all.py --warm` is run again, which is correct behaviour
and not a fault.

**What MySQL gets instead.** The moment a session reaches a terminal state,
`booking/mysql_sessions.py` writes the durable record to `booking_sessions` and
`booking_attempts`. Redis holds the session *while it is alive*; MySQL holds
the fact that it happened. Different questions, different stores.

**When Redis is down.** `SessionStore` falls back to the in-process dictionary
it used before Redis existed — TTL'd and capped, so it cannot leak.

---

## 3. `jm:agg:<sha1 of the filter selection>` — STRING, TTL

```
jm:agg:9c1f4a2b7e0d5a83fc12   ->  {"overview": {...}, "by_campus": [...], ...}
```

**Structure.** A STRING holding the whole dashboard payload as JSON. The value
is consumed all at once by one HTTP response, so there is nothing to gain from
splitting it into fields.

**What the key is.** A SHA-1 digest of the canonical JSON form of the filter
selection plus the minute-cost assumption. Canonical, so that two selections
that mean the same thing land on the same key regardless of the order the query
string arrived in. The digest — rather than the filters spelled out — keeps the
key a fixed length whatever the selection.

The caller's API key is deliberately *not* in the key: the payload is identical
for every analyst, and caching per principal would multiply the keyspace by the
number of users and hit the cache far less often.

**Why it is worth caching.** One payload is roughly forty aggregations over
44,876 rows. An analyst does not look at one selection — they click campus,
then a team, then a provider, then back — and every click asks for a payload a
previous click may already have computed. Cache-aside: look, miss, compute,
store.

**TTL: `REDIS_AGGREGATE_TTL`, 86400 s (one day).** Time is the only thing that
invalidates these, which is correct here because the booking history is an
immutable record of things that already happened — rows are appended by
seeding, never edited. If this ever served a history that could be corrected in
place, the right change would be a version counter in the key so that a
correction invalidates every dependent entry at once, not a shorter TTL.

**When Redis is down.** `enterprise/cache.py` calls `analytics.build` directly.
The page is slower and correct.

---

## 4. `jm:audit:recent` — LIST, no TTL, bounded

```
LPUSH  jm:audit:recent  {"at": "...", "kind": "recommendation", ...}
LTRIM  jm:audit:recent  0 499
LRANGE jm:audit:recent  0 24
```

**Structure.** A LIST, written with `LPUSH` then `LTRIM` — the bounded activity
log from Lecture 49. This is one key holding many entries, so its data volume is
its `LLEN`, not a key count.

**Why bounded rather than expiring.** `LTRIM` after every push means the list
can never grow past `REDIS_AUDIT_LIST_MAX` (500), so it needs no cleanup job
and no TTL, and memory use is constant no matter how long the process runs. A
TTL would be wrong here anyway: it would eventually empty the list even though
the newest entries are exactly the ones the screen wants.

**Why MySQL also has these.** The list answers "what has this system just been
doing?" — the audit screen's default view, newest first, read constantly while
somebody has the page open. `audit_events` in MySQL answers "what did it decide
on the 3rd of June?", which needs every row, indexed by time and kind, still
there next year. `db/audit_sink.py` writes both and reads Redis first, falling
through to MySQL when the request is for more than the list holds.

**When Redis is down.** The trail is read from MySQL; with MySQL also down, the
in-memory ring buffer in `security.py` answers, as it always did.

---

## Why `SCAN` and not `KEYS`

`count_keys()` in `db/redis_store.py` uses `SCAN`, while Lecture 49's hands-on
uses `KEYS *`. They answer the same question. `KEYS jm:session:*` walks the
entire keyspace in one blocking call and stalls every other client while it
does; `SCAN` returns a cursor and lets the server breathe between batches. For
a keyspace of a few hundred keys the difference is invisible, which is why
`KEYS` is the right thing to teach — but the counting code runs against
whatever size the keyspace happens to be, and `SCAN` is what makes that safe.

## Inspecting it by hand

```
redis-cli -h 127.0.0.1 -p 6380 -a "$REDIS_PASSWORD"

  SCAN 0 MATCH jm:geo:* COUNT 100      # a page of cached place lookups
  GET jm:geo:indiranagar
  HGETALL jm:session:bk_ePY4CiQNB-jX   # one live booking
  TTL jm:session:bk_ePY4CiQNB-jX       # seconds left, -1 = no expiry, -2 = gone
  LLEN jm:audit:recent                 # how many decisions the hot list holds
  LRANGE jm:audit:recent 0 4           # the five most recent
```

`python database/verify.py` does all of this programmatically and prints the
counts.
