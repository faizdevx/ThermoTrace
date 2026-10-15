-- Satellite-imagery evidence per thermal source (Sentinel-2 dNBR / hot pixels, Landsat thermal check).
-- The chips themselves live in the shared on-disk imagery cache; these are the numbers the UI and model use.
ALTER TABLE thermal_sources
    ADD COLUMN IF NOT EXISTS imagery_available         BOOLEAN,
    ADD COLUMN IF NOT EXISTS imagery_scene_id          TEXT,
    ADD COLUMN IF NOT EXISTS imagery_scene_time        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS imagery_cloud_percent     DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS imagery_before_time       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS imagery_dnbr_p95          DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS imagery_burned_fraction   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS imagery_swir_hot_pixels   INTEGER,
    ADD COLUMN IF NOT EXISTS imagery_landsat_excess_k  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS imagery_checked_at        TIMESTAMPTZ;
