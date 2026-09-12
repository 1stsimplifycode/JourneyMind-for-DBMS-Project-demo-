"""Count what is actually in the three databases, and say whether it is enough.

    python database/verify.py [--min 100] [--json]

WHY THIS IS A SCRIPT AND NOT A PARAGRAPH IN THE README
------------------------------------------------------
"There are more than a hundred rows" is a claim. `SELECT COUNT(*)` is an
answer. Every number this prints comes from the running database at the moment
it is printed -- there is no stored total anywhere in here, and nothing is
inferred from the size of a seed file.

WHAT IT COUNTS
--------------
Every structure the application actually creates:

    MySQL   one COUNT(*) per table
    Redis   one count per key family, by the pattern the application writes
            under -- SCAN for the key families, LLEN for the list
    Neo4j   one count per node label and per relationship type, using the
            @counts_* blocks in database/neo4j/queries.cypher

A store that is unreachable is reported as SKIP, not as a failure: all three
are optional at runtime, and "Redis is not running" is a different statement
from "Redis is running and is short of data". The exit code distinguishes them:

    0   every structure that could be counted meets the minimum
    1   at least one structure is short
    2   at least one store could not be reached (and nothing was short)
"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap
from _bootstrap import banner, setup

#: The evaluation constraint. Overridable so the same script can check a
#: smaller fixture, but 100 is the number the project is held to.
DEFAULT_MIN = 100

SKIP = "SKIP"
PASS = "PASS"
FAIL = "FAIL"


class Report:
    """Collects one row per structure and prints the table at the end."""

    def __init__(self, minimum: int) -> None:
        self.minimum = minimum
        self.rows: list[dict] = []

    def add(self, technology: str, structure: str, count: int | None,
            note: str = "") -> None:
        if count is None:
            status = SKIP
        elif count >= self.minimum:
            status = PASS
        else:
            status = FAIL
        self.rows.append({"technology": technology, "structure": structure,
                          "count": count, "minimum": self.minimum,
                          "status": status, "note": note})

    # ----------------------------------------------------------------
    @property
    def failures(self) -> list[dict]:
        return [r for r in self.rows if r["status"] == FAIL]

    @property
    def skipped(self) -> list[dict]:
        return [r for r in self.rows if r["status"] == SKIP]

    def print_table(self) -> None:
        w_tech = max(10, *(len(r["technology"]) for r in self.rows))
        w_struct = max(9, *(len(r["structure"]) for r in self.rows))
        head = (f"| {'Technology':<{w_tech}} | {'Structure':<{w_struct}} "
                f"| {'Count':>9} | {'Required':>8} | Result |")
        rule = (f"| {'-' * w_tech} | {'-' * w_struct} | {'-' * 9} "
                f"| {'-' * 8} | ------ |")
        print(head)
        print(rule)
        for r in self.rows:
            count = "—" if r["count"] is None else f"{r['count']:,}"
            print(f"| {r['technology']:<{w_tech}} | {r['structure']:<{w_struct}} "
                  f"| {count:>9} | {'>=' + str(r['minimum']):>8} | {r['status']:<6} |")

        notes = [r for r in self.rows if r["note"]]
        if notes:
            print()
            for r in notes:
                print(f"  {r['structure']}: {r['note']}")

    def exit_code(self) -> int:
        if self.failures:
            return 1
        if self.skipped:
            return 2
        return 0


# --------------------------------------------------------------------------
# MySQL
# --------------------------------------------------------------------------
#: Every table in database/mysql/schema.sql. Listed rather than discovered with
#: SHOW TABLES, so that a table the schema is supposed to create but did not is
#: reported as missing instead of silently not being checked.
MYSQL_TABLES = (
    ("zones", "pickup/drop zones the booking history refers to"),
    ("bookings", "the enterprise booking history (fact table)"),
    ("booking_sessions", "bookings this instance made, one per BOOK NOW"),
    ("booking_attempts", "each attempt inside a session"),
    ("audit_events", "every AI decision and the evidence behind it"),
)


def check_mysql(report: Report) -> None:
    from app.db import mysql

    mysql.reset_pool()
    if not mysql.available():
        reason = mysql.unavailable_reason() or "not configured"
        for table, _ in MYSQL_TABLES:
            report.add("MySQL", table, None, f"MySQL unreachable: {reason}")
        return

    for table, purpose in MYSQL_TABLES:
        try:
            n = int(mysql.scalar(f"SELECT COUNT(*) FROM `{table}`") or 0)
            report.add("MySQL", table, n, purpose)
        except Exception as exc:
            report.add("MySQL", table, None, f"{type(exc).__name__}: {exc}")

    # Referential integrity is part of "meaningful", not a separate nicety: a
    # booking pointing at a zone that does not exist is not a usable record.
    # The FK makes this impossible; counting it proves the FK is really there.
    try:
        orphans = int(mysql.scalar(
            "SELECT COUNT(*) FROM bookings b "
            "LEFT JOIN zones z ON z.zone_id = b.zone_id "
            "WHERE z.zone_id IS NULL") or 0)
        attempts_orphaned = int(mysql.scalar(
            "SELECT COUNT(*) FROM booking_attempts a "
            "LEFT JOIN booking_sessions s ON s.session_id = a.session_id "
            "WHERE s.session_id IS NULL") or 0)
        print(f"\n  referential integrity: {orphans} bookings with no zone, "
              f"{attempts_orphaned} attempts with no session "
              f"({'both zero, as the foreign keys require' if not (orphans or attempts_orphaned) else 'NON-ZERO — the schema is not what it claims'})")
    except Exception as exc:
        print(f"\n  referential integrity check failed: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------
# Redis
# --------------------------------------------------------------------------
#: The Redis families that expire by design, and how long they last. They are
#: above 100 immediately after `database/init_all.py` and fall away afterwards,
#: because that is what a session store and a cache are supposed to do. Listed
#: here so the report can say so rather than leaving a reader to guess whether
#: a low count means "expired" or "never seeded".
EPHEMERAL = {
    "live booking sessions": "REDIS_DEMO_TTL, 7 days for seeded sessions",
    "cached dashboard payloads": "REDIS_AGGREGATE_TTL, 1 day",
}


def check_redis(report: Report) -> None:
    from app.db import redis_store

    redis_store.reset_client()
    families = (
        ("jm:geo:*", "cached place lookups", "string"),
        ("jm:session:*", "live booking sessions", "hash"),
        ("jm:agg:*", "cached dashboard payloads", "string"),
    )
    if not redis_store.available():
        reason = redis_store.unavailable_reason() or "not configured"
        for pattern, label, _ in families:
            report.add("Redis", label, None, f"Redis unreachable: {reason}")
        report.add("Redis", "recent decisions list", None,
                   f"Redis unreachable: {reason}")
        return

    for pattern, label, structure in families:
        # The pattern is rebuilt through `key()` so it honours REDIS_PREFIX
        # rather than assuming the default one.
        suffix = pattern.split(":", 1)[1]
        actual = redis_store.key(*suffix.split(":"))
        n = redis_store.count_keys(actual)
        note = f"{structure} keys matching {actual}"
        if label in EPHEMERAL:
            note += f" — TTL-backed ({EPHEMERAL[label]}); restore with " \
                    f"`python database/init_all.py --warm`"
        report.add("Redis", label, n, note)

    # The audit trail is ONE key holding many entries, so its volume is the
    # length of the list, not a key count. Counting keys here would report 1
    # and be arithmetically true and completely meaningless.
    audit_key = redis_store.key("audit", "recent")
    n = redis_store.llen(audit_key)
    report.add("Redis", "recent decisions list", n,
               f"list entries in {audit_key} (LLEN, bounded by LTRIM)")

    # TTLs are part of the design, so show that they are real rather than
    # claimed. -1 would mean a session that never expires.
    sample = next(iter(redis_store.scan_keys(redis_store.key("session", "*"))), None)
    if sample:
        print(f"\n  TTL sample: {sample} expires in "
              f"{redis_store.ttl(sample)} s")


# --------------------------------------------------------------------------
# Neo4j
# --------------------------------------------------------------------------
NEO4J_COUNTS = (
    ("counts_nodes", "(:Stop) nodes", "every vertex the router can stand on"),
    ("counts_road", "[:ROAD_LINK]", "walkable street segments, both directions"),
    ("counts_transit", "[:TRANSIT_LINK]", "stop-to-stop hops on metro/bus routes"),
    ("counts_transfer", "[:TRANSFER_LINK]", "walks between nearby transit nodes"),
)


def check_neo4j(report: Report) -> None:
    from app.db import neo4j_store
    from app.graph.neo4j_topology import cypher

    neo4j_store.reset_driver()
    if not neo4j_store.available():
        reason = neo4j_store.unavailable_reason() or "not configured"
        for _, label, _ in NEO4J_COUNTS:
            report.add("Neo4j", label, None, f"Neo4j unreachable: {reason}")
        return

    for block, label, purpose in NEO4J_COUNTS:
        q = cypher(block)
        if q is None:
            report.add("Neo4j", label, None,
                       f"no @{block} block in database/neo4j/queries.cypher")
            continue
        rows = neo4j_store.run(q)
        n = int(rows[0]["n"]) if rows else None
        report.add("Neo4j", label, n, purpose)

    # A node that participates in nothing is padding, and the brief says so
    # explicitly. This is the query that proves there is none.
    orphans = neo4j_store.run(
        "MATCH (s:Stop) WHERE NOT (s)--() RETURN count(s) AS n")
    if orphans is not None:
        n = int(orphans[0]["n"])
        print(f"\n  disconnected (:Stop) nodes: {n} "
              f"({'none — every node is on the network' if n == 0 else 'NON-ZERO — these take part in no traversal'})")

    # And one real traversal, so the graph is shown to be queryable and not
    # merely populated.
    path = neo4j_store.run(cypher("shortest_transit_path") or "",
                           {"from_id": "mg_majestic", "to_id": "mg_silkboard"})
    if path:
        p = path[0]
        print(f"  sample traversal (Majestic -> Silk Board): {p['hops']} hops, "
              f"{' -> '.join(p['stop_names'][:4])}"
              f"{' -> ...' if len(p['stop_names']) > 4 else ''}")


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min", type=int, default=DEFAULT_MIN,
                    help=f"minimum items per structure (default {DEFAULT_MIN})")
    ap.add_argument("--json", action="store_true",
                    help="print the rows as JSON instead of a table")
    args = ap.parse_args()

    setup(verbose=False)
    report = Report(args.min)

    if not args.json:
        banner("DATABASE DATA VOLUME VERIFICATION")
        print("Counts below are read from the running databases at this moment.")

    check_mysql(report)
    check_redis(report)
    check_neo4j(report)

    if args.json:
        print(json.dumps(report.rows, indent=2))
        return report.exit_code()

    print()
    report.print_table()
    print()

    if report.skipped:
        print(f"  {len(report.skipped)} structure(s) could not be counted "
              f"because their store is not reachable. That is not a pass and "
              f"not a failure — it is an unknown.")
    if report.failures:
        print(f"  {len(report.failures)} structure(s) below {args.min}:")
        for r in report.failures:
            print(f"    - {r['technology']} {r['structure']}: {r['count']}")
        # A TTL-backed family that has simply expired is a different situation
        # from one that was never seeded, and the fix is different too. Say
        # which one this is instead of only reporting the number.
        expired = [r for r in report.failures if r["structure"] in EPHEMERAL]
        if expired:
            print(f"\n  {len(expired)} of those are TTL-backed and expire by "
                  f"design. Restore them with:\n"
                  f"      python database/init_all.py --warm")
        if len(expired) < len(report.failures):
            print("\n  The rest were never seeded. Run:\n"
                  "      python database/init_all.py")
        print("\n  DATABASE POPULATION IS INCOMPLETE.")
    elif not report.skipped:
        print(f"  All {len(report.rows)} structures meet the {args.min}+ "
              f"requirement.")

    return report.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
