CREATE OR REPLACE FUNCTION ensure_valid_multipolygon(input_geometry geometry)
RETURNS geometry
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    repaired geometry;
BEGIN
    IF input_geometry IS NULL THEN
        RETURN NULL;
    END IF;

    repaired := ST_Multi(ST_MakeValid(input_geometry));

    IF repaired IS NULL OR ST_IsEmpty(repaired) THEN
        repaired := ST_Multi(ST_Buffer(input_geometry, 0));
    END IF;

    RETURN repaired;
END;
$$;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE OR REPLACE FUNCTION calculate_distance_meters(
    left_geometry geometry,
    right_geometry geometry
)
RETURNS DOUBLE PRECISION
LANGUAGE sql
STABLE
AS $$
    SELECT CASE
        WHEN left_geometry IS NULL OR right_geometry IS NULL THEN NULL
        ELSE ST_Distance(
            ST_Transform(left_geometry, 3857),
            ST_Transform(right_geometry, 3857)
        )
    END;
$$;

CREATE OR REPLACE FUNCTION point_within_district(
    point_geometry geometry,
    district_geometry geometry
)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT CASE
        WHEN point_geometry IS NULL OR district_geometry IS NULL THEN FALSE
        ELSE ST_Within(point_geometry, district_geometry)
    END;
$$;

CREATE OR REPLACE FUNCTION generate_spatial_candidate_pairs(
    left_table regclass,
    right_table regclass,
    max_distance_meters DOUBLE PRECISION DEFAULT 1500
)
RETURNS TABLE (
    left_id BIGINT,
    right_id BIGINT,
    spatial_distance_meters DOUBLE PRECISION
)
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
    RETURN QUERY EXECUTE format(
        'SELECT l.id AS left_id, r.id AS right_id, calculate_distance_meters(l.geometry, r.geometry) AS spatial_distance_meters
         FROM %s l
         JOIN %s r
           ON ST_DWithin(ST_Transform(l.geometry, 3857), ST_Transform(r.geometry, 3857), %L)',
        left_table,
        right_table,
        max_distance_meters
    );
END;
$$;

CREATE OR REPLACE FUNCTION detect_duplicate_clusters(
    target_table regclass,
    key_column_name TEXT DEFAULT 'site_id',
    name_column_name TEXT DEFAULT 'normalized_name',
    name_similarity_threshold DOUBLE PRECISION DEFAULT 0.9,
    distance_threshold_meters DOUBLE PRECISION DEFAULT 100
)
RETURNS TABLE (
    source_key_a TEXT,
    source_key_b TEXT,
    name_similarity DOUBLE PRECISION,
    distance_meters DOUBLE PRECISION
)
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
    RETURN QUERY EXECUTE format(
        'SELECT
             a.%I::text AS source_key_a,
             b.%I::text AS source_key_b,
             similarity(COALESCE(a.%I, a.name), COALESCE(b.%I, b.name)) AS name_similarity,
             calculate_distance_meters(a.geometry, b.geometry) AS distance_meters
         FROM %s a
         JOIN %s b
           ON a.id < b.id
          AND calculate_distance_meters(a.geometry, b.geometry) <= %L
          AND similarity(COALESCE(a.%I, a.name), COALESCE(b.%I, b.name)) >= %L',
        key_column_name,
        key_column_name,
        name_column_name,
        name_column_name,
        target_table,
        target_table,
        distance_threshold_meters,
        name_column_name,
        name_column_name,
        name_similarity_threshold
    );
END;
$$;