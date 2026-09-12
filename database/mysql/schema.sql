-- ---------------------------------------------------------------------------
-- JourneyMind — MySQL schema
--
-- WHAT LIVES IN MYSQL AND WHY
-- ---------------------------
-- Records that are durable, strongly typed, related to each other by key, and
-- queried by attribute rather than by identifier:
--
--   zones             the pickup/drop zones the demand history refers to
--   bookings          the enterprise booking history (the fact table)
--   booking_sessions  bookings this instance actually made, one per BOOK NOW
--   booking_attempts  each attempt inside a session, and how it ended
--   audit_events      every AI decision, with the evidence behind it
--
-- Redis could hold none of this: "total spend for Engineering at the Sarjapur
-- campus between two dates, excluding cancelled trips" is a filtered
-- aggregate over 60,000 rows, and a key-value store has no way to answer it
-- except by reading every value. Neo4j could hold it, but none of these
-- questions is about a path — nothing here traverses.
--
-- WHY THERE IS NO campuses / providers / employee_groups TABLE
-- -----------------------------------------------------------
-- Those columns have 5, 4 and 5 distinct values in sixty thousand rows. A
-- five-row dimension table would add a join to every query and buy nothing:
-- there are no further attributes to hang off it, and no integrity risk a
-- CHECK constraint does not already cover. `zones` IS normalised out, because
-- it carries real attributes (position, congestion) that the booking row has
-- no business repeating 60,000 times.
--
-- Run with:  mysql -u <user> -p < database/mysql/schema.sql
-- ---------------------------------------------------------------------------

CREATE DATABASE IF NOT EXISTS journeymind
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE journeymind;

-- ---------------------------------------------------------------------------
-- zones — where a trip starts. 105 rows, one per zone the history refers to.
--
-- A zone is a metro station, a bus stop or a named place in the study
-- corridor. `observed_congestion` and `latent_congestion` are the demand
-- signals the travel-time model was trained against; holding them here once
-- rather than on every booking row is the normalisation that makes this table
-- worth existing.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS zones (
    zone_id              VARCHAR(64)   NOT NULL,
    name                 VARCHAR(128)  NOT NULL,
    kind                 ENUM('metro_station', 'bus_stop', 'place', 'junction')
                                       NOT NULL,
    lat                  DECIMAL(9, 6) NOT NULL,
    lon                  DECIMAL(9, 6) NOT NULL,
    latent_congestion    DECIMAL(6, 5) NOT NULL DEFAULT 0,
    observed_congestion  DECIMAL(6, 5) NOT NULL DEFAULT 0,
    PRIMARY KEY (zone_id),
    KEY idx_zones_kind (kind),
    CONSTRAINT chk_zones_lat CHECK (lat BETWEEN -90 AND 90),
    CONSTRAINT chk_zones_lon CHECK (lon BETWEEN -180 AND 180),
    CONSTRAINT chk_zones_congestion CHECK (
        latent_congestion BETWEEN 0 AND 1 AND observed_congestion BETWEEN 0 AND 1)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------------
-- bookings — the enterprise booking history. 60,000 rows.
--
-- The fact table the whole enterprise dashboard aggregates over. Every row is
-- one request for a vehicle and the four booleans that say how far it got:
-- matched (did anybody respond), accepted (did they take it), cancelled (did
-- they then drop it), completed (did the employee actually travel).
--
-- Those four are what makes this a mobility dataset rather than a spend
-- report: a booking that failed still cost the employee time, and a table that
-- only held completed trips could not see it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bookings (
    booking_id       VARCHAR(32)   NOT NULL,
    ts               DATETIME      NOT NULL,
    hour             DECIMAL(6, 3) NOT NULL,
    dow              TINYINT       NOT NULL,
    is_weekend       BOOLEAN       NOT NULL DEFAULT FALSE,
    late_night       BOOLEAN       NOT NULL DEFAULT FALSE,
    rain             BOOLEAN       NOT NULL DEFAULT FALSE,
    -- the operator and the vehicle are different facts and are kept apart
    provider_id      VARCHAR(24)   NOT NULL,
    mode             VARCHAR(24)   NOT NULL,
    campus_id        VARCHAR(32)   NOT NULL,
    campus           VARCHAR(64)   NOT NULL,
    employee_group   VARCHAR(32)   NOT NULL,
    cost_centre      VARCHAR(32)   NOT NULL,
    -- the zone dimension, normalised out into `zones`
    zone_id          VARCHAR(64)   NOT NULL,
    distance_km      DECIMAL(7, 3) NOT NULL,
    pickup_km        DECIMAL(7, 3) NOT NULL,
    peak_intensity   DECIMAL(6, 5) NOT NULL,
    short_trip_penalty DECIMAL(6, 5) NOT NULL DEFAULT 0,
    matched          BOOLEAN       NOT NULL DEFAULT FALSE,
    accepted         BOOLEAN       NOT NULL DEFAULT FALSE,
    cancelled        BOOLEAN       NOT NULL DEFAULT FALSE,
    completed        BOOLEAN       NOT NULL DEFAULT FALSE,
    PRIMARY KEY (booking_id),
    -- Referential integrity: a booking cannot name a zone that does not exist,
    -- and a zone still referenced by history cannot be deleted out from under
    -- it. RESTRICT rather than CASCADE on purpose — silently deleting sixty
    -- thousand facts because a dimension row went away is not a repair.
    CONSTRAINT fk_bookings_zone FOREIGN KEY (zone_id)
        REFERENCES zones (zone_id) ON DELETE RESTRICT ON UPDATE CASCADE,
    -- The dashboard filters on exactly these, so each gets an index. `ts`
    -- carries the date-range filter; the composite matches the common
    -- "one campus, one provider" drill-down without a second lookup.
    KEY idx_bookings_ts (ts),
    KEY idx_bookings_zone (zone_id),
    KEY idx_bookings_hour (hour),
    KEY idx_bookings_campus_provider (campus_id, provider_id),
    KEY idx_bookings_group (employee_group),
    CONSTRAINT chk_bookings_dow CHECK (dow BETWEEN 0 AND 6),
    CONSTRAINT chk_bookings_hour CHECK (hour >= 0 AND hour < 24),
    CONSTRAINT chk_bookings_distance CHECK (distance_km >= 0),
    -- A trip cannot complete without having been accepted, and cannot be
    -- accepted without having been matched. The state machine in
    -- app/lifecycle/states.py says so; the database now enforces it.
    CONSTRAINT chk_bookings_lifecycle CHECK (
        (accepted = FALSE OR matched = TRUE)
        AND (completed = FALSE OR accepted = TRUE)
        AND NOT (completed = TRUE AND cancelled = TRUE))
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------------
-- booking_sessions — what happened when a rider pressed BOOK NOW here.
--
-- Live sessions are held in Redis while they are in flight (they expire, they
-- are read on every retry, and they are gone within the hour). The moment a
-- session reaches a terminal state — settled or out of attempts — it is
-- written here, because THAT is the durable record: what was advertised, what
-- was paid, how many minutes the failures cost, and the three probabilities
-- the option was priced with.
--
-- The probabilities are stored, not recomputed, because the point of the whole
-- product is that the prediction and the lived outcome can be compared. A
-- re-prediction six months later would be comparing against a different model.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS booking_sessions (
    session_id       VARCHAR(32)    NOT NULL,
    created_at       DATETIME       NOT NULL,
    departure        DATETIME       NOT NULL,
    provider_id      VARCHAR(32)    NOT NULL,
    display_name     VARCHAR(64)    NOT NULL,
    mode             VARCHAR(24)    NOT NULL,
    service_class    VARCHAR(24)    NOT NULL,
    origin_label     VARCHAR(160)   NOT NULL,
    dest_label       VARCHAR(160)   NOT NULL,
    advertised_fare  DECIMAL(10, 2) NOT NULL,
    paid_fare        DECIMAL(10, 2) NULL,
    p_match          DECIMAL(6, 5)  NOT NULL,
    p_accept         DECIMAL(6, 5)  NOT NULL,
    p_cancel         DECIMAL(6, 5)  NOT NULL,
    attempt_count    TINYINT        NOT NULL DEFAULT 0,
    wasted_min       DECIMAL(7, 2)  NOT NULL DEFAULT 0,
    settled          BOOLEAN        NOT NULL DEFAULT FALSE,
    exhausted        BOOLEAN        NOT NULL DEFAULT FALSE,
    -- TRUE for the reproducible demonstration/seed traffic, so a viewer can
    -- always tell seeded rows from rows a person created.
    demo             BOOLEAN        NOT NULL DEFAULT FALSE,
    PRIMARY KEY (session_id),
    KEY idx_sessions_created (created_at),
    KEY idx_sessions_provider (provider_id),
    CONSTRAINT chk_sessions_probs CHECK (
        p_match BETWEEN 0 AND 1 AND p_accept BETWEEN 0 AND 1
        AND p_cancel BETWEEN 0 AND 1),
    CONSTRAINT chk_sessions_paid CHECK (paid_fare IS NULL OR paid_fare >= 0),
    -- A settled session is one that ran out of nothing; the two terminal
    -- states are mutually exclusive.
    CONSTRAINT chk_sessions_terminal CHECK (NOT (settled = TRUE AND exhausted = TRUE))
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------------
-- booking_attempts — one row per press of BOOK NOW or TRY AGAIN.
--
-- The child side of the only genuine one-to-many relationship in the schema.
-- A session has between one and four attempts; each carries its own fare,
-- because a re-request lands in a market that has just demonstrated it is
-- tight and is priced accordingly.
--
-- ON DELETE CASCADE here and RESTRICT on bookings.zone_id is not an
-- inconsistency: an attempt has no meaning without its session, whereas a
-- booking has plenty of meaning without its zone row.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS booking_attempts (
    attempt_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    session_id   VARCHAR(32)     NOT NULL,
    attempt_no   TINYINT         NOT NULL,
    fare         DECIMAL(10, 2)  NOT NULL,
    outcome      VARCHAR(32)     NOT NULL,
    succeeded    BOOLEAN         NOT NULL DEFAULT FALSE,
    wasted_min   DECIMAL(7, 2)   NOT NULL DEFAULT 0,
    driver_name  VARCHAR(64)     NULL,
    eta_min      DECIMAL(6, 2)   NULL,
    PRIMARY KEY (attempt_id),
    UNIQUE KEY uq_attempt_per_session (session_id, attempt_no),
    CONSTRAINT fk_attempts_session FOREIGN KEY (session_id)
        REFERENCES booking_sessions (session_id) ON DELETE CASCADE,
    CONSTRAINT chk_attempts_no CHECK (attempt_no >= 1),
    CONSTRAINT chk_attempts_fare CHECK (fare >= 0)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------------
-- audit_events — every decision this instance made, and what it made it on.
--
-- Shaped like the Recommendation Record in V2_TRUST_SECURITY_GOVERNANCE.md:
-- what was asked, what was decided, by which model version, with what
-- confidence. It is what makes "why did the system say that, last Tuesday?"
-- answerable.
--
-- The variable-shaped halves (`request`, `decision`) are JSON columns rather
-- than a wide table of nullable columns, because a recommendation, an
-- enterprise query and a booking carry genuinely different fields — and
-- MySQL's JSON type keeps them queryable (`decision->>'$.recommended'`)
-- instead of turning the column into an opaque blob.
--
-- Redis holds the newest 500 of these for the audit screen; this table holds
-- all of them, because an audit trail that forgets is not an audit trail.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_events (
    event_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    at              DATETIME        NOT NULL,
    kind            VARCHAR(32)     NOT NULL,
    actor           VARCHAR(64)     NOT NULL,
    request         JSON            NOT NULL,
    decision        JSON            NOT NULL,
    model_versions  JSON            NULL,
    confidence      DECIMAL(6, 5)   NULL,
    data_classes    JSON            NULL,
    PRIMARY KEY (event_id),
    KEY idx_audit_at (at),
    KEY idx_audit_kind_at (kind, at),
    CONSTRAINT chk_audit_confidence CHECK (
        confidence IS NULL OR confidence BETWEEN 0 AND 1)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------------
-- v_campus_mobility — a saved query, not a second copy of the data.
--
-- WHAT A VIEW IS
-- A view is a SELECT that has been given a name. It stores no rows of its own;
-- every time it is read, MySQL runs the query underneath it against the live
-- tables. So it cannot go stale, and it costs no extra storage.
--
-- WHY THIS ONE EXISTS
-- "How is each campus doing, and how much is failure costing it?" is the
-- question the enterprise dashboard is built around, and answering it means
-- the same JOIN to `zones` and the same GROUP BY every time. Writing it once
-- here means a person at a `mysql>` prompt can ask it without reconstructing
-- the join, and cannot accidentally ask a slightly different question than the
-- product does.
--
-- WHY THE DASHBOARD ITSELF DOES NOT READ IT
-- Stated plainly because it matters: the dashboard loads the history once into
-- typed NumPy columns and filters it in memory, because a filter click there is
-- a boolean mask rather than a fresh query (see enterprise/store.py). This view
-- is the SQL statement of the same question -- used for reporting, for the
-- verification queries in the manual, and as the readable definition of what
-- "campus performance" means.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_campus_mobility AS
SELECT
    b.campus_id,
    b.campus,
    z.kind                                            AS zone_kind,
    COUNT(*)                                          AS bookings,
    SUM(b.completed)                                  AS completed_trips,
    SUM(b.cancelled)                                  AS cancelled_trips,
    ROUND(AVG(b.distance_km), 2)                      AS avg_distance_km,
    ROUND(100.0 * SUM(b.completed) / COUNT(*), 1)     AS completion_pct,
    ROUND(100.0 * SUM(b.cancelled) / COUNT(*), 1)     AS cancellation_pct
FROM bookings b
JOIN zones z ON z.zone_id = b.zone_id
GROUP BY b.campus_id, b.campus, z.kind;
