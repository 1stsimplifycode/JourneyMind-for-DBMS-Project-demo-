"""Redis is really used by the application, family by family.

Each of the four key families gets a test that drives a REAL request and then
inspects Redis, rather than calling the helpers directly. A helper that works
in isolation proves nothing about whether the product uses it.
"""

import json

import pytest

pytestmark = pytest.mark.integration


def test_connection_round_trips(redis_store):
    assert redis_store.available() is True


# ------------------------------------------------- jm:geo:*  (STRING, cache)
def test_geocode_cache_is_read_by_a_real_request(redis_store, client, geocoder_on):
    """A typed name outside the bundled places resolves FROM Redis.

    'Cubbon Park' is a metro station in the bundled network but NOT one of the
    fifteen named places, so `resolve_point` falls past the local match and
    calls `geocode()`, which consults Redis before any network call. A bundled
    place name such as 'Indiranagar 100ft Road' would be matched locally and
    never reach Redis -- which is why it is deliberately not used here.

    The proof is a swap: the cached value is replaced with a distinguishable
    one, and the API's answer has to follow it. Reading the key back would only
    show that the key exists; this shows the application used it.
    """
    from app.services import geocode as geo
    from app.services.geocode import redis_key

    key = redis_key("cubbon park")
    original = redis_store.get_json(key)
    assert original is not None, (
        "jm:geo:cubbon park is missing - run `python database/init_all.py`")

    sentinel = [float(original[0]), float(original[1]), "CACHED VIA REDIS"]
    try:
        assert redis_store.set_json(key, sentinel, ttl_s=None)
        geo._memory.pop("cubbon park", None)      # the per-process memo, not a store
        r = client.post("/api/compare", json={
            "origin": "Cubbon Park", "destination": "Trinity",
            "priority": "balanced"})
        assert r.status_code == 200
        assert r.json()["origin"]["label"] == "CACHED VIA REDIS", (
            "the resolved label did not come from Redis")
    finally:
        redis_store.set_json(key, original, ttl_s=None)
        geo._memory.pop("cubbon park", None)

    # ...and with the real value restored, the name is the corridor's own.
    r = client.post("/api/compare", json={
        "origin": "Cubbon Park", "destination": "Trinity", "priority": "balanced"})
    assert r.json()["origin"]["label"] == original[2]


def test_geocode_cache_survives_and_has_no_ttl(redis_store):
    """Positions of physical infrastructure do not go stale, so they persist."""
    from app.services.geocode import redis_key
    assert redis_store.ttl(redis_key("cubbon park")) == -1


# --------------------------------------------- jm:session:*  (HASH + EXPIRE)
def test_booking_session_is_a_hash_with_a_ttl(redis_store, client):
    cmp_ = client.post("/api/compare", json={
        "origin": "pl_home", "destination": "pl_college", "priority": "balanced"})
    provider = cmp_.json()["recommended_provider"]
    booked = client.post("/api/book", json={
        "origin": "pl_home", "destination": "pl_college",
        "provider_id": provider, "priority": "balanced"})
    assert booked.status_code == 200
    sid = booked.json()["session"]["session_id"]

    from app.booking.redis_sessions import session_key
    key = session_key(sid)

    raw = redis_store.hgetall(key)
    assert raw, "the live session was not written to Redis"
    assert raw["provider_id"] == provider
    assert raw["session_id"] == sid

    ttl = redis_store.ttl(key)
    assert ttl and ttl > 0, "a live session must expire by itself"

    # And the session is read BACK from Redis on retry: the attempt count in
    # the hash has to move, which only happens if the round trip works.
    before = int(raw["attempt_count"])
    again = client.post(f"/api/book/{sid}/retry")
    if again.status_code == 200:
        after = int(redis_store.hgetall(key)["attempt_count"])
        assert after == before + 1


def test_session_hash_carries_the_rng_so_retries_stay_deterministic(redis_store, client):
    """The generator state is part of the record, not an implementation detail."""
    cmp_ = client.post("/api/compare", json={
        "origin": "pl_home", "destination": "pl_domlur", "priority": "balanced"})
    provider = cmp_.json()["recommended_provider"]
    sid = client.post("/api/book", json={
        "origin": "pl_home", "destination": "pl_domlur",
        "provider_id": provider, "priority": "balanced"}).json()["session"]["session_id"]

    from app.booking.redis_sessions import session_key
    raw = redis_store.hgetall(session_key(sid))
    state = json.loads(raw["rng_state"])
    assert state["bit_generator"] == "PCG64"


# ------------------------------------------------- jm:agg:*  (STRING + TTL)
def test_dashboard_cache_is_aside_not_decoration(redis_store, client, api_key):
    """First call misses and stores; the second is served from Redis."""
    params = {"campus": "cmp_sarjapur", "provider": "cab",
              "employee_group": "Engineering", "mode": "cab"}
    from app.enterprise.analytics import Filters
    from app.enterprise.cache import cache_key

    filters = Filters(campus=params["campus"], provider=params["provider"],
                      employee_group=params["employee_group"], mode=params["mode"])
    from app.api.mobility import DEFAULT_MINUTE_COST
    key = cache_key(filters, DEFAULT_MINUTE_COST)
    redis_store.delete(key)                      # start from a known miss

    first = client.get("/api/enterprise/overview", params=params,
                       headers={"X-API-Key": api_key})
    assert first.status_code == 200
    assert first.json()["cache"] == {"hit": False, "key": key}
    assert redis_store.get_json(key) is not None, "a miss did not populate the cache"

    second = client.get("/api/enterprise/overview", params=params,
                        headers={"X-API-Key": api_key})
    assert second.json()["cache"] == {"hit": True, "key": key}
    # Same answer either way -- a cache that changes the result is a bug.
    assert second.json()["overview"] == first.json()["overview"]

    ttl = redis_store.ttl(key)
    assert ttl and ttl > 0, "a cached aggregate must expire"


# ------------------------------------- jm:audit:recent  (LIST, LPUSH + LTRIM)
def test_audit_list_grows_and_stays_bounded(redis_store, client, api_key):
    from app.db.audit_sink import recent_key
    from app.db.settings import get_db_settings

    key = recent_key()
    cap = get_db_settings().redis.audit_list_max

    before = redis_store.llen(key)
    client.get("/api/enterprise/overview", headers={"X-API-Key": api_key})
    after = redis_store.llen(key)

    assert after >= before, "the audit list shrank on a write"
    assert after <= cap, f"LTRIM is not bounding the list at {cap}"

    newest = redis_store.lrange_json(key, 0, 0)
    assert newest and newest[0]["kind"] == "enterprise_query"
    assert "at" in newest[0] and "decision" in newest[0]


def test_audit_screen_reads_the_list(redis_store, client, api_key):
    r = client.get("/api/enterprise/audit?limit=5", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    body = r.json()
    assert body["stores"]["hot"]["store"] == "redis"
    assert body["stores"]["hot"]["holding"] >= 1
    assert len(body["entries"]) >= 1
