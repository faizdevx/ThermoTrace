"""Transactional, set-based sync of osm_industrial_raw -> osm_industries -> industrial_sites.

``osm_industrial_raw`` is owned by osm2pgsql (kept current by replication).
``apply_osm_sync`` turns it into the application tables inside ONE caller-owned
transaction. All heavy work happens inside PostgreSQL via temp tables, so Python
memory use is constant no matter how many rows change.

Change detection is content-hash based (tags + normalised geometry), so an
unchanged source never creates new revisions, and replaying the same replication
state is a no-op (idempotent).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

SOURCE = "openstreetmap"
UUID_NAMESPACE_URL = "6ba7b811-9dad-11d1-80b4-00c04fd430c8"  # uuid.NAMESPACE_URL


class MassDeactivationError(RuntimeError):
    """Refusing to deactivate a suspicious share of active sites (likely a bad import)."""


@dataclass
class OsmSyncResult:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped_invalid: int = 0
    unchanged: int = 0
    raw_rows: int = 0
    revision: int | None = None
    changed: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


_STAGE_SQL = """
CREATE TEMP TABLE _stage ON COMMIT DROP AS
WITH norm AS (
    SELECT
        CASE r.osm_type WHEN 'N' THEN 'node' WHEN 'W' THEN 'way' ELSE 'relation' END AS osm_type,
        r.osm_id,
        r.version,
        r.changeset,
        r.osm_timestamp,
        r.tags,
        CASE
            WHEN GeometryType(r.geom) IN ('POLYGON', 'MULTIPOLYGON')
                THEN ST_Multi(ST_CollectionExtract(ST_MakeValid(r.geom), 3))
            ELSE r.geom
        END AS geom
    FROM osm_industrial_raw r
)
SELECT
    n.*,
    COALESCE(n.tags->>'name', n.tags->>'name:en', n.tags->>'official_name') AS name,
    (SELECT m.industry_type
       FROM industry_class_map m
      WHERE (m.tag_key, m.tag_value) IN (
                ('industrial', n.tags->>'industrial'), ('power', n.tags->>'power'),
                ('man_made',   n.tags->>'man_made'),   ('building', n.tags->>'building'),
                ('landuse',    n.tags->>'landuse'))
      ORDER BY CASE m.tag_key WHEN 'industrial' THEN 1 WHEN 'power' THEN 2
                              WHEN 'man_made' THEN 3 WHEN 'building' THEN 4 ELSE 5 END
      LIMIT 1) AS mapped_type
FROM norm n
WHERE n.geom IS NOT NULL
  AND NOT ST_IsEmpty(n.geom)
  AND ST_IsValid(n.geom)
  AND ST_XMin(n.geom) >= -180 AND ST_XMax(n.geom) <= 180
  AND ST_YMin(n.geom) >= -90  AND ST_YMax(n.geom) <= 90;

ALTER TABLE _stage ADD COLUMN industry_type TEXT, ADD COLUMN feature_class TEXT,
    ADD COLUMN op_status TEXT, ADD COLUMN evidence_score INTEGER, ADD COLUMN evidence_level TEXT,
    ADD COLUMN content_hash TEXT, ADD COLUMN site_key TEXT;

UPDATE _stage SET
    industry_type = COALESCE(mapped_type, 'Other Industrial'),
    feature_class = CASE
        WHEN tags->>'power' = 'plant' THEN 'power_plant'
        WHEN tags->>'man_made' IN ('wastewater_plant', 'water_works') THEN 'utility'
        WHEN tags->>'man_made' = 'works' OR tags ? 'industrial' THEN 'facility'
        WHEN tags->>'building' = 'industrial' THEN 'building'
        ELSE 'landuse_area' END,
    op_status = CASE
        WHEN tags->>'abandoned' = 'yes' THEN 'abandoned'
        WHEN tags->>'disused' = 'yes' THEN 'disused'
        WHEN tags ? 'construction' OR tags->>'landuse' = 'construction' THEN 'under_construction'
        ELSE 'mapped' END,
    evidence_score =
          (CASE WHEN name IS NOT NULL THEN 1 ELSE 0 END)
        + (CASE WHEN tags ?| ARRAY['operator', 'owner', 'brand'] THEN 1 ELSE 0 END)
        + (CASE WHEN (tags->>'industrial' IS NOT NULL AND tags->>'industrial' <> 'yes')
                  OR tags->>'man_made' = 'works' OR tags->>'power' = 'plant' THEN 1 ELSE 0 END)
        + (CASE WHEN tags ?| ARRAY['website', 'contact:website', 'wikidata', 'wikipedia', 'ref',
                                   'phone', 'contact:phone', 'start_date', 'product'] THEN 1 ELSE 0 END),
    content_hash = md5(tags::text || '|' || ST_AsEWKT(ST_ReducePrecision(geom, 0.0000001))),
    site_key = osm_type || '/' || osm_id;

UPDATE _stage SET evidence_level = CASE WHEN evidence_score >= 3 THEN 'high'
                                        WHEN evidence_score = 2 THEN 'medium' ELSE 'low' END;
CREATE UNIQUE INDEX ON _stage (site_key);
"""

_UPSERT_OSM_INDUSTRIES = """
INSERT INTO osm_industries (
    osm_id, osm_type, osm_version, osm_changeset, name, normalized_name,
    industrial_type, normalized_industrial_type, geometry, source, source_timestamp,
    extraction_date, first_seen, last_seen, operational_status, raw_tags, source_url,
    content_hash, is_active, deleted_at, revision)
SELECT s.osm_id, s.osm_type, s.version, s.changeset, s.name,
       lower(regexp_replace(COALESCE(s.name, ''), '\\s+', ' ', 'g')),
       s.industry_type, lower(s.industry_type), s.geom, %(source)s, s.osm_timestamp,
       CURRENT_DATE, CURRENT_DATE, CURRENT_DATE, s.op_status, s.tags,
       'https://www.openstreetmap.org/' || s.osm_type || '/' || s.osm_id,
       s.content_hash, TRUE, NULL, %(rev)s
FROM _stage s
ON CONFLICT (source, osm_type, osm_id) DO UPDATE SET
    osm_version = EXCLUDED.osm_version, osm_changeset = EXCLUDED.osm_changeset,
    name = EXCLUDED.name, normalized_name = EXCLUDED.normalized_name,
    industrial_type = EXCLUDED.industrial_type,
    normalized_industrial_type = EXCLUDED.normalized_industrial_type,
    geometry = EXCLUDED.geometry, source_timestamp = EXCLUDED.source_timestamp,
    last_seen = CURRENT_DATE, operational_status = EXCLUDED.operational_status,
    raw_tags = EXCLUDED.raw_tags, content_hash = EXCLUDED.content_hash,
    is_active = TRUE, deleted_at = NULL, revision = EXCLUDED.revision, updated_at = NOW()
WHERE osm_industries.content_hash IS DISTINCT FROM EXCLUDED.content_hash
   OR NOT osm_industries.is_active
"""

_CHANGED_MASTERS = """
CREATE TEMP TABLE _changed ON COMMIT DROP AS
SELECT s.site_key
FROM _stage s
LEFT JOIN industrial_sites m
       ON m.primary_source = %(source)s AND m.source_ref = s.site_key
WHERE m.site_id IS NULL OR NOT m.is_active OR m.content_hash IS DISTINCT FROM s.content_hash
"""

_UPSERT_MASTERS = """
WITH up AS (
    INSERT INTO industrial_sites (
        site_id, name, normalized_name, industry_type, normalized_industry_type, geometry,
        state, district, address, establishment_status, extraction_date, first_seen, last_seen,
        operational_status, osm_ids, government_ids, matched_source_ids, source_count,
        source_confidence, match_score, match_method, match_confidence, review_required,
        primary_source, source_ref, source_url, source_updated_at, osm_version, feature_class,
        geometry_type, evidence_level, evidence_score, tags, content_hash,
        is_active, deleted_at, revision)
    SELECT
        uuid_generate_v5(%(ns)s::uuid, 'industrial-site:v2:osm:' || s.osm_type || ':' || s.osm_id),
        s.name, lower(regexp_replace(COALESCE(s.name, ''), '\\s+', ' ', 'g')),
        s.industry_type, lower(s.industry_type), s.geom,
        st.name, di.name,
        NULLIF(concat_ws(', ', s.tags->>'addr:housenumber', s.tags->>'addr:street',
                         s.tags->>'addr:city', s.tags->>'addr:postcode'), ''),
        NULL, CURRENT_DATE, CURRENT_DATE, CURRENT_DATE,
        s.op_status,
        jsonb_build_array(s.site_key), '[]'::jsonb,
        jsonb_build_object('osm', jsonb_build_array(s.site_key), 'government', '[]'::jsonb,
                           'all', jsonb_build_array('osm:' || s.site_key)),
        1, s.evidence_score / 4.0, 0, 'osm_only', 'unmatched', FALSE,
        %(source)s, s.site_key, 'https://www.openstreetmap.org/' || s.site_key,
        s.osm_timestamp, s.version, s.feature_class,
        GeometryType(s.geom), s.evidence_level, s.evidence_score, s.tags, s.content_hash,
        TRUE, NULL, %(rev)s
    FROM _stage s
    JOIN _changed c ON c.site_key = s.site_key
    LEFT JOIN LATERAL (
        SELECT g.name FROM states g
         WHERE ST_Intersects(g.geometry, ST_PointOnSurface(s.geom))
         ORDER BY g.id LIMIT 1) st ON TRUE
    LEFT JOIN LATERAL (
        SELECT g.name FROM districts g
         WHERE ST_Intersects(g.geometry, ST_PointOnSurface(s.geom))
         ORDER BY g.id LIMIT 1) di ON TRUE
    ON CONFLICT (site_id) DO UPDATE SET
        name = EXCLUDED.name, normalized_name = EXCLUDED.normalized_name,
        industry_type = EXCLUDED.industry_type,
        normalized_industry_type = EXCLUDED.normalized_industry_type,
        geometry = EXCLUDED.geometry, state = EXCLUDED.state, district = EXCLUDED.district,
        address = EXCLUDED.address, last_seen = CURRENT_DATE,
        operational_status = EXCLUDED.operational_status,
        source_updated_at = EXCLUDED.source_updated_at, osm_version = EXCLUDED.osm_version,
        feature_class = EXCLUDED.feature_class, geometry_type = EXCLUDED.geometry_type,
        evidence_level = EXCLUDED.evidence_level, evidence_score = EXCLUDED.evidence_score,
        source_confidence = EXCLUDED.source_confidence,
        tags = EXCLUDED.tags, content_hash = EXCLUDED.content_hash,
        is_active = TRUE, deleted_at = NULL, revision = EXCLUDED.revision, updated_at = NOW()
    RETURNING (xmax = 0) AS inserted, site_id, source_ref, osm_version
), ledger AS (
    INSERT INTO site_source_records (
        site_id, source_system, source_key, source_id, source_table, source_version,
        first_linked, last_linked)
    SELECT site_id, 'osm', 'osm:' || source_ref, source_ref, 'osm_industries',
           osm_version::text, CURRENT_DATE, CURRENT_DATE
    FROM up
    ON CONFLICT (site_id, source_system, source_key) DO UPDATE SET
        source_version = EXCLUDED.source_version, last_linked = CURRENT_DATE, updated_at = NOW()
    RETURNING 1
)
SELECT COUNT(*) FILTER (WHERE inserted) AS created,
       COUNT(*) FILTER (WHERE NOT inserted) AS updated
FROM up
"""

_COUNT_REMOVALS = """
SELECT COUNT(*) FROM industrial_sites m
WHERE m.primary_source = %(source)s AND m.is_active
  AND NOT EXISTS (SELECT 1 FROM _stage s WHERE s.site_key = m.source_ref)
"""

_DEACTIVATE_SITES = """
UPDATE industrial_sites m
   SET is_active = FALSE, deleted_at = NOW(), revision = %(rev)s, updated_at = NOW()
 WHERE m.primary_source = %(source)s AND m.is_active
   AND NOT EXISTS (SELECT 1 FROM _stage s WHERE s.site_key = m.source_ref)
"""

_DEACTIVATE_OSM = """
UPDATE osm_industries o
   SET is_active = FALSE, deleted_at = NOW(), revision = %(rev)s, updated_at = NOW()
 WHERE o.source = %(source)s AND o.is_active
   AND NOT EXISTS (SELECT 1 FROM _stage s
                    WHERE s.osm_type = o.osm_type AND s.osm_id = o.osm_id)
"""


def apply_osm_sync(
    cur,
    *,
    max_deactivate_fraction: float = 0.2,
    force_admin_rebuild: bool = False,
    allow_mass_deactivation: bool = False,
) -> OsmSyncResult:
    """Apply osm_industrial_raw to the application tables. Caller commits/rolls back.

    Serialises concurrent writers by locking the single data_revision row.
    """
    result = OsmSyncResult()
    cur.execute("SELECT revision FROM data_revision WHERE id = 1 FOR UPDATE")
    current_revision = int(cur.fetchone()[0])
    rev = current_revision + 1

    if force_admin_rebuild:
        cur.execute(
            "UPDATE industrial_sites SET content_hash = NULL WHERE primary_source = %s", (SOURCE,)
        )

    cur.execute("SELECT COUNT(*) FROM osm_industrial_raw")
    result.raw_rows = int(cur.fetchone()[0])
    cur.execute(_STAGE_SQL)
    cur.execute("SELECT COUNT(*) FROM _stage")
    staged = int(cur.fetchone()[0])
    result.skipped_invalid = result.raw_rows - staged

    params = {"source": SOURCE, "rev": rev, "ns": UUID_NAMESPACE_URL}

    cur.execute("SELECT COUNT(*) FROM industrial_sites WHERE primary_source = %s AND is_active", (SOURCE,))
    active_before = int(cur.fetchone()[0])
    cur.execute(_COUNT_REMOVALS, params)
    removals = int(cur.fetchone()[0])
    if (
        not allow_mass_deactivation
        and active_before >= 200
        and removals > max_deactivate_fraction * active_before
    ):
        raise MassDeactivationError(
            f"{removals} of {active_before} active sites would be deactivated "
            f"(> {max_deactivate_fraction:.0%}); refusing. Check the raw table / replication state, "
            "or re-run with allow_mass_deactivation after review."
        )

    cur.execute(_UPSERT_OSM_INDUSTRIES, params)
    cur.execute(_CHANGED_MASTERS, params)
    cur.execute(_UPSERT_MASTERS, params)
    created, updated = cur.fetchone()
    result.created, result.updated = int(created or 0), int(updated or 0)

    cur.execute(_DEACTIVATE_SITES, params)
    result.deleted = cur.rowcount
    cur.execute(_DEACTIVATE_OSM, params)

    result.unchanged = max(staged - result.created - result.updated, 0)
    result.changed = (result.created + result.updated + result.deleted) > 0
    if result.changed:
        cur.execute(
            "UPDATE data_revision SET revision = %s, changed_at = NOW() WHERE id = 1", (rev,)
        )
        result.revision = rev
    else:
        result.revision = current_revision
    return result
