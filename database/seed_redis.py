"""Pre-warm the Redis geocode cache from the bundled study-area network.

    python database/seed_redis.py [--flush]

WHAT THIS SEEDS AND WHY IT IS NOT FILLER
----------------------------------------
`app/services/geocode.py` resolves a typed place name in three steps: the
fifteen named places in the bundle, then the cache, then Nominatim over the
public internet. Everything between those fifteen names and the wider world --
every metro station, every bus stop, every named junction in the corridor --
went to Nominatim, one second apart, and came back with whatever OpenStreetMap
thought "Banashankari" meant.

The bundle already knows exactly where those places are, because the router
stands on them. Loading them into the cache means a rider typing "Indiranagar"
or "South End Circle" gets the corridor's own coordinate instantly and offline,
and Nominatim is only asked about names the study area has genuinely never
heard of.

So every key written here is a name the geocoder is actually asked for, with an
answer the application actually uses. The other two Redis families -- live
sessions and cached aggregates -- are written by `seed_demo_traffic.py`,
because they are produced by running the product rather than by loading a file.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys

import _bootstrap
from _bootstrap import banner, city_dir, setup


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--flush", action="store_true",
                    help="delete this application's keys before seeding")
    args = ap.parse_args()

    setup()
    banner("Redis — geocode cache for the study-area network")

    from app.db import redis_store
    from app.services.geocode import redis_key

    redis_store.reset_client()
    if not redis_store.available():
        print(f"  Redis is not reachable: {redis_store.unavailable_reason()}",
              file=sys.stderr)
        return 1

    if args.flush:
        removed = 0
        for pattern in ("geo", "session", "agg", "audit"):
            keys = list(redis_store.scan_keys(redis_store.key(pattern, "*")))
            keys += [redis_store.key(pattern, "recent")]
            removed += redis_store.delete(*{k for k in keys if k})
        print(f"  --flush: removed {removed} existing keys")

    # Named places first: they are what the UI offers in the From / To lists,
    # so their names are the ones most likely to be typed.
    places = json.loads((city_dir() / "places.json").read_text(encoding="utf-8"))
    entries: dict[str, tuple[float, float, str]] = {
        p["name"].strip().lower(): (float(p["lat"]), float(p["lon"]), p["name"])
        for p in places if p.get("name", "").strip()
    }

    # ...then every other vertex of the network. `setdefault` means a named
    # place always wins over a node that happens to share its name.
    with open(city_dir() / "nodes.csv", newline="", encoding="utf-8") as fh:
        for n in csv.DictReader(fh):
            name = (n["name"] or "").strip()
            if not name:
                continue
            entries.setdefault(name.lower(),
                               (float(n["lat"]), float(n["lon"]), name))

    written = 0
    for query, (lat, lon, name) in sorted(entries.items()):
        # No TTL. These are positions of physical infrastructure in a bundled
        # dataset: they do not go stale, and expiring them would only send the
        # next rider who types the name back out to the internet.
        if redis_store.set_json(redis_key(query), [lat, lon, name], ttl_s=None):
            written += 1

    print(f"  jm:geo:*  {written} cached place lookups "
          f"({len(places)} named places + every other named network node)")
    print("\n  done. Sessions and cached aggregates come from "
          "database/seed_demo_traffic.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
