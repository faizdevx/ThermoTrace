-- Authoritative facility database: one row per physical facility, one row per source record merged into it.
CREATE TABLE IF NOT EXISTS facilities (
    facility_id     TEXT PRIMARY KEY,
    name            TEXT,
    kind            TEXT NOT NULL,
    geometry        geometry(Point, 4326) NOT NULL,
    radius_m        DOUBLE PRECISION NOT NULL,
    capacity_mw     DOUBLE PRECISION,
    status          TEXT,
    primary_source  TEXT NOT NULL,
    source_count    INTEGER NOT NULL,
    sources         TEXT NOT NULL,
    attribution     TEXT NOT NULL,
    built_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_facilities_geom ON facilities USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_facilities_kind ON facilities (kind);

CREATE TABLE IF NOT EXISTS facility_sources (
    facility_id     TEXT NOT NULL REFERENCES facilities (facility_id) ON DELETE CASCADE,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    name            TEXT,
    distance_m      DOUBLE PRECISION,
    url             TEXT,
    licence         TEXT NOT NULL,
    attribution     TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_facility_sources_fac ON facility_sources (facility_id);
