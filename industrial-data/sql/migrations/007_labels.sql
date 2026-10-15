-- Labelled evaluation set (weak rule labels + curated incidents). Versioned by rule_version; the parquet files and
-- manifest in data/labels/ are the system of record, this table makes them queryable next to thermal_sources.
CREATE TABLE IF NOT EXISTS labels (
    label_id        TEXT PRIMARY KEY,
    unit_type       TEXT NOT NULL CHECK (unit_type IN ('cell_window', 'incident')),
    cell_key        TEXT,
    source_id       TEXT,                       -- incident id, or a thermal_sources.source_id when a unit is tied to one
    geometry        geometry(Point, 4326) NOT NULL,
    state           TEXT,
    window_start    DATE NOT NULL,
    window_end      DATE NOT NULL,
    label           TEXT NOT NULL,
    label_source    TEXT NOT NULL,
    evidence_url    TEXT,
    labelled_by     TEXT NOT NULL,
    rule_version    TEXT NOT NULL,
    split           TEXT NOT NULL CHECK (split IN ('train', 'test', 'unused', 'incident_test')),
    region_group    TEXT,
    n_detections    INTEGER,
    n_days          INTEGER,
    frp_max         DOUBLE PRECISION,
    night_fraction  DOUBLE PRECISION,
    detail          JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_labels_geom ON labels USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_labels_class_split ON labels (label, split);
CREATE INDEX IF NOT EXISTS idx_labels_window ON labels (window_start);
