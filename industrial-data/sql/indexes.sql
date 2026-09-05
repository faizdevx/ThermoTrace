CREATE INDEX IF NOT EXISTS idx_india_boundary_geometry_gist ON india_boundary USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_india_boundary_name ON india_boundary (name);
CREATE INDEX IF NOT EXISTS idx_india_boundary_administrative_code ON india_boundary (administrative_code);
CREATE INDEX IF NOT EXISTS idx_india_boundary_source ON india_boundary (source);
CREATE INDEX IF NOT EXISTS idx_india_boundary_source_id ON india_boundary (source_id);

CREATE INDEX IF NOT EXISTS idx_states_geometry_gist ON states USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_states_name ON states (name);
CREATE INDEX IF NOT EXISTS idx_states_administrative_code ON states (administrative_code);
CREATE INDEX IF NOT EXISTS idx_states_source ON states (source);
CREATE INDEX IF NOT EXISTS idx_states_source_id ON states (source_id);

CREATE INDEX IF NOT EXISTS idx_districts_geometry_gist ON districts USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_districts_name ON districts (name);
CREATE INDEX IF NOT EXISTS idx_districts_administrative_code ON districts (administrative_code);
CREATE INDEX IF NOT EXISTS idx_districts_source ON districts (source);
CREATE INDEX IF NOT EXISTS idx_districts_source_id ON districts (source_id);

CREATE INDEX IF NOT EXISTS idx_osm_industries_geometry_gist ON osm_industries USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_osm_industries_osm_identity ON osm_industries (osm_type, osm_id);
CREATE INDEX IF NOT EXISTS idx_osm_industries_name ON osm_industries (name);
CREATE INDEX IF NOT EXISTS idx_osm_industries_normalized_name ON osm_industries (normalized_name);
CREATE INDEX IF NOT EXISTS idx_osm_industries_industrial_type ON osm_industries (industrial_type);
CREATE INDEX IF NOT EXISTS idx_osm_industries_source ON osm_industries (source);

CREATE INDEX IF NOT EXISTS idx_industrial_entity_matches_site_id ON industrial_entity_matches (site_id);
CREATE INDEX IF NOT EXISTS idx_industrial_entity_matches_match_score ON industrial_entity_matches (match_score);
CREATE INDEX IF NOT EXISTS idx_industrial_entity_matches_match_confidence ON industrial_entity_matches (match_confidence);
CREATE INDEX IF NOT EXISTS idx_industrial_entity_matches_osm_source_key ON industrial_entity_matches (osm_source_key);
CREATE INDEX IF NOT EXISTS idx_industrial_entity_matches_government_source_key ON industrial_entity_matches (government_source_key);

CREATE INDEX IF NOT EXISTS idx_industrial_sites_site_id ON industrial_sites (site_id);
CREATE INDEX IF NOT EXISTS idx_industrial_sites_geometry_gist ON industrial_sites USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_industrial_sites_state ON industrial_sites (state);
CREATE INDEX IF NOT EXISTS idx_industrial_sites_district ON industrial_sites (district);
CREATE INDEX IF NOT EXISTS idx_industrial_sites_industry_type ON industrial_sites (industry_type);
CREATE INDEX IF NOT EXISTS idx_industrial_sites_source_count ON industrial_sites (source_count);
CREATE INDEX IF NOT EXISTS idx_industrial_sites_match_confidence ON industrial_sites (match_confidence);
CREATE INDEX IF NOT EXISTS idx_industrial_sites_review_required ON industrial_sites (review_required);

CREATE INDEX IF NOT EXISTS idx_industrial_entity_matches_extraction_date ON industrial_entity_matches (extraction_date);
CREATE INDEX IF NOT EXISTS idx_osm_industries_extraction_date ON osm_industries (extraction_date);

-- site_source_records indexes (Phase 9)
CREATE INDEX IF NOT EXISTS idx_ssr_site_id ON site_source_records (site_id);
CREATE INDEX IF NOT EXISTS idx_ssr_source_key ON site_source_records (source_key);
CREATE INDEX IF NOT EXISTS idx_ssr_source_system ON site_source_records (source_system);
CREATE INDEX IF NOT EXISTS idx_ssr_source_id ON site_source_records (source_id);

-- normalized_industry_type index on industrial_sites (Phase 10)
CREATE INDEX IF NOT EXISTS idx_industrial_sites_normalized_industry_type
    ON industrial_sites (normalized_industry_type);

