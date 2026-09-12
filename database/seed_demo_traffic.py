"""Produce the live-traffic records by running the product, not by inventing rows.

    python database/seed_demo_traffic.py [--trips 130] [--no-enterprise]

WHY THIS EXISTS
---------------
Three of the data families cannot be loaded from a file, because they are not
facts about the world -- they are records of the application doing its job:

    MySQL  booking_sessions / booking_attempts   what happened when somebody
                                                 pressed BOOK NOW
    MySQL  audit_events                          every decision, and the
                                                 evidence behind it
    Redis  jm:session:*                          those bookings while they were
                                                 still in flight
    Redis  jm:agg:*                              dashboard payloads an analyst
                                                 asked for
    Redis  jm:audit:recent                       the hot tail of the audit trail

The only honest way to create them is to run the real thing. So this script
drives the actual HTTP API in-process: every row below is written by the same
`/api/compare`, `/api/book`, `/api/book/{id}/retry` and
`/api/enterprise/overview` handlers a browser hits, through the same engine,
the same reliability model and the same session store. Nothing here constructs
a row directly, and there is no seeding-only code path that could drift from
the serving one.

REPRODUCIBLE, BUT NOT UNIFORM
-----------------------------
The trip list is generated deterministically -- the same origins, destinations,
departure times, priorities and weather on every run. The booking OUTCOMES are
not fixed, because they are genuine draws from the reliability model's own
probabilities; forcing them all down the demonstration branch would fill the
table with 130 identical failures and misrepresent the very distribution the
product exists to measure. Every sixth trip does use `demo: true`, which fixes
the dice so a presenter can reproduce the interesting failure on stage; those
rows carry `demo = TRUE` in MySQL so they are distinguishable.

SESSION LIFETIME
----------------
A real booking session expires 45 minutes after it opens. These are written
with REDIS_DEMO_TTL instead (a week by default), so the data-volume
verification can be re-run tomorrow and still find them. That is a property of
the seeded keys, not a change to the server: a live rider's session still
expires in 45 minutes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import _bootstrap
from _bootstrap import banner, setup

#: How many of the 210 ordered place pairs to use.
DEFAULT_TRIPS = 130

#: Every Nth trip is booked with the dice fixed, giving a reproducible
#: demonstration run inside an otherwise naturally-varying population.
DEMO_EVERY = 6

PRIORITIES = ("balanced", "cheapest", "fastest", "reliable")


def trip_plan(places: list[dict], n: int) -> list[dict]:
    """A deterministic spread of trips across the corridor and the day.

    Ordered pairs are walked with a stride that is coprime to the number of
    places, so consecutive trips do not share an origin and the whole set of
    pairs is covered evenly rather than the first few being used repeatedly.
    """
    ids = [p["place_id"] for p in places]
    k = len(ids)
    # A Monday, so the first week of trips spans a full working week.
    base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    base -= timedelta(days=base.weekday())

    trips = []
    for i in range(n):
        o = ids[i % k]
        d = ids[(i * 7 + 3) % k]
        if o == d:
            d = ids[(i * 7 + 4) % k]
        # 06:00 to 22:00, stepping through the day so peaks and off-peak hours
        # are both represented; rain on roughly one trip in five.
        hour = 6 + (i * 5) % 17
        minute = (i * 13) % 60
        when = base + timedelta(days=i % 5, hours=hour, minutes=minute)
        trips.append({
            "origin": o,
            "destination": d,
            "departure_time": when.isoformat(timespec="minutes"),
            "priority": PRIORITIES[i % len(PRIORITIES)],
            "rain": i % 5 == 0,
            "demo": i % DEMO_EVERY == 0,
        })
    return trips


def enterprise_selections(facets: dict) -> list[dict]:
    """Every filter selection the dashboard's own dropdowns can produce with at
    most two of them set.

    The Enterprise screen has four independent dropdowns -- Campus, Provider,
    Team, Mode -- and nothing else; the date range beside them is a label, not
    a control. So the set of selections a person can actually ask for is every
    combination of those four, and the ones they actually ask for are the
    shallow ones: clear the filters, pick a campus, then add a team to it.

    "At most two set" is therefore the rule, and it is the whole rule -- not a
    hand-picked list of the pairs that happened to look interesting. Warming
    three- and four-deep selections as well would be warming entries an analyst
    reaches once a month, and a cache entry nothing reads is not a cache entry.
    """
    dimensions = [
        ("campus", [c["id"] for c in facets.get("campuses", [])]),
        ("provider", list(facets.get("providers", []))),
        ("employee_group", list(facets.get("employee_groups", []))),
        ("mode", list(facets.get("modes", []))),
    ]

    out: list[dict] = [{}]                                  # filters cleared
    for name, values in dimensions:
        out += [{name: v} for v in values]                  # one dropdown set
    for i, (a_name, a_values) in enumerate(dimensions):     # two dropdowns set
        for b_name, b_values in dimensions[i + 1:]:
            out += [{a_name: a, b_name: b} for a in a_values for b in b_values]
    return out


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trips", type=int, default=DEFAULT_TRIPS)
    ap.add_argument("--no-enterprise", action="store_true",
                    help="skip the dashboard warm-up pass")
    args = ap.parse_args()

    setup()

    # Seeded sessions outlive a real one on purpose -- see the module docstring.
    # Set before anything imports the settings, which cache on first read.
    os.environ.setdefault("REDIS_SESSION_TTL",
                          os.getenv("REDIS_DEMO_TTL", str(7 * 24 * 3600)))

    banner("Demo traffic — driving the real API to produce live records")

    from fastapi.testclient import TestClient

    from app.main import app
    from app.security import DEMO_KEY, get_keystore

    with TestClient(app) as client:
        places = client.get("/api/places").json()["places"]
        print(f"  study area offers {len(places)} named places")

        trips = trip_plan(places, args.trips)
        booked = attempts = settled = exhausted = 0
        skipped = 0

        for i, trip in enumerate(trips, 1):
            r = client.post("/api/compare", json={
                "origin": trip["origin"], "destination": trip["destination"],
                "departure_time": trip["departure_time"],
                "priority": trip["priority"], "rain": trip["rain"],
            })
            if r.status_code != 200:
                skipped += 1
                continue
            comparison = r.json()
            provider = comparison.get("recommended_provider")
            if not provider:
                skipped += 1
                continue

            b = client.post("/api/book", json={
                "origin": trip["origin"], "destination": trip["destination"],
                "provider_id": provider,
                "departure_time": trip["departure_time"],
                "priority": trip["priority"], "rain": trip["rain"],
                "demo": trip["demo"],
            })
            if b.status_code != 200:
                skipped += 1
                continue
            session = b.json()["session"]
            booked += 1
            attempts += 1

            # Keep pressing TRY AGAIN exactly as a stuck rider would, until the
            # ride is settled or the attempt budget is gone.
            while session.get("can_retry"):
                again = client.post(f"/api/book/{session['session_id']}/retry")
                if again.status_code != 200:
                    break
                session = again.json()["session"]
                attempts += 1

            settled += bool(session.get("settled"))
            exhausted += bool(session.get("exhausted"))

            if i % 25 == 0:
                print(f"  ... {i}/{len(trips)} trips")

        print(f"\n  bookings run     : {booked}")
        print(f"  attempts run     : {attempts}")
        print(f"  ended settled    : {settled}")
        print(f"  ended exhausted  : {exhausted}")
        if skipped:
            print(f"  trips with no bookable option: {skipped}")

        if args.no_enterprise:
            return 0

        # --- the dashboard pass -------------------------------------------
        # Enterprise endpoints are gated. In demo mode a single clearly-named
        # key is enabled, which is the one used here; a configured deployment
        # would pass one of its own.
        store = get_keystore()
        if not store.keys:
            print("\n  enterprise endpoints are closed on this configuration "
                  "(set JM_API_KEYS or DEMO_MODE=true) — skipping the "
                  "dashboard pass", file=sys.stderr)
            return 0
        api_key = DEMO_KEY if store.demo_enabled else os.getenv("JM_SEED_API_KEY", "")
        if not api_key:
            print("\n  no usable API key for the enterprise pass; set "
                  "JM_SEED_API_KEY", file=sys.stderr)
            return 0
        headers = {"X-API-Key": api_key}

        facets = client.get("/api/enterprise/facets", headers=headers)
        if facets.status_code != 200:
            print(f"\n  enterprise facets refused ({facets.status_code}) — "
                  "skipping the dashboard pass", file=sys.stderr)
            return 0

        selections = enterprise_selections(facets.json()["facets"])
        warmed = empty = 0
        for sel in selections:
            r = client.get("/api/enterprise/overview", params=sel, headers=headers)
            if r.status_code != 200:
                continue
            warmed += 1
            if not r.json()["overview"].get("bookings"):
                empty += 1

        print(f"\n  dashboard payloads computed and cached: {warmed}")
        if empty:
            print(f"  ({empty} of them match no bookings — still a real answer "
                  f"an analyst can ask for, and still worth caching)")

    print("\n  done. Run database/verify.py for the data-volume report.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
