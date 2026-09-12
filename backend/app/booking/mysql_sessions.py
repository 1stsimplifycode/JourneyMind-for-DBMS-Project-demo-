"""The durable record of a booking, written when the booking is over.

WHY A TRANSACTION
-----------------
A session row and its attempt rows describe one event. Writing the session and
then failing to write the attempts would leave a booking in the database that
appears to have cost nothing and taken no time -- and the enterprise dashboard
would then report it as such. So both go inside one transaction: all of it
commits, or none of it does (Unit 4, Lecture 43: atomicity).

The isolation level is left at MySQL's default, REPEATABLE READ. Nothing here
reads a row it then writes, so the stricter SERIALIZABLE would only buy lock
contention; and READ COMMITTED would buy nothing either, because the work is a
single short write. Naming the default explicitly is how a reader knows it was
a decision rather than an omission.

WHY ONLY TERMINAL SESSIONS
--------------------------
A session that is still in flight is going to change -- a retry is a new fare
and a new outcome. Persisting each intermediate state would write four rows to
record one booking and leave the dashboard counting the same trip repeatedly.
Redis holds the session while it is moving; this writes it down when it stops.
"""

from __future__ import annotations

import logging

from ..db import mysql

log = logging.getLogger("journeymind.booking.mysql")

INSERT_SESSION = """
    REPLACE INTO booking_sessions
        (session_id, created_at, departure, provider_id, display_name, mode,
         service_class, origin_label, dest_label, advertised_fare, paid_fare,
         p_match, p_accept, p_cancel, attempt_count, wasted_min,
         settled, exhausted, demo)
    VALUES (%s, FROM_UNIXTIME(%s), %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s)
"""

DELETE_ATTEMPTS = "DELETE FROM booking_attempts WHERE session_id = %s"

INSERT_ATTEMPT = """
    INSERT INTO booking_attempts
        (session_id, attempt_no, fare, outcome, succeeded, wasted_min,
         driver_name, eta_min)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""


def is_terminal(session) -> bool:
    """The booking is over: the rider is travelling, or out of attempts."""
    return bool(session.settled or session.exhausted)


def persist(session) -> bool:
    """Write a finished session and its attempts. False if MySQL is not there.

    Never raises. A booking that could not be written down is a lost record,
    which is bad; a booking that 500s the rider's screen because the database
    was busy is worse, and the rider has already taken the ride either way.
    """
    if not is_terminal(session):
        return False
    try:
        with mysql.transaction() as cur:
            cur.execute(INSERT_SESSION, (
                session.session_id, session.created_at, session.departure,
                session.provider_id, session.display_name, session.mode,
                session.service_class, session.origin_label[:160],
                session.dest_label[:160],
                round(session.base_fare, 2),
                round(session.total_paid, 2) if session.settled else None,
                round(session.p_match, 5), round(session.p_accept, 5),
                round(session.p_cancel, 5),
                len(session.attempts), round(session.wasted_min, 2),
                session.settled, session.exhausted, session.demo,
            ))
            # REPLACE on the parent cascades the old attempts away, but a
            # re-persist of the same session (a retry that settles it) must not
            # leave attempt rows from the previous write behind either.
            cur.execute(DELETE_ATTEMPTS, (session.session_id,))
            cur.executemany(INSERT_ATTEMPT, [
                (session.session_id, a.number, round(a.fare, 2),
                 a.outcome.value, a.succeeded, round(a.wasted_min, 2),
                 a.driver_name, round(a.eta_min, 2) if a.eta_min is not None else None)
                for a in session.attempts
            ])
        return True
    except mysql.MySQLUnavailable as exc:
        log.info("booking %s not persisted (%s)", session.session_id, exc)
        return False
    except Exception:
        log.exception("persisting booking %s failed; the ride itself is "
                      "unaffected", session.session_id)
        return False
