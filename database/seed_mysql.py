"""Create the MySQL schema and load the bundled records into it.

    python database/seed_mysql.py [--reset]

WHY THIS IS A SCRIPT AND NOT A 15 MB seed.sql
---------------------------------------------
The two bulk tables hold 105 zones and 60,000 bookings, and both already exist
in the repository as the data files the application has always shipped with
(`data/mobility/zones.json`, `data/mobility/bookings.csv`). Writing them out
again as INSERT statements would put a second, immediately-divergent copy of
the same facts under version control. Reading the real files and loading them
is reproducible in the way that matters: run it twice and the database is the
same, because it is derived from the same source both times.

The DDL *is* a .sql file, because the schema is the thing worth reading.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import _bootstrap
from _bootstrap import DATA_DIR, PROJECT_ROOT, banner, mobility_dir, setup

SCHEMA_SQL = Path(__file__).resolve().parent / "mysql" / "schema.sql"


def _statements(sql_text: str) -> list[str]:
    """Split a .sql file into executable statements.

    Line comments are stripped first so that a ';' inside one cannot end a
    statement early. There are no string literals containing ';' in the schema,
    which is what makes a split this simple correct here.
    """
    no_comments = re.sub(r"^\s*--.*$", "", sql_text, flags=re.MULTILINE)
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def apply_schema(reset: bool) -> None:
    """Run schema.sql. Connects without a database, because it creates one."""
    import mysql.connector

    from app.db.settings import get_db_settings

    s = get_db_settings().mysql
    conn = mysql.connector.connect(
        host=s.host, port=s.port, user=s.user, password=s.password,
        charset="utf8mb4", use_pure=True, autocommit=True)
    cur = conn.cursor()
    try:
        if reset:
            # Children first: booking_attempts has a FK to booking_sessions and
            # bookings has one to zones, so dropping in creation order would be
            # refused by InnoDB -- which is the referential integrity working.
            print("  --reset: dropping existing tables")
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{s.database}`")
            cur.execute(f"USE `{s.database}`")
            for table in ("booking_attempts", "booking_sessions",
                          "audit_events", "bookings", "zones"):
                cur.execute(f"DROP TABLE IF EXISTS `{table}`")
        for stmt in _statements(SCHEMA_SQL.read_text(encoding="utf-8")):
            cur.execute(stmt)
        print(f"  schema applied from {SCHEMA_SQL.relative_to(PROJECT_ROOT)}")
    finally:
        cur.close()
        conn.close()


# --------------------------------------------------------------------------
def load_zones() -> int:
    """105 pickup/drop zones, from the bundle the analytics already reads."""
    from app.db import mysql

    path = mobility_dir() / "zones.json"
    zones = json.loads(path.read_text(encoding="utf-8"))
    rows = [
        (z["zone_id"], z["name"], z["kind"], float(z["lat"]), float(z["lon"]),
         float(z.get("latent_congestion", 0.0)),
         float(z.get("observed_congestion", 0.0)))
        for z in zones
    ]
    # INSERT ... ON DUPLICATE KEY UPDATE, not REPLACE. REPLACE is a DELETE
    # followed by an INSERT, and deleting a zone that 60,000 bookings point at
    # is refused by fk_bookings_zone -- which is the foreign key doing its job,
    # and which made re-running this script on an already-loaded database fail.
    # This form updates the row in place, so the seed really is idempotent.
    mysql.execute_many(
        """INSERT INTO zones
               (zone_id, name, kind, lat, lon, latent_congestion, observed_congestion)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE
               name = VALUES(name), kind = VALUES(kind),
               lat = VALUES(lat), lon = VALUES(lon),
               latent_congestion = VALUES(latent_congestion),
               observed_congestion = VALUES(observed_congestion)""",
        rows)
    print(f"  zones: {len(rows)} rows from {path.name}")
    return len(rows)


def load_bookings() -> int:
    """The 60,000-row enterprise booking history.

    `zone_kind` and `zone_congestion_observed` in the CSV are dropped on the
    way in: they are attributes of the zone, not of the booking, and they now
    live once each in `zones` instead of sixty thousand times each here. That
    is the normalisation the FK exists to make safe.
    """
    from app.db import mysql

    path = mobility_dir() / "bookings.csv"

    def rows():
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                yield (
                    r["booking_id"], r["ts"].replace("T", " "),
                    float(r["hour"]), int(r["dow"]),
                    int(r["is_weekend"]), int(r["late_night"]), int(r["rain"]),
                    r["provider_id"], r["mode"],
                    r["campus_id"], r["campus"], r["employee_group"],
                    r["cost_centre"], r["zone_id"],
                    float(r["distance_km"]), float(r["pickup_km"]),
                    float(r["peak_intensity"]), float(r["short_trip_penalty"]),
                    int(r["matched"]), int(r["accepted"]),
                    int(r["cancelled"]), int(r["completed"]),
                )

    # Same reasoning as `zones`: update in place rather than delete-and-insert,
    # so a re-run cannot trip a foreign key or briefly empty the table.
    n = mysql.execute_many(
        """INSERT INTO bookings
               (booking_id, ts, hour, dow, is_weekend, late_night, rain,
                provider_id, mode, campus_id, campus, employee_group,
                cost_centre, zone_id, distance_km, pickup_km, peak_intensity,
                short_trip_penalty, matched, accepted, cancelled, completed)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   %s, %s, %s, %s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE
               ts = VALUES(ts), matched = VALUES(matched),
               accepted = VALUES(accepted), cancelled = VALUES(cancelled),
               completed = VALUES(completed)""",
        rows())
    total = mysql.scalar("SELECT COUNT(*) FROM bookings")
    print(f"  bookings: {total} rows from {path.name}")
    return int(total or 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true",
                    help="drop the tables first (destroys seeded demo traffic)")
    args = ap.parse_args()

    setup()
    banner("MySQL — schema and bundled records")

    from app.db import mysql
    mysql.reset_pool()

    apply_schema(args.reset)

    if not mysql.available():
        print("  MySQL is not reachable — check MYSQL_* in .env", file=sys.stderr)
        return 1

    load_zones()
    load_bookings()

    print("\n  done. Live booking sessions, attempts and audit events are")
    print("  written by database/seed_demo_traffic.py, which runs the real")
    print("  engine rather than inventing rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
