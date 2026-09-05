"""
PostGIS repository — queries the ``industrial_sites`` table defined in
``industrial-data/sql/schema.sql``.

Schema notes (do NOT add a second CREATE TABLE — this reads the existing one):
  • industrial_sites.geometry  : geometry(GEOMETRY, 4326), GIST-indexed
  • industrial_sites.operational_status : TEXT  (frontend calls this 'status')
  • industrial_sites.osm_ids / government_ids : JSONB arrays
  • industrial_sites.site_id   : TEXT UNIQUE

Spatial bbox queries use:
  ST_Intersects(geometry, ST_MakeEnvelope(west, south, east, north, 4326))
which is sargable — the planner will use idx_industrial_sites_geometry_gist.

This module is only imported / executed when DATA_MODE=postgis.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text

from backend.app.db import get_engine

logger = logging.getLogger(__name__)

# ── Column list shared by all SELECT queries ─────────────────────────────────
# ST_Centroid handles Point, Polygon, and MultiPolygon transparently.
# SRID 4326 → lon/lat in degrees.
_SELECT_COLS = """
    site_id,
    COALESCE(name, '')                              AS name,
    normalized_name,
    COALESCE(industry_type, '')                     AS industry_type,
    ST_X(ST_Centroid(geometry))                     AS longitude,
    ST_Y(ST_Centroid(geometry))                     AS latitude,
    COALESCE(state, '')                             AS state,
    COALESCE(district, '')                          AS district,
    COALESCE(address, '')                           AS address,
    COALESCE(operational_status, '')                AS status,
    COALESCE(osm_ids, '[]'::jsonb)                  AS osm_ids,
    COALESCE(government_ids, '[]'::jsonb)           AS government_ids,
    source_count,
    match_score,
    COALESCE(match_confidence, 'low')               AS match_confidence,
    COALESCE(match_method, '')                      AS match_method,
    review_required,
    last_verified::text                             AS last_verified
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _parse_jsonb(value: Any) -> list:
    """Coerce a JSONB DB value (could be list, str, or None) to a Python list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            result = json.loads(value)
            return result if isinstance(result, list) else []
        except (ValueError, TypeError):
            return []
    return []


def _row_to_site_dict(row: Any) -> dict[str, Any]:
    """Convert a SQLAlchemy RowMapping to the canonical site dict used by catalog.py."""
    osm_ids = _parse_jsonb(row["osm_ids"])
    gov_ids = _parse_jsonb(row["government_ids"])

    data_sources: list[str] = []
    if osm_ids:
        data_sources.append("openstreetmap")
    if gov_ids:
        data_sources.append("government")

    last_verified = row["last_verified"]
    if last_verified is not None and not isinstance(last_verified, str):
        last_verified = str(last_verified)

    industry_type = row["industry_type"] or ""

    return {
        "site_id":                  row["site_id"],
        "name":                     row["name"],
        "normalized_name":          row["normalized_name"] or _normalize(row["name"]),
        "industry_type":            industry_type,
        "normalized_industry_type": _normalize(industry_type),
        "latitude":                 float(row["latitude"] or 0.0),
        "longitude":                float(row["longitude"] or 0.0),
        "state":                    row["state"],
        "district":                 row["district"],
        "address":                  row["address"],
        "status":                   row["status"],
        "osm_ids":                  osm_ids,
        "government_ids":           gov_ids,
        "source_ids":               [f"osm:{i}" for i in osm_ids] + [f"gov:{i}" for i in gov_ids],
        "data_sources":             data_sources,
        "source_count":             int(row["source_count"] or 0),
        "match_score":              float(row["match_score"] or 0.0),
        "match_confidence":         row["match_confidence"],
        "match_method":             row["match_method"],
        "review_required":          bool(row["review_required"]),
        "last_verified":            last_verified or "",
    }


def _build_where(raw_filters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """
    Construct a safe parameterised WHERE clause from raw filter dict.
    Returns (where_sql_fragment, params_dict).
    """
    conditions: list[str] = ["1=1"]
    params: dict[str, Any] = {}

    # ── Spatial bbox — uses GIST index ──────────────────────────────────────
    bbox_str = raw_filters.get("bbox")
    if bbox_str:
        try:
            parts = [float(v) for v in str(bbox_str).split(",")]
            if len(parts) == 4:
                west, south, east, north = parts
                conditions.append(
                    "ST_Intersects("
                    "  geometry,"
                    "  ST_MakeEnvelope(:bbox_west, :bbox_south, :bbox_east, :bbox_north, 4326)"
                    ")"
                )
                params["bbox_west"]  = west
                params["bbox_south"] = south
                params["bbox_east"]  = east
                params["bbox_north"] = north
        except (ValueError, TypeError):
            logger.warning("Ignoring invalid bbox value: %r", bbox_str)

    # ── Attribute filters — all case-insensitive ─────────────────────────────
    if raw_filters.get("state"):
        conditions.append("lower(state) = lower(:state)")
        params["state"] = raw_filters["state"]

    if raw_filters.get("district"):
        conditions.append("lower(district) = lower(:district)")
        params["district"] = raw_filters["district"]

    if raw_filters.get("industry_type"):
        conditions.append("lower(industry_type) = lower(:industry_type)")
        params["industry_type"] = raw_filters["industry_type"]

    if raw_filters.get("status"):
        conditions.append("lower(operational_status) = lower(:status)")
        params["status"] = raw_filters["status"]

    if raw_filters.get("confidence"):
        conditions.append("lower(match_confidence) = lower(:confidence)")
        params["confidence"] = raw_filters["confidence"]

    # ── Full-text search (shared with search_sites) ──────────────────────────
    if raw_filters.get("q"):
        conditions.append(
            "("
            "  name            ILIKE :q_pattern"
            "  OR normalized_name ILIKE :q_pattern"
            "  OR industry_type   ILIKE :q_pattern"
            "  OR state           ILIKE :q_pattern"
            "  OR district        ILIKE :q_pattern"
            "  OR address         ILIKE :q_pattern"
            ")"
        )
        params["q_pattern"] = f"%{raw_filters['q']}%"

    return " AND ".join(conditions), params


# ── Public API (mirrors the functions in catalog.py) ─────────────────────────

def filter_sites(raw_filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Return sites matching all supplied filters; bbox uses spatial index."""
    where, params = _build_where(raw_filters)
    sql = text(
        f"SELECT {_SELECT_COLS}"
        f"FROM industrial_sites"
        f" WHERE {where}"
        f" ORDER BY name"
    )
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(sql, params).mappings().all()
        return [_row_to_site_dict(r) for r in rows]
    except Exception as exc:
        logger.error("PostGIS filter_sites error: %s", exc, exc_info=True)
        raise


def search_sites(query: str, raw_filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Full-text ILIKE search combined with any attribute/bbox filters."""
    filters = dict(raw_filters or {})
    filters["q"] = query
    return filter_sites(filters)


def get_site_by_id(site_id: str) -> dict[str, Any] | None:
    """Fetch a single site by its unique site_id."""
    sql = text(
        f"SELECT {_SELECT_COLS}"
        f"FROM industrial_sites"
        f" WHERE site_id = :site_id"
    )
    try:
        with get_engine().connect() as conn:
            row = conn.execute(sql, {"site_id": site_id}).mappings().first()
        return _row_to_site_dict(row) if row else None
    except Exception as exc:
        logger.error("PostGIS get_site_by_id error: %s", exc, exc_info=True)
        raise


def get_statistics() -> dict[str, Any]:
    """Aggregate statistics from the industrial_sites table."""
    agg_sql = text("""
        SELECT
            COUNT(*)                                                       AS total_sites,
            COUNT(*) FILTER (WHERE jsonb_array_length(osm_ids)        > 0) AS osm_records,
            COUNT(*) FILTER (WHERE jsonb_array_length(government_ids) > 0) AS government_records,
            COUNT(*) FILTER (WHERE source_count > 1)                       AS matched_records,
            COUNT(*) FILTER (WHERE match_confidence = 'high')              AS high_confidence_matches
        FROM industrial_sites
    """)
    by_state_sql = text("""
        SELECT COALESCE(state, 'Unknown') AS grp, COUNT(*) AS cnt
        FROM industrial_sites
        GROUP BY state
        ORDER BY state
    """)
    by_type_sql = text("""
        SELECT COALESCE(industry_type, 'Unknown') AS grp, COUNT(*) AS cnt
        FROM industrial_sites
        GROUP BY industry_type
        ORDER BY industry_type
    """)
    by_status_sql = text("""
        SELECT COALESCE(operational_status, 'unknown') AS grp, COUNT(*) AS cnt
        FROM industrial_sites
        GROUP BY operational_status
        ORDER BY operational_status
    """)
    by_confidence_sql = text("""
        SELECT COALESCE(match_confidence, 'unknown') AS grp, COUNT(*) AS cnt
        FROM industrial_sites
        GROUP BY match_confidence
        ORDER BY match_confidence
    """)
    try:
        with get_engine().connect() as conn:
            agg     = conn.execute(agg_sql).mappings().first()
            by_state   = {r["grp"]: int(r["cnt"]) for r in conn.execute(by_state_sql).mappings()}
            by_type    = {r["grp"]: int(r["cnt"]) for r in conn.execute(by_type_sql).mappings()}
            by_status  = {r["grp"]: int(r["cnt"]) for r in conn.execute(by_status_sql).mappings()}
            by_conf    = {r["grp"]: int(r["cnt"]) for r in conn.execute(by_confidence_sql).mappings()}

        total   = int(agg["total_sites"])
        matched = int(agg["matched_records"])
        return {
            "total_sites":           total,
            "by_state":              by_state,
            "by_industry_type":      by_type,
            "by_status":             by_status,
            "osm_records":           int(agg["osm_records"]),
            "government_records":    int(agg["government_records"]),
            "matched_records":       matched,
            "unmatched_records":     total - matched,
            "high_confidence_matches": int(agg["high_confidence_matches"]),
            "confidence_distribution": by_conf,
        }
    except Exception as exc:
        logger.error("PostGIS get_statistics error: %s", exc, exc_info=True)
        raise


def get_filter_options() -> dict[str, list[str]]:
    """Return distinct values for each filterable column, for dropdown population."""
    queries: dict[str, str] = {
        "states":            "SELECT DISTINCT state           FROM industrial_sites WHERE state           IS NOT NULL ORDER BY state",
        "districts":         "SELECT DISTINCT district        FROM industrial_sites WHERE district        IS NOT NULL ORDER BY district",
        "industry_types":    "SELECT DISTINCT industry_type   FROM industrial_sites WHERE industry_type   IS NOT NULL ORDER BY industry_type",
        "statuses":          "SELECT DISTINCT operational_status FROM industrial_sites WHERE operational_status IS NOT NULL ORDER BY operational_status",
        "confidence_levels": "SELECT DISTINCT match_confidence FROM industrial_sites WHERE match_confidence IS NOT NULL ORDER BY match_confidence",
    }
    try:
        with get_engine().connect() as conn:
            return {
                key: [row[0] for row in conn.execute(text(sql))]
                for key, sql in queries.items()
            }
    except Exception as exc:
        logger.error("PostGIS get_filter_options error: %s", exc, exc_info=True)
        raise
