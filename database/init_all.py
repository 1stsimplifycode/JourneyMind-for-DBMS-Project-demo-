"""One command that takes three empty databases to a verified demonstration state.

    python database/init_all.py            # full initialisation, then verify
    python database/init_all.py --warm     # only restore the expiring Redis
                                           # families, then verify
    python database/init_all.py --reset    # drop the MySQL tables first

WHY THIS EXISTS
---------------
The four seed scripts have to run in a particular order, and two of the Redis
key families expire by design. An evaluator should not have to work out which
script restores which family -- that is exactly the kind of undocumented step
that makes a result look irreproducible when it is not.

So this is the documented entry point. It runs the existing scripts, in order,
as separate processes, and then runs the verifier. It does not contain any
seeding logic of its own:

    1. seed_mysql.py         schema + zones + the bundled booking history
    2. seed_neo4j.py         constraints, indexes, stops and links
    3. seed_redis.py         the geocode cache
    4. seed_demo_traffic.py  drives the real API: sessions, attempts, audit
                             events, and the warmed dashboard aggregates
    5. verify.py             counts what is actually there

SEEDING AND VERIFYING STAY SEPARATE
-----------------------------------
This script never writes to a database itself, and `verify.py` never writes to
one either. Step 5 is a different process from steps 1-4 and would report a
failure just as readily if a step above it had done nothing. A verifier that
could top up the data it is about to count would not be a verifier.

WHY A SUBPROCESS PER STEP
-------------------------
`seed_demo_traffic.py` sets REDIS_SESSION_TTL before it imports the application,
because the settings object caches on first read. Running the steps in one
interpreter would let an earlier import freeze the wrong value. Separate
processes make each step start from the environment it documents.

ON THE EXPIRING FAMILIES
------------------------
`jm:session:*` and `jm:agg:*` carry TTLs because the data genuinely is
short-lived -- a booking session is worthless an hour later and a cached
aggregate goes stale. That is correct Redis behaviour and it is not changed
here. It does mean the counts fall over time, so `--warm` re-runs step 4 and
brings both families back above 100 in about a minute, without touching MySQL,
Neo4j or the geocode cache.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import _bootstrap
from _bootstrap import banner, setup

HERE = Path(__file__).resolve().parent

#: (script, argv, what it produces, does --warm need it?)
STEPS = (
    ("seed_mysql.py", [], "MySQL schema, 105 zones, 60,000 bookings", False),
    ("seed_neo4j.py", [], "Neo4j stops and road/transit/transfer links", False),
    ("seed_redis.py", [], "Redis geocode cache (jm:geo:*)", False),
    ("seed_demo_traffic.py", [],
     "MySQL sessions/attempts/audit + Redis jm:session:* and jm:agg:*", True),
)


def run_step(script: str, argv: list[str]) -> int:
    """Run one seed script as its own process, streaming its output."""
    cmd = [sys.executable, str(HERE / script), *argv]
    print(f"\n$ python database/{script} {' '.join(argv)}".rstrip())
    print("-" * 74)
    started = time.perf_counter()
    result = subprocess.run(cmd, cwd=str(HERE.parent))
    took = time.perf_counter() - started
    if result.returncode != 0:
        print(f"\n  !! {script} exited {result.returncode} after {took:.1f}s",
              file=sys.stderr)
    else:
        print(f"  ({script} finished in {took:.1f}s)")
    return result.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--warm", action="store_true",
                    help="only restore the TTL-backed Redis families, then verify")
    ap.add_argument("--reset", action="store_true",
                    help="drop the MySQL tables before reloading them")
    ap.add_argument("--trips", type=int, default=None,
                    help="how many demonstration bookings to run (default 130)")
    ap.add_argument("--skip-verify", action="store_true",
                    help="seed only; do not run database/verify.py afterwards")
    args = ap.parse_args()

    setup()
    banner("JourneyMind — database initialisation"
           + (" (warm only)" if args.warm else ""))

    if args.warm:
        print("Restoring the TTL-backed Redis families (jm:session:*, jm:agg:*).")
        print("MySQL, Neo4j and the geocode cache are left exactly as they are.")
    else:
        print("Initialising all three stores from the bundled datasets, then")
        print("driving the real API to produce the live records.")

    steps = [s for s in STEPS if s[3]] if args.warm else list(STEPS)

    for script, argv, produces, _ in steps:
        argv = list(argv)
        if args.reset and script == "seed_mysql.py":
            argv.append("--reset")
        if args.trips is not None and script == "seed_demo_traffic.py":
            argv += ["--trips", str(args.trips)]
        if run_step(script, argv) != 0:
            print(f"\n  initialisation stopped: {script} failed. The stores it "
                  f"would have filled ({produces}) are unchanged.", file=sys.stderr)
            return 1

    if args.skip_verify:
        print("\n  seeded. Run `python database/verify.py` for the count report.")
        return 0

    # A separate process, on purpose: the thing that counts the data must not
    # be the thing that created it.
    print()
    banner("Verification — counts read back from the running databases")
    return subprocess.run([sys.executable, str(HERE / "verify.py")],
                          cwd=str(HERE.parent)).returncode


if __name__ == "__main__":
    raise SystemExit(main())
