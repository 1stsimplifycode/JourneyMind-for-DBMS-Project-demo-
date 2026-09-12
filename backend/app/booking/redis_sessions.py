"""Live booking sessions, held in Redis.

WHY THIS IS THE RIGHT STORE FOR THIS DATA
-----------------------------------------
A booking session is the textbook key-value record (Unit 4, Lecture 49,
"Session storage"):

    it is read by one key and never searched   -> no index is wanted
    it is rewritten on every retry             -> a hash field update, not a row
    it is worthless forty-five minutes later   -> a TTL, not a delete job
    losing it costs one demonstration          -> no durability requirement

Putting it in MySQL would mean a table whose rows are all deleted within the
hour, a cleanup job to do the deleting, and a write per state change on a
storage engine built for durability nobody here needs. Putting it in Neo4j
would be stranger still: nothing about a session is a path.

WHAT MYSQL DOES GET
-------------------
The moment a session reaches a terminal state, `mysql_sessions.persist()`
writes the durable record -- what was advertised, what was paid, what the
failures cost. Redis holds the session while it is alive; MySQL holds the fact
that it happened. Those are different questions and they get different stores.

THE RANDOM GENERATOR IS PART OF THE SESSION
-------------------------------------------
`session.rng` is serialised with the rest of it. That is not an
implementation detail: the retry sequence is a continuing draw from one seeded
stream, and a session rehydrated with a fresh generator would restart the
stream and quietly break the determinism `demo_seed` exists to provide.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import numpy as np

from ..db import redis_store
from ..db.settings import get_db_settings
from ..lifecycle.expected_cost import LifecycleParams
from ..lifecycle.states import BookingState

log = logging.getLogger("journeymind.booking.redis")


def session_key(session_id: str) -> str:
    return redis_store.key("session", session_id)


#: The hash fields that are plain scalars, and how to read each one back.
_SCALARS: dict[str, type] = {
    "provider_id": str, "display_name": str, "mode": str, "service_class": str,
    "origin_label": str, "dest_label": str,
    "base_fare": float, "pickup_min": float, "ride_min": float,
    "p_match": float, "p_accept": float, "p_cancel": float,
    "created_at": float,
}


def to_hash(session) -> dict[str, str]:
    """A BookingSession as flat hash fields.

    Flat, and not one JSON blob, because the fields a human inspecting Redis
    would want -- which provider, how many attempts, did it settle -- are then
    readable with `HGET` at a `redis-cli` prompt instead of needing a parser.
    The two genuinely nested parts (the attempts and the frozen comparison) are
    JSON inside their own fields.
    """
    out = {name: str(getattr(session, name)) for name in _SCALARS}
    out.update({
        "session_id": session.session_id,
        "departure": session.departure.isoformat(),
        "demo": "1" if session.demo else "0",
        # Denormalised for readability at the CLI; both are derived from
        # `attempts` and are recomputed, never trusted, on the way back in.
        "attempt_count": str(len(session.attempts)),
        "settled": "1" if session.settled else "0",
        "params": json.dumps(session.params.__dict__),
        "rng_state": json.dumps(session.rng.bit_generator.state),
        "attempts": json.dumps([_attempt_to_dict(a) for a in session.attempts]),
        "comparison": json.dumps(session.comparison) if session.comparison else "",
    })
    return out


def _attempt_to_dict(attempt) -> dict:
    return {
        "number": attempt.number,
        "fare": attempt.fare,
        "outcome": attempt.outcome.value,
        "wasted_min": attempt.wasted_min,
        "driver_name": attempt.driver_name,
        "eta_min": attempt.eta_min,
        "steps": [s.as_dict() for s in attempt.steps],
    }


def from_hash(raw: dict[str, str]):
    """Rebuild a BookingSession, or None if the hash is not one.

    Imported lazily to keep this module free of a circular import: session.py
    owns the dataclasses and reaches for this module from inside SessionStore.
    """
    from .session import Attempt, BookingSession, Step

    try:
        rng = np.random.default_rng()
        rng.bit_generator.state = json.loads(raw["rng_state"])

        attempts = []
        for a in json.loads(raw.get("attempts") or "[]"):
            attempts.append(Attempt(
                number=int(a["number"]),
                fare=float(a["fare"]),
                steps=[Step(**s) for s in a.get("steps", [])],
                outcome=BookingState(a["outcome"]),
                wasted_min=float(a["wasted_min"]),
                driver_name=a.get("driver_name"),
                eta_min=a.get("eta_min"),
            ))

        return BookingSession(
            session_id=raw["session_id"],
            departure=datetime.fromisoformat(raw["departure"]),
            params=LifecycleParams(**json.loads(raw["params"])),
            rng=rng,
            attempts=attempts,
            demo=raw.get("demo") == "1",
            comparison=json.loads(raw["comparison"]) if raw.get("comparison") else None,
            **{name: cast(raw[name]) for name, cast in _SCALARS.items()},
        )
    except (KeyError, ValueError, TypeError) as exc:
        # A hash we cannot read is a hash written by an older shape of this
        # code. Say so and treat the session as expired rather than 500.
        log.warning("discarding an unreadable session hash (%s: %s)",
                    type(exc).__name__, exc)
        return None


# --------------------------------------------------------------------------
def put(session) -> bool:
    """Write the session and (re)set its expiry. False if Redis is not there."""
    s = get_db_settings().redis
    ttl = s.demo_ttl_s if session.demo else s.session_ttl_s
    return redis_store.hset_all(session_key(session.session_id), to_hash(session), ttl)


def get(session_id: str):
    raw = redis_store.hgetall(session_key(session_id))
    return from_hash(raw) if raw else None


def count() -> int:
    """How many live sessions Redis is holding. Used by the volume check."""
    return redis_store.count_keys(redis_store.key("session", "*"))
