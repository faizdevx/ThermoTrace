-- NASA FIRMS thermal detections and classified thermal sources.
CREATE TABLE IF NOT EXISTS thermal_detections (
    detection_id   TEXT PRIMARY KEY,
    instrument     TEXT NOT NULL,
    acq_datetime   TIMESTAMPTZ NOT NULL,
    geometry       geometry(Point, 4326) NOT NULL,
    frp            DOUBLE PRECISION,
    bright_ti4     DOUBLE PRECISION,
    bright_ti5     DOUBLE PRECISION,
    confidence     TEXT,
    daynight       CHAR(1),
    source_id      TEXT,
    loaded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_thermal_det_geom ON thermal_detections USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_thermal_det_time ON thermal_detections (acq_datetime DESC);
CREATE INDEX IF NOT EXISTS idx_thermal_det_source ON thermal_detections (source_id);

CREATE TABLE IF NOT EXISTS thermal_sources (
    source_id       TEXT PRIMARY KEY,
    geometry        geometry(Point, 4326) NOT NULL,
    thermal_class   TEXT NOT NULL,
    thermal_label   TEXT NOT NULL,
    class_confidence TEXT,
    class_margin    DOUBLE PRECISION,
    class_scores    JSONB,
    reasons         JSONB,
    alert_level     TEXT,
    alert_text      TEXT,
    state           TEXT,
    site_ref        TEXT,
    site_name       TEXT,
    site_kind       TEXT,
    site_distance_m DOUBLE PRECISION,
    site_within     BOOLEAN,
    n_detections    INTEGER,
    n_days          INTEGER,
    window_days     INTEGER,
    first_seen      TIMESTAMPTZ,
    last_seen       TIMESTAMPTZ,
    frp_mean        DOUBLE PRECISION,
    frp_max         DOUBLE PRECISION,
    frp_total       DOUBLE PRECISION,
    night_fraction  DOUBLE PRECISION,
    extent_m        DOUBLE PRECISION,
    lc_dominant     TEXT,
    lc_crop         DOUBLE PRECISION,
    lc_vegetation   DOUBLE PRECISION,
    lc_built        DOUBLE PRECISION,
    lc_bare         DOUBLE PRECISION,
    instruments     TEXT,
    is_current      BOOLEAN NOT NULL DEFAULT TRUE,
    run_id          BIGINT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_thermal_src_geom ON thermal_sources USING GIST (geometry) WHERE is_current;
CREATE INDEX IF NOT EXISTS idx_thermal_src_class ON thermal_sources (thermal_class) WHERE is_current;
CREATE INDEX IF NOT EXISTS idx_thermal_src_alert ON thermal_sources (alert_level) WHERE is_current;
CREATE INDEX IF NOT EXISTS idx_thermal_src_last ON thermal_sources (last_seen DESC);

CREATE TABLE IF NOT EXISTS thermal_runs (
    id             BIGSERIAL PRIMARY KEY,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at    TIMESTAMPTZ,
    status         TEXT NOT NULL DEFAULT 'running',
    detections     INTEGER,
    sources        INTEGER,
    latest_detection TIMESTAMPTZ,
    summary        JSONB,
    error          TEXT
);
