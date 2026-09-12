"""Load the booking history out of MySQL into the columnar table.

WHY THE COLUMNAR TABLE SURVIVED THE MOVE TO A DATABASE
------------------------------------------------------
`store.py` explains why the history is held as typed NumPy columns: filtering
is a boolean mask and aggregating is a sum over it, and the list-of-dicts
version cost 89 MB and 1.6 seconds per filter click. None of that stops being
true because the rows now arrive over a socket instead of out of a CSV.

So MySQL is the system of record and this is the read path into the shape the
dashboard needs. Two things changed and both are improvements:

  1. The provider filter runs in SQL. `WHERE provider_id IN (...)` means the
     15,124 carpool rows -- a mode the product no longer offers -- are never
     sent over the wire at all, where the CSV loader had to read and discard
     them.
  2. Zone congestion comes from a JOIN. The CSV repeated
     `zone_congestion_observed` on all 60,000 rows; it is an attribute of the
     zone, there are 105 zones, and it is now stored 105 times. The JOIN
     reproduces the old column value for value -- that is what makes this a
     normalisation rather than a change of data.

FALLBACK
--------
Returns None when MySQL is unreachable or the tables are empty, and
`analytics.load_bookings()` then reads the bundled CSV exactly as before.
"""

from __future__ import annotations

import logging

from ..db import mysql
from .store import OFFERED_PROVIDERS, BookingTable

log = logging.getLogger("journeymind.enterprise.mysql")

#: The columns `BookingTable` expects, in the order this SELECT returns them.
#: Keeping the two lists adjacent is what stops a schema change from silently
#: shifting every column one to the left.
COLUMNS = (
    "ts", "hour", "dow", "is_weekend", "late_night", "rain",
    "provider_id", "mode", "campus_id", "campus", "employee_group",
    "distance_km", "pickup_km", "peak_intensity",
    "zone_congestion_observed",
    "matched", "accepted", "cancelled", "completed",
)

SELECT_SQL = """
    SELECT b.ts, b.hour, b.dow, b.is_weekend, b.late_night, b.rain,
           b.provider_id, b.mode, b.campus_id, b.campus, b.employee_group,
           b.distance_km, b.pickup_km, b.peak_intensity,
           z.observed_congestion,
           b.matched, b.accepted, b.cancelled, b.completed
      FROM bookings AS b
      JOIN zones    AS z ON z.zone_id = b.zone_id
     WHERE b.provider_id IN ({placeholders})
     ORDER BY b.ts
"""

COUNT_EXCLUDED_SQL = """
    SELECT COUNT(*) FROM bookings WHERE provider_id NOT IN ({placeholders})
"""


def load_table_from_mysql() -> BookingTable | None:
    """The offered-provider booking history as a `BookingTable`, or None."""
    offered = tuple(sorted(OFFERED_PROVIDERS))
    placeholders = ", ".join(["%s"] * len(offered))

    try:
        excluded = int(mysql.scalar(
            COUNT_EXCLUDED_SQL.format(placeholders=placeholders), offered) or 0)
        rows = list(mysql.query_rows(
            SELECT_SQL.format(placeholders=placeholders), offered))
    except mysql.MySQLUnavailable as exc:
        log.info("booking history not read from MySQL (%s) — using the bundled "
                 "CSV", exc)
        return None
    except Exception:
        log.exception("reading the booking history from MySQL failed — using "
                      "the bundled CSV")
        return None

    if not rows:
        log.warning("MySQL holds no bookings for the offered providers — "
                    "falling back to the bundled CSV. Has database/seed_mysql.py "
                    "been run?")
        return None

    # zip(*rows) transposes at C speed, the same trick the CSV loader uses, and
    # for the same reason: 45,000 dictionaries are never materialised.
    columns = list(zip(*rows))
    cols = {name: columns[i] for i, name in enumerate(COLUMNS)}
    # `ts` arrives as datetime objects; BookingTable slices the first ten
    # characters off it to get the date, so it has to be text.
    cols["ts"] = tuple(str(v) for v in cols["ts"])
    for name in ("provider_id", "mode", "campus_id", "campus", "employee_group"):
        cols[name] = tuple(str(v) for v in cols[name])

    table = BookingTable(cols)
    table.excluded_rows = excluded
    log.info("enterprise: loaded %d bookings from MySQL (columnar); %d rows "
             "excluded in SQL for modes JourneyMind no longer offers",
             table.n, excluded)
    return table
