CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS india_boundary (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    administrative_code TEXT,
    geometry geometry(MULTIPOLYGON, 4326) NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT india_boundary_source_key UNIQUE (source, source_id)
);

CREATE TABLE IF NOT EXISTS states (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    administrative_code TEXT,
    geometry geometry(MULTIPOLYGON, 4326) NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT states_source_key UNIQUE (source, source_id)
);

CREATE TABLE IF NOT EXISTS districts (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    administrative_code TEXT,
    geometry geometry(MULTIPOLYGON, 4326) NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT districts_source_key UNIQUE (source, source_id)
);

CREATE TABLE IF NOT EXISTS osm_industries (
    id BIGSERIAL PRIMARY KEY,
    osm_id BIGINT NOT NULL,
    osm_type TEXT NOT NULL,
    osm_version INTEGER,
    osm_changeset BIGINT,
    name TEXT,
    normalized_name TEXT,
    industrial_type TEXT,
    normalized_industrial_type TEXT,
    geometry geometry(GEOMETRY, 4326) NOT NULL,
    source TEXT NOT NULL,
    source_timestamp TIMESTAMPTZ,
    extraction_date DATE,
    first_seen DATE,
    last_seen DATE,
    operational_status TEXT,
    raw_tags JSONB NOT NULL,
    source_url TEXT,
    district_name TEXT,
    district_source_id TEXT,
    probable_duplicate BOOLEAN NOT NULL DEFAULT FALSE,
    duplicate_group_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT osm_industries_source_key UNIQUE (source, osm_type, osm_id)
);

-- Idempotent migration: add osm_version / osm_changeset to existing databases
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'osm_industries' AND column_name = 'osm_version'
    ) THEN
        ALTER TABLE osm_industries ADD COLUMN osm_version INTEGER;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'osm_industries' AND column_name = 'osm_changeset'
    ) THEN
        ALTER TABLE osm_industries ADD COLUMN osm_changeset BIGINT;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS industrial_entity_matches (
    id BIGSERIAL PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    site_id TEXT,
    osm_source_key TEXT NOT NULL,
    government_source_key TEXT NOT NULL,
    osm_id BIGINT NOT NULL,
    government_source_id TEXT,
    government_table TEXT NOT NULL,
    match_score DOUBLE PRECISION NOT NULL,
    match_confidence TEXT NOT NULL,
    match_method TEXT NOT NULL,
    review_required BOOLEAN NOT NULL DEFAULT FALSE,
    matched_source_ids JSONB NOT NULL,
    spatial_distance_meters DOUBLE PRECISION,
    name_score DOUBLE PRECISION,
    industry_score DOUBLE PRECISION,
    address_score DOUBLE PRECISION,
    state_consistent BOOLEAN NOT NULL DEFAULT FALSE,
    district_consistent BOOLEAN NOT NULL DEFAULT FALSE,
    extraction_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT industrial_entity_matches_unique UNIQUE (osm_source_key, government_source_key)
);

CREATE TABLE IF NOT EXISTS industrial_sites (
    id BIGSERIAL PRIMARY KEY,
    site_id TEXT NOT NULL,
    name TEXT,
    normalized_name TEXT,
    industry_type TEXT,
    geometry geometry(GEOMETRY, 4326) NOT NULL,
    state TEXT,
    district TEXT,
    address TEXT,
    establishment_status TEXT,
    establishment_date DATE,
    extraction_date DATE,
    first_seen DATE,
    last_seen DATE,
    operational_status TEXT,
    osm_ids JSONB NOT NULL,
    government_ids JSONB NOT NULL,
    matched_source_ids JSONB NOT NULL,
    source_count INTEGER NOT NULL,
    source_confidence DOUBLE PRECISION NOT NULL,
    match_score DOUBLE PRECISION NOT NULL,
    match_method TEXT NOT NULL,
    match_confidence TEXT NOT NULL,
    review_required BOOLEAN NOT NULL DEFAULT FALSE,
    last_verified DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT industrial_sites_site_id_key UNIQUE (site_id)
);

-- Idempotent migration: add normalized_industry_type to existing databases
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'industrial_sites' AND column_name = 'normalized_industry_type'
    ) THEN
        ALTER TABLE industrial_sites ADD COLUMN normalized_industry_type TEXT;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'industrial_sites_source_count_nonnegative'
          AND conrelid = 'industrial_sites'::regclass
    ) THEN
        ALTER TABLE industrial_sites
            ADD CONSTRAINT industrial_sites_source_count_nonnegative CHECK (source_count >= 0);
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'industrial_entity_matches_score_range'
          AND conrelid = 'industrial_entity_matches'::regclass
    ) THEN
        ALTER TABLE industrial_entity_matches
            ADD CONSTRAINT industrial_entity_matches_score_range CHECK (match_score >= 0 AND match_score <= 1);
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'industrial_sites_score_range'
          AND conrelid = 'industrial_sites'::regclass
    ) THEN
        ALTER TABLE industrial_sites
            ADD CONSTRAINT industrial_sites_score_range CHECK (match_score >= 0 AND match_score <= 1);
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- site_source_records — stable source identity ledger (Phase 9)
-- Each row maps one (site_id, source_system, source_key) pair.
-- This table is the ground truth for refresh-aware site resolution:
-- on re-run, existing site_ids are looked up here instead of being
-- regenerated, preventing foreign key drift.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS site_source_records (
    id              BIGSERIAL PRIMARY KEY,
    site_id         TEXT NOT NULL,
    source_system   TEXT NOT NULL,
    source_key      TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    source_table    TEXT NOT NULL,
    source_version  TEXT,
    first_linked    DATE NOT NULL,
    last_linked     DATE NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT site_source_records_unique UNIQUE (site_id, source_system, source_key)
);