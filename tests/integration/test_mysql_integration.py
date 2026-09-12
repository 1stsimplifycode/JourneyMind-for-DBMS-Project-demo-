"""MySQL is really connected, really seeded, and really written to by the app.

Three different claims, tested separately:
  connection   -> a trivial statement round-trips
  seeded data  -> the bundled history is present, with its integrity intact
  write path   -> pressing BOOK NOW through the real API lands rows in MySQL
"""

import pytest

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------- connect
def test_connection_round_trips(mysql):
    assert mysql.scalar("SELECT 1") == 1


def test_schema_has_exactly_the_documented_tables(mysql):
    """No extra application TABLE exists that the 100+ report omits.

    Base tables only. `SHOW TABLES` also lists views, and a view holds no rows
    of its own, so counting one as a table would mean the data-volume report
    had to cover something that stores nothing.
    """
    found = {r["TABLE_NAME"] for r in mysql.query(
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'")}
    assert found == {"zones", "bookings", "booking_sessions",
                     "booking_attempts", "audit_events"}


def test_the_reporting_view_exists_and_answers(mysql):
    """v_campus_mobility is a saved SELECT, not a copy of the data."""
    views = {r["TABLE_NAME"] for r in mysql.query(
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'VIEW'")}
    assert "v_campus_mobility" in views

    rows = mysql.query("SELECT campus, bookings, completed_trips, completion_pct "
                       "FROM v_campus_mobility ORDER BY bookings DESC LIMIT 5")
    assert rows, "the view returned nothing"
    for r in rows:
        assert r["bookings"] > 0
        assert 0 <= float(r["completion_pct"]) <= 100
        assert r["completed_trips"] <= r["bookings"]


# ---------------------------------------------------------------- seeded reads
def test_bundled_history_is_loaded(mysql):
    assert int(mysql.scalar("SELECT COUNT(*) FROM zones")) >= 100
    assert int(mysql.scalar("SELECT COUNT(*) FROM bookings")) >= 100


def test_a_real_aggregate_query_answers(mysql):
    """The shape of question the dashboard asks: filter, group, aggregate."""
    rows = mysql.query(
        """SELECT campus_id,
                  COUNT(*)      AS trips,
                  AVG(distance_km) AS avg_km
             FROM bookings
            WHERE completed = TRUE
            GROUP BY campus_id
            ORDER BY trips DESC""")
    assert rows, "no completed trips to aggregate"
    assert all(r["trips"] > 0 and r["avg_km"] > 0 for r in rows)


def test_join_across_the_foreign_key(mysql):
    """bookings -> zones is a real FK an evaluator can join on."""
    rows = mysql.query(
        """SELECT z.kind, COUNT(*) AS n
             FROM bookings b
             JOIN zones z ON z.zone_id = b.zone_id
            GROUP BY z.kind""")
    assert rows
    assert sum(r["n"] for r in rows) == int(mysql.scalar("SELECT COUNT(*) FROM bookings"))


def test_referential_integrity_holds(mysql):
    orphan_bookings = int(mysql.scalar(
        "SELECT COUNT(*) FROM bookings b "
        "LEFT JOIN zones z ON z.zone_id = b.zone_id WHERE z.zone_id IS NULL"))
    orphan_attempts = int(mysql.scalar(
        "SELECT COUNT(*) FROM booking_attempts a "
        "LEFT JOIN booking_sessions s ON s.session_id = a.session_id "
        "WHERE s.session_id IS NULL"))
    assert orphan_bookings == 0
    assert orphan_attempts == 0


def test_foreign_key_is_enforced_not_decorative(mysql):
    """Inserting a booking for a zone that does not exist must be refused."""
    with pytest.raises(Exception) as exc:
        mysql.execute(
            """INSERT INTO bookings
                   (booking_id, ts, hour, dow, is_weekend, late_night, rain,
                    provider_id, mode, campus_id, campus, employee_group,
                    cost_centre, zone_id, distance_km, pickup_km,
                    peak_intensity, short_trip_penalty,
                    matched, accepted, cancelled, completed)
               VALUES ('itest_fk_violation', '2026-01-01 09:00:00', 9, 1, 0, 0, 0,
                       'cab', 'cab', 'cmp_x', 'X', 'Eng', 'CC1',
                       'zone_that_does_not_exist', 1, 1, 0.1, 0, 1, 1, 0, 1)""")
    assert "foreign key" in str(exc.value).lower() or "1452" in str(exc.value)


# ----------------------------------------------------------------- write path
def test_booking_through_the_api_is_persisted(mysql, client):
    """BOOK NOW -> /api/book -> mysql_sessions.persist() -> two tables, one txn."""
    before_s = int(mysql.scalar("SELECT COUNT(*) FROM booking_sessions"))
    before_a = int(mysql.scalar("SELECT COUNT(*) FROM booking_attempts"))

    cmp_ = client.post("/api/compare", json={
        "origin": "pl_home", "destination": "pl_college", "priority": "balanced"})
    assert cmp_.status_code == 200
    provider = cmp_.json()["recommended_provider"]

    booked = client.post("/api/book", json={
        "origin": "pl_home", "destination": "pl_college",
        "provider_id": provider, "priority": "balanced", "demo": True})
    assert booked.status_code == 200
    session = booked.json()["session"]
    sid = session["session_id"]

    while session.get("can_retry"):
        again = client.post(f"/api/book/{sid}/retry")
        assert again.status_code == 200
        session = again.json()["session"]

    # The session is terminal, so it must now be in MySQL.
    row = mysql.query(
        "SELECT * FROM booking_sessions WHERE session_id = %s", [sid])
    assert row, "a finished booking was not written to MySQL"
    assert row[0]["provider_id"] == provider
    assert bool(row[0]["settled"]) or bool(row[0]["exhausted"])

    attempts = mysql.query(
        "SELECT attempt_no, fare, outcome FROM booking_attempts "
        "WHERE session_id = %s ORDER BY attempt_no", [sid])
    assert len(attempts) == len(session["attempts"]), \
        "attempt rows and the session disagree about how many attempts ran"
    assert [a["attempt_no"] for a in attempts] == list(range(1, len(attempts) + 1))

    assert int(mysql.scalar("SELECT COUNT(*) FROM booking_sessions")) == before_s + 1
    assert int(mysql.scalar("SELECT COUNT(*) FROM booking_attempts")) > before_a


def test_audit_events_are_written_by_a_real_request(mysql, client, api_key):
    before = int(mysql.scalar("SELECT COUNT(*) FROM audit_events"))
    r = client.get("/api/enterprise/overview", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    after = int(mysql.scalar("SELECT COUNT(*) FROM audit_events"))
    assert after > before, "an enterprise query recorded no audit event"

    newest = mysql.query(
        "SELECT kind, actor FROM audit_events ORDER BY event_id DESC LIMIT 1")
    assert newest[0]["kind"] == "enterprise_query"
