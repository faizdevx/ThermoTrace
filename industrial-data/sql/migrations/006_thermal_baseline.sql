-- Long baseline per facility and per 1 km cell from the FIRMS archive (VIIRS S-NPP + NOAA-20).
--
-- Definitions (one place; src/thermal/baseline.py computes the same numbers for the file backend and
-- tests/thermal/test_baseline_postgis.py checks the two agree):
--   anchor       newest acq_date in thermal_detections
--   window       365 days ending 14 days before the anchor (the live window is excluded, so "today" is
--                compared with history that does not contain it)
--   covered day  a day in the window on which the table holds any detection (archive gaps are not "quiet")
--   detection    low-confidence VIIRS pixels are dropped; one physical detection counted once even if it is
--                held as both an SP and an NRT row (SP wins)
--   active day   a covered day with >= 1 detection assigned to the entity
--   daily FRP    sum of FRP per day; mean / std over ALL covered days (inactive days are 0)

ALTER TABLE thermal_detections
    ADD COLUMN IF NOT EXISTS satellite TEXT GENERATED ALWAYS AS (split_part(instrument, '_', 2)) STORED,
    ADD COLUMN IF NOT EXISTS product   TEXT GENERATED ALWAYS AS (split_part(instrument, '_', 3)) STORED,
    ADD COLUMN IF NOT EXISTS acq_date  DATE GENERATED ALWAYS AS ((acq_datetime AT TIME ZONE 'UTC')::date) STORED;
CREATE INDEX IF NOT EXISTS idx_thermal_det_acq_date ON thermal_detections (acq_date);
CREATE INDEX IF NOT EXISTS idx_thermal_det_geog ON thermal_detections USING GIST ((geometry::geography));

-- facility + 1 km cell of every detection, filled incrementally by assign_thermal_geo()
CREATE TABLE IF NOT EXISTS thermal_detection_geo (
    detection_id TEXT PRIMARY KEY REFERENCES thermal_detections (detection_id) ON DELETE CASCADE,
    facility_id  TEXT,
    cell_x       INTEGER NOT NULL,
    cell_y       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tdg_facility ON thermal_detection_geo (facility_id) WHERE facility_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tdg_cell ON thermal_detection_geo (cell_x, cell_y);

-- 1 km cell = floor(x / 1000), floor(y / 1000) in EPSG:6933 (equal-area, metres).
-- A detection belongs to the facility whose (radius + 500 m VIIRS buffer) contains it with the largest margin,
-- the same rule as SiteIndex.match.
CREATE OR REPLACE FUNCTION assign_thermal_geo() RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE n BIGINT;
BEGIN
    INSERT INTO thermal_detection_geo (detection_id, facility_id, cell_x, cell_y)
    SELECT d.detection_id,
           -- 6500 m (= the largest stored radius 6000 m + 500 m buffer) is a constant, so the GiST index on
           -- facilities.geometry::geography drives the search; the per-facility radius is applied afterwards.
           -- (A per-row radius inside ST_DWithin cannot use the index: ~50 ms per detection instead of ~0.3.)
           (SELECT f.facility_id FROM facilities f
             WHERE ST_DWithin(d.geometry::geography, f.geometry::geography, 6500)
               AND ST_Distance(d.geometry::geography, f.geometry::geography) <= f.radius_m + 500
             ORDER BY f.radius_m + 500 - ST_Distance(d.geometry::geography, f.geometry::geography) DESC
             LIMIT 1),
           floor(ST_X(ST_Transform(d.geometry, 6933)) / 1000)::int,
           floor(ST_Y(ST_Transform(d.geometry, 6933)) / 1000)::int
      FROM thermal_detections d
     WHERE NOT EXISTS (SELECT 1 FROM thermal_detection_geo g WHERE g.detection_id = d.detection_id);
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$;

CREATE INDEX IF NOT EXISTS idx_facilities_geog ON facilities USING GIST ((geometry::geography));

DROP MATERIALIZED VIEW IF EXISTS thermal_baseline;
CREATE MATERIALIZED VIEW thermal_baseline AS
WITH bounds AS (
    SELECT max(acq_date) - 14 AS window_end, max(acq_date) - 14 - 364 AS window_start
      FROM thermal_detections
),
covered AS (            -- days in the window that have data at all
    SELECT DISTINCT d.acq_date
      FROM thermal_detections d, bounds b
     WHERE d.acq_date BETWEEN b.window_start AND b.window_end
),
covered_month AS (
    SELECT extract(month FROM acq_date)::int AS m, count(*)::int AS days FROM covered GROUP BY 1
),
physical AS (           -- one row per physical detection: SP beats NRT, low confidence dropped
    SELECT DISTINCT ON (d.satellite, d.acq_datetime, round(ST_Y(d.geometry)::numeric, 4), round(ST_X(d.geometry)::numeric, 4))
           d.detection_id, d.acq_date, d.frp, d.daynight
      FROM thermal_detections d, bounds b
     WHERE d.acq_date BETWEEN b.window_start AND b.window_end
       AND d.confidence <> 'low'
     ORDER BY d.satellite, d.acq_datetime, round(ST_Y(d.geometry)::numeric, 4), round(ST_X(d.geometry)::numeric, 4),
              (d.product = 'SP') DESC
),
assigned AS (
    SELECT 'facility'::text AS scope, g.facility_id AS key, p.acq_date, p.frp, p.daynight
      FROM physical p JOIN thermal_detection_geo g USING (detection_id) WHERE g.facility_id IS NOT NULL
    UNION ALL
    SELECT 'cell', g.cell_x || '_' || g.cell_y, p.acq_date, p.frp, p.daynight
      FROM physical p JOIN thermal_detection_geo g USING (detection_id)
),
per_day AS (
    SELECT scope, key, acq_date, sum(frp) AS frp_sum, count(*) AS n
      FROM assigned GROUP BY 1, 2, 3
),
per_month AS (
    SELECT scope, key, extract(month FROM acq_date)::int AS m, count(*)::int AS active_days
      FROM per_day GROUP BY 1, 2, 3
),
monthly AS (            -- 12 entries, January..December; NULL where the window has no covered day
    SELECT k.scope, k.key,
           array_agg(CASE WHEN cm.days > 0 THEN round(coalesce(pm.active_days, 0)::numeric / cm.days, 4)::float8 END
                     ORDER BY mm.m) AS profile
      FROM (SELECT DISTINCT scope, key FROM per_month) k
      CROSS JOIN generate_series(1, 12) AS mm(m)
      LEFT JOIN covered_month cm ON cm.m = mm.m
      LEFT JOIN per_month pm ON pm.scope = k.scope AND pm.key = k.key AND pm.m = mm.m
     GROUP BY 1, 2
),
detection_stats AS (
    SELECT scope, key,
           count(*)::int AS n_detections,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY frp) AS frp_median,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY frp) AS frp_p95,
           avg((daynight = 'N')::int)::float8 AS night_share
      FROM assigned GROUP BY 1, 2
),
day_stats AS (
    SELECT scope, key, count(*)::int AS active_days, sum(frp_sum) AS s1, sum(frp_sum * frp_sum) AS s2
      FROM per_day GROUP BY 1, 2
)
SELECT ds.scope, ds.key,
       ds.active_days,
       (SELECT count(*) FROM covered)::int AS covered_days,
       ds.active_days::float8 / NULLIF((SELECT count(*) FROM covered), 0) AS active_fraction,
       mo.profile AS monthly_profile,
       st.frp_median, st.frp_p95, st.night_share, st.n_detections,
       ds.s1 / NULLIF((SELECT count(*) FROM covered), 0) AS daily_frp_mean,
       sqrt(greatest(ds.s2 / NULLIF((SELECT count(*) FROM covered), 0)
                     - power(ds.s1 / NULLIF((SELECT count(*) FROM covered), 0), 2), 0)) AS daily_frp_std,
       b.window_start, b.window_end
  FROM day_stats ds
  JOIN detection_stats st USING (scope, key)
  JOIN monthly mo USING (scope, key)
  CROSS JOIN bounds b;

CREATE UNIQUE INDEX idx_thermal_baseline_key ON thermal_baseline (scope, key);

-- what the current thermal source looked like against its own history
ALTER TABLE thermal_sources
    ADD COLUMN IF NOT EXISTS baseline_scope           TEXT,
    ADD COLUMN IF NOT EXISTS baseline_key             TEXT,
    ADD COLUMN IF NOT EXISTS baseline_active_fraction DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS baseline_covered_days    INTEGER,
    ADD COLUMN IF NOT EXISTS baseline_frp_median      DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS baseline_frp_p95         DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS baseline_night_share     DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS baseline_monthly         JSONB,
    ADD COLUMN IF NOT EXISTS baseline_window_end      DATE,
    ADD COLUMN IF NOT EXISTS frp_today                DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS frp_today_date           DATE,
    ADD COLUMN IF NOT EXISTS frp_z                    DOUBLE PRECISION;
