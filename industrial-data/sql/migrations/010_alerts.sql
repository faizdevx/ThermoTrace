-- Industrial-fire alerts and the analyst loop. One alert per facility per dedupe window; every analyst decision is
-- kept in alert_decisions and the latest one is mirrored into `labels` (rule_version 'analyst-v1', split 'unused'
-- so it never leaks into the frozen labels-v1 evaluation) where retraining can pick it up.
CREATE TABLE IF NOT EXISTS alerts (
    alert_id       BIGSERIAL PRIMARY KEY,
    facility_key   TEXT NOT NULL,              -- thermal_sources.site_ref, else 'src:<source_id>'
    source_id      TEXT NOT NULL,
    geometry       geometry(Point, 4326) NOT NULL,
    site_ref       TEXT,
    site_name      TEXT,
    site_kind      TEXT,
    state          TEXT,
    trigger        TEXT NOT NULL CHECK (trigger IN ('p_fire', 'frp_z', 'both', 'test')),
    severity       TEXT NOT NULL CHECK (severity IN ('watch', 'high', 'critical')),
    p_fire         DOUBLE PRECISION,
    frp_max        DOUBLE PRECISION,
    frp_z          DOUBLE PRECISION,
    first_seen     TIMESTAMPTZ,
    last_seen      TIMESTAMPTZ,
    peak_frp       DOUBLE PRECISION,
    peak_at        TIMESTAMPTZ,
    summary        TEXT NOT NULL,
    reasons        JSONB,
    imagery        JSONB,
    status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'confirmed', 'rejected', 'unsure')),
    delivery       JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {channel: {ok, at, error, attempts}}
    is_test        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_alerts_facility_time ON alerts (facility_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_geom ON alerts USING GIST (geometry);

CREATE TABLE IF NOT EXISTS alert_decisions (
    id          BIGSERIAL PRIMARY KEY,
    alert_id    BIGINT NOT NULL REFERENCES alerts (alert_id) ON DELETE CASCADE,
    decision    TEXT NOT NULL CHECK (decision IN ('confirmed', 'rejected', 'unsure')),
    note        TEXT,
    analyst     TEXT,
    decided_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    label_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_decisions_alert ON alert_decisions (alert_id, decided_at);
