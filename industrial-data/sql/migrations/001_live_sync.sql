-- ThermoTrace live-sync schema (applied once, tracked in schema_migrations).
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Global data revision.
-- Incremented once per committed sync that changed visible data. Clients use
-- it as a cheap change identifier (SSE payload, ETag, cache key).
CREATE TABLE IF NOT EXISTS data_revision (
    id          SMALLINT PRIMARY KEY CHECK (id = 1),
    revision    BIGINT NOT NULL DEFAULT 0,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO data_revision (id, revision) VALUES (1, 0) ON CONFLICT (id) DO NOTHING;

-- Per-source sync state (durable checkpoint).
CREATE TABLE IF NOT EXISTS sync_state (
    source                    TEXT PRIMARY KEY,
    kind                      TEXT NOT NULL,
    enabled                   BOOLEAN NOT NULL DEFAULT TRUE,
    status                    TEXT NOT NULL DEFAULT 'awaiting_setup',
    update_cadence            TEXT,
    poll_interval_seconds     INTEGER,
    freshness_target_seconds  INTEGER,
    cursor                    JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_check_at             TIMESTAMPTZ,
    last_check_error          TEXT,
    source_head_sequence      BIGINT,
    source_head_timestamp     TIMESTAMPTZ,
    source_head_published_at  TIMESTAMPTZ,
    last_source_data_at       TIMESTAMPTZ,
    last_commit_at            TIMESTAMPTZ,
    last_success_at           TIMESTAMPTZ,
    last_error                TEXT,
    consecutive_failures      INTEGER NOT NULL DEFAULT 0,
    next_retry_at             TIMESTAMPTZ,
    manual_requested_at       TIMESTAMPTZ,
    heartbeat_at              TIMESTAMPTZ,
    totals                    JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON COLUMN sync_state.cursor IS
    'Last source state APPLIED TO MASTER TABLES. OSM: {"sequence": n, "timestamp": iso}.';
COMMENT ON COLUMN sync_state.last_check_at IS
    'Last time the source was successfully asked. NOT the time the source data changed.';

CREATE TABLE IF NOT EXISTS sync_runs (
    id                     BIGSERIAL PRIMARY KEY,
    source                 TEXT NOT NULL,
    trigger                TEXT NOT NULL DEFAULT 'schedule',
    status                 TEXT NOT NULL DEFAULT 'running',
    started_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at            TIMESTAMPTZ,
    from_cursor            JSONB,
    to_cursor              JSONB,
    created_count          INTEGER NOT NULL DEFAULT 0,
    updated_count          INTEGER NOT NULL DEFAULT 0,
    deleted_count          INTEGER NOT NULL DEFAULT 0,
    skipped_count          INTEGER NOT NULL DEFAULT 0,
    failed_count           INTEGER NOT NULL DEFAULT 0,
    revision               BIGINT,
    source_data_at         TIMESTAMPTZ,
    source_published_at    TIMESTAMPTZ,
    committed_at           TIMESTAMPTZ,
    ingest_latency_seconds DOUBLE PRECISION,
    duration_seconds       DOUBLE PRECISION,
    error                  TEXT,
    details                JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_sync_runs_source_started ON sync_runs (source, started_at DESC);

-- Lifecycle + provenance columns on the existing tables.
ALTER TABLE osm_industries
    ADD COLUMN IF NOT EXISTS content_hash TEXT,
    ADD COLUMN IF NOT EXISTS is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS deleted_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS revision     BIGINT NOT NULL DEFAULT 0;

ALTER TABLE industrial_sites
    ADD COLUMN IF NOT EXISTS is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS deleted_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS revision          BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS primary_source    TEXT,
    ADD COLUMN IF NOT EXISTS source_ref        TEXT,
    ADD COLUMN IF NOT EXISTS source_url        TEXT,
    ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS osm_version       INTEGER,
    ADD COLUMN IF NOT EXISTS feature_class     TEXT,
    ADD COLUMN IF NOT EXISTS geometry_type     TEXT,
    ADD COLUMN IF NOT EXISTS evidence_level    TEXT,
    ADD COLUMN IF NOT EXISTS evidence_score    INTEGER,
    ADD COLUMN IF NOT EXISTS tags              JSONB,
    ADD COLUMN IF NOT EXISTS content_hash      TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_industrial_sites_source_ref
    ON industrial_sites (primary_source, source_ref) WHERE source_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_industrial_sites_active_geom
    ON industrial_sites USING GIST (geometry) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_industrial_sites_active_state_district
    ON industrial_sites (lower(state), lower(district)) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_industrial_sites_active_type
    ON industrial_sites (lower(industry_type)) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_industrial_sites_active_status
    ON industrial_sites (lower(operational_status)) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_industrial_sites_evidence
    ON industrial_sites (evidence_level) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_industrial_sites_revision ON industrial_sites (revision);
CREATE INDEX IF NOT EXISTS idx_industrial_sites_name_trgm
    ON industrial_sites USING GIN (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_osm_industries_active
    ON osm_industries (osm_type, osm_id) WHERE is_active;

-- Industry category mapping (documented, editable).
CREATE TABLE IF NOT EXISTS industry_class_map (
    tag_key       TEXT NOT NULL,
    tag_value     TEXT NOT NULL,
    industry_type TEXT NOT NULL,
    PRIMARY KEY (tag_key, tag_value)
);
INSERT INTO industry_class_map (tag_key, tag_value, industry_type) VALUES
    ('industrial','factory','Manufacturing'),
    ('industrial','manufacture','Manufacturing'),
    ('industrial','engineering','Manufacturing'),
    ('industrial','machine_shop','Manufacturing'),
    ('industrial','automotive_industry','Manufacturing'),
    ('industrial','electronics','Manufacturing'),
    ('industrial','shipyard','Manufacturing'),
    ('industrial','chemical','Chemicals & Petrochemicals'),
    ('industrial','oil','Chemicals & Petrochemicals'),
    ('industrial','gas','Chemicals & Petrochemicals'),
    ('industrial','petroleum','Chemicals & Petrochemicals'),
    ('industrial','refinery','Chemicals & Petrochemicals'),
    ('industrial','fertilizer','Chemicals & Petrochemicals'),
    ('industrial','pharmaceutical','Chemicals & Petrochemicals'),
    ('industrial','steel','Metals & Steel'),
    ('industrial','metal','Metals & Steel'),
    ('industrial','metal_processing','Metals & Steel'),
    ('industrial','foundry','Metals & Steel'),
    ('industrial','smelter','Metals & Steel'),
    ('industrial','aluminium','Metals & Steel'),
    ('industrial','mine','Mining & Quarrying'),
    ('industrial','quarry','Mining & Quarrying'),
    ('industrial','mining','Mining & Quarrying'),
    ('industrial','cement','Cement & Building Materials'),
    ('industrial','brickworks','Cement & Building Materials'),
    ('industrial','concrete','Cement & Building Materials'),
    ('industrial','textile','Textiles & Garments'),
    ('industrial','clothing','Textiles & Garments'),
    ('industrial','garment','Textiles & Garments'),
    ('industrial','food','Food Processing'),
    ('industrial','food_processing','Food Processing'),
    ('industrial','sugar','Food Processing'),
    ('industrial','brewery','Food Processing'),
    ('industrial','bakery','Food Processing'),
    ('industrial','dairy','Food Processing'),
    ('industrial','agriculture','Food Processing'),
    ('industrial','power','Energy & Power'),
    ('industrial','electrical','Energy & Power'),
    ('industrial','warehouse','Warehouse & Logistics'),
    ('industrial','warehouse;logistics','Warehouse & Logistics'),
    ('industrial','logistics','Warehouse & Logistics'),
    ('industrial','depot','Warehouse & Logistics'),
    ('industrial','distribution','Warehouse & Logistics'),
    ('industrial','port','Warehouse & Logistics'),
    ('industrial','wood','Wood & Paper'),
    ('industrial','sawmill','Wood & Paper'),
    ('industrial','paper','Wood & Paper'),
    ('industrial','recycling','Waste & Recycling'),
    ('industrial','scrap_yard','Waste & Recycling'),
    ('industrial','waste','Waste & Recycling'),
    ('industrial','water','Water & Utilities'),
    ('industrial','water_treatment','Water & Utilities'),
    ('man_made','works','Manufacturing'),
    ('man_made','wastewater_plant','Water & Utilities'),
    ('man_made','water_works','Water & Utilities'),
    ('power','plant','Energy & Power'),
    ('landuse','industrial','Industrial Area (land use)'),
    ('building','industrial','Industrial Building')
ON CONFLICT (tag_key, tag_value) DO NOTHING;
