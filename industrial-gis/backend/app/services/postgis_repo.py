"""PostGIS repository for the live ThermoTrace dataset.

Reads ``industrial_sites`` (maintained by the sync worker, see industrial-data/src/sync).
Only ``is_active`` rows are ever served: a site whose source object was deleted or
re-tagged stays in the table for provenance but disappears from every query here.

Counting rules (used by /api/industries ``meta.total``, /api/statistics and exports):
every number counts ACTIVE sites matching the same filter set (state, district,
industry_type, status, confidence, q, bbox). Nothing is hard-coded.

Pagination: ``ORDER BY site_id`` (stable key) with limit/offset. Offset paging is not a
snapshot; every response carries ``meta.revision`` so a client can tell the dataset
changed between pages. Exports run in a single REPEATABLE READ transaction, so they are
internally consistent and never skip or duplicate rows.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from typing import Any, Iterator

from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.app.db import get_engine

logger = logging.getLogger(__name__)

SEARCH_COLUMNS = ("name", "industry_type", "state", "district", "address")

# Properties shared by list rows, details and exports.
_PROP_COLS = """
    site_id,
    COALESCE(name, '')                      AS name,
    COALESCE(normalized_name, '')           AS normalized_name,
    COALESCE(industry_type, '')             AS industry_type,
    COALESCE(normalized_industry_type, '')  AS normalized_industry_type,
    COALESCE(operational_status, '')        AS status,
    COALESCE(state, '')                     AS state,
    COALESCE(district, '')                  AS district,
    COALESCE(address, '')                   AS address,
    COALESCE(osm_ids, '[]'::jsonb)          AS osm_ids,
    COALESCE(government_ids, '[]'::jsonb)   AS government_ids,
    COALESCE(source_count, 0)               AS source_count,
    COALESCE(match_score, 0)                AS match_score,
    COALESCE(match_confidence, 'unmatched') AS match_confidence,
    COALESCE(match_method, 'osm_only')      AS match_method,
    COALESCE(review_required, FALSE)        AS review_required,
    COALESCE(last_verified::text, '')       AS last_verified,
    COALESCE(evidence_level, 'low')         AS evidence_level,
    COALESCE(evidence_score, 0)             AS evidence_score,
    COALESCE(feature_class, '')             AS feature_class,
    COALESCE(geometry_type, '')             AS geometry_type,
    COALESCE(primary_source, 'openstreetmap') AS primary_source,
    COALESCE(source_ref, '')                AS source_ref,
    COALESCE(source_url, '')                AS source_url,
    source_updated_at,
    osm_version,
    revision,
    updated_at
"""

_POINT = "ST_PointOnSurface(geometry)"


class InvalidFilter(ValueError):
    """Raised for malformed filter input (maps to HTTP 422)."""


def parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    """``west,south,east,north`` in degrees; raises InvalidFilter when malformed."""
    if value is None or value == "":
        return None
    try:
        west, south, east, north = (float(part) for part in value.split(","))
    except ValueError as exc:
        raise InvalidFilter("bbox must be four comma-separated numbers: west,south,east,north") from exc
    if not all(-180 <= x <= 180 for x in (west, east)) or not all(-90 <= y <= 90 for y in (south, north)):
        raise InvalidFilter("bbox coordinates out of range (lon -180..180, lat -90..90)")
    if west >= east or south >= north:
        raise InvalidFilter("bbox must satisfy west < east and south < north")
    return west, south, east, north


def _like(value: str) -> str:
    escaped = re.sub(r"([\\%_])", r"\\\1", value.strip())
    return f"%{escaped}%"


def build_where(filters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Parameterised WHERE over active sites. Raises InvalidFilter on malformed input."""
    conditions = ["is_active"]
    params: dict[str, Any] = {}
    bbox = parse_bbox(filters.get("bbox"))
    if bbox:
        conditions.append("ST_Intersects(geometry, ST_MakeEnvelope(:bw, :bs, :be, :bn, 4326))")
        params.update(bw=bbox[0], bs=bbox[1], be=bbox[2], bn=bbox[3])
    for key, column in (
        ("state", "state"), ("district", "district"),
        ("industry_type", "industry_type"), ("status", "operational_status"),
        ("confidence", "evidence_level"),
    ):
        value = filters.get(key)
        if value:
            conditions.append(f"lower({column}) = lower(:{key})")
            params[key] = value
    query = filters.get("q")
    if query and query.strip():
        clauses = " OR ".join(f"{col} ILIKE :q ESCAPE '\\'" for col in SEARCH_COLUMNS)
        conditions.append(f"({clauses})")
        params["q"] = _like(query)
    return " AND ".join(conditions), params


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _row_to_properties(row: Any, lon: float | None, lat: float | None) -> dict[str, Any]:
    osm_ids = list(row["osm_ids"] or [])
    gov_ids = list(row["government_ids"] or [])
    sources = []
    if osm_ids:
        sources.append("openstreetmap")
    if gov_ids:
        sources.append("government")
    return {
        "site_id": row["site_id"],
        "name": row["name"],
        "normalized_name": row["normalized_name"],
        "industry_type": row["industry_type"],
        "normalized_industry_type": row["normalized_industry_type"],
        "status": row["status"],
        "state": row["state"],
        "district": row["district"],
        "address": row["address"],
        "latitude": lat,
        "longitude": lon,
        "osm_ids": osm_ids,
        "government_ids": gov_ids,
        "source_ids": [f"osm:{i}" for i in osm_ids] + [f"gov:{i}" for i in gov_ids],
        "data_sources": sources,
        "source_count": int(row["source_count"]),
        "match_score": float(row["match_score"]),
        "match_confidence": row["match_confidence"],
        "match_method": row["match_method"],
        "review_required": bool(row["review_required"]),
        "last_verified": row["last_verified"],
        "evidence_level": row["evidence_level"],
        "evidence_score": int(row["evidence_score"]),
        "feature_class": row["feature_class"],
        "geometry_type": row["geometry_type"],
        "primary_source": row["primary_source"],
        "source_ref": row["source_ref"],
        "source_url": row["source_url"],
        "source_updated_at": _iso(row["source_updated_at"]),
        "osm_version": row["osm_version"],
        "revision": int(row["revision"] or 0),
        "updated_at": _iso(row["updated_at"]),
    }


def get_revision(conn: Connection | None = None) -> dict[str, Any]:
    sql = text("SELECT revision, changed_at FROM data_revision WHERE id = 1")
    if conn is None:
        with get_engine().connect() as c:
            row = c.execute(sql).first()
    else:
        row = conn.execute(sql).first()
    if row is None:
        return {"revision": 0, "changed_at": None}
    return {"revision": int(row[0]), "changed_at": _iso(row[1])}


def list_sites(
    filters: dict[str, Any], limit: int, offset: int, geometry: str = "point"
) -> tuple[list[dict[str, Any]], int, int]:
    """Return (features, total_matching, revision)."""
    where, params = build_where(filters)
    geom_sql = "ST_AsGeoJSON(geometry, 6)" if geometry == "full" else f"ST_AsGeoJSON({_POINT}, 6)"
    list_sql = text(
        f"SELECT {_PROP_COLS}, {geom_sql}::json AS geom, "
        f"ST_X({_POINT}) AS lon, ST_Y({_POINT}) AS lat "
        f"FROM industrial_sites WHERE {where} ORDER BY site_id LIMIT :limit OFFSET :offset"
    )
    count_sql = text(f"SELECT COUNT(*) FROM industrial_sites WHERE {where}")
    with get_engine().connect() as conn:
        # One snapshot for rows + count + revision so meta is self-consistent.
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        total = int(conn.execute(count_sql, params).scalar_one())
        rows = conn.execute(list_sql, {**params, "limit": limit, "offset": offset}).mappings().all()
        revision = get_revision(conn)["revision"]
    features = [
        {"type": "Feature", "geometry": row["geom"],
         "properties": _row_to_properties(row, row["lon"], row["lat"])}
        for row in rows
    ]
    return features, total, revision


def get_site(site_id: str) -> dict[str, Any] | None:
    """Single site with FULL geometry, tags and area. Inactive sites are reported as gone (None)."""
    sql = text(
        f"SELECT {_PROP_COLS}, ST_AsGeoJSON(geometry, 7)::json AS geom, tags, "
        f"ST_X({_POINT}) AS lon, ST_Y({_POINT}) AS lat, "
        "CASE WHEN GeometryType(geometry) LIKE '%POLYGON' "
        "     THEN ST_Area(geometry::geography) END AS area_m2 "
        "FROM industrial_sites WHERE site_id = :site_id AND is_active"
    )
    with get_engine().connect() as conn:
        row = conn.execute(sql, {"site_id": site_id}).mappings().first()
    if row is None:
        return None
    props = _row_to_properties(row, row["lon"], row["lat"])
    props["tags"] = row["tags"] or {}
    props["area_m2"] = float(row["area_m2"]) if row["area_m2"] is not None else None
    return {"type": "Feature", "geometry": row["geom"], "properties": props}


def get_statistics(filters: dict[str, Any]) -> dict[str, Any]:
    """Aggregates over ACTIVE sites matching ``filters`` (scope = the filter set)."""
    where, params = build_where(filters)

    def grouped(conn: Connection, column: str, fallback: str) -> dict[str, int]:
        sql = text(
            f"SELECT COALESCE(NULLIF({column}, ''), :fb) AS grp, COUNT(*) AS cnt "
            f"FROM industrial_sites WHERE {where} GROUP BY 1 ORDER BY cnt DESC, grp"
        )
        return {r["grp"]: int(r["cnt"]) for r in conn.execute(sql, {**params, "fb": fallback}).mappings()}

    with get_engine().connect() as conn:
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        agg = conn.execute(text(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE jsonb_array_length(COALESCE(osm_ids,'[]'::jsonb)) > 0) AS osm, "
            "COUNT(*) FILTER (WHERE jsonb_array_length(COALESCE(government_ids,'[]'::jsonb)) > 0) AS gov, "
            "COUNT(*) FILTER (WHERE source_count > 1) AS matched, "
            "COUNT(*) FILTER (WHERE match_confidence = 'high') AS high_match, "
            "COUNT(DISTINCT state) AS n_states, COUNT(DISTINCT district) AS n_districts "
            f"FROM industrial_sites WHERE {where}"), params).mappings().one()
        stats = {
            "by_state": grouped(conn, "state", "Unassigned"),
            "by_district": dict(list(grouped(conn, "district", "Unassigned").items())[:50]),
            "by_industry_type": grouped(conn, "industry_type", "Unclassified"),
            "by_status": grouped(conn, "operational_status", "unknown"),
            "by_feature_class": grouped(conn, "feature_class", "unknown"),
            "confidence_distribution": grouped(conn, "evidence_level", "unknown"),
        }
        revision = get_revision(conn)["revision"]
    total = int(agg["total"])
    return {
        "total_sites": total,
        **stats,
        "osm_records": int(agg["osm"]),
        "government_records": int(agg["gov"]),
        "matched_records": int(agg["matched"]),
        "unmatched_records": total - int(agg["matched"]),
        "high_confidence_matches": int(agg["high_match"]),
        "distinct_states": int(agg["n_states"]),
        "distinct_districts": int(agg["n_districts"]),
        "revision": revision,
        "scope": {
            "description": "Active sites matching the supplied filters (all of the loaded dataset when none).",
            "filters": {k: v for k, v in filters.items() if v},
        },
    }


def get_filter_options(state: str | None = None) -> dict[str, list[str]]:
    """Distinct values among ACTIVE sites; ``districts`` cascades from ``state`` when given."""
    def distinct(conn: Connection, column: str, extra: str = "", params: dict | None = None) -> list[str]:
        sql = text(
            f"SELECT DISTINCT {column} FROM industrial_sites "
            f"WHERE is_active AND {column} IS NOT NULL AND {column} <> '' {extra} ORDER BY 1"
        )
        return [r[0] for r in conn.execute(sql, params or {})]

    with get_engine().connect() as conn:
        districts = (
            distinct(conn, "district", "AND lower(state) = lower(:state)", {"state": state})
            if state else distinct(conn, "district")
        )
        return {
            "states": distinct(conn, "state"),
            "districts": districts,
            "industry_types": distinct(conn, "industry_type"),
            "statuses": distinct(conn, "operational_status"),
            "confidence_levels": [v for v in ("high", "medium", "low")
                                  if v in distinct(conn, "evidence_level")],
            "feature_classes": distinct(conn, "feature_class"),
        }


# ── Exports ────────────────────────────────────────────────────────────────

CSV_COLUMNS = [
    "site_id", "name", "industry_type", "feature_class", "status", "state", "district", "address",
    "latitude", "longitude", "geometry_type", "evidence_level", "evidence_score",
    "primary_source", "source_ref", "source_url", "osm_version", "source_updated_at",
    "match_method", "revision",
]
_EXPORT_BATCH = 2000


def count_matching(filters: dict[str, Any]) -> int:
    where, params = build_where(filters)
    with get_engine().connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM industrial_sites WHERE {where}"), params).scalar_one())


def _stream_rows(filters: dict[str, Any], full_geometry: bool) -> Iterator[Any]:
    where, params = build_where(filters)
    geom = "ST_AsGeoJSON(geometry, 6)" if full_geometry else f"ST_AsGeoJSON({_POINT}, 6)"
    sql = text(
        f"SELECT {_PROP_COLS}, {geom}::json AS geom, ST_X({_POINT}) AS lon, ST_Y({_POINT}) AS lat "
        f"FROM industrial_sites WHERE {where} ORDER BY site_id"
    )
    with get_engine().connect() as conn:
        conn = conn.execution_options(stream_results=True, max_row_buffer=_EXPORT_BATCH)
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        result = conn.execute(sql, params)
        for row in result.mappings():
            yield row


def export_csv(filters: dict[str, Any]) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    yield buffer.getvalue()
    for row in _stream_rows(filters, full_geometry=False):
        props = _row_to_properties(row, row["lon"], row["lat"])
        buffer.seek(0)
        buffer.truncate(0)
        writer.writerow([_csv_safe(props.get(col)) for col in CSV_COLUMNS])
        yield buffer.getvalue()


def _csv_safe(value: Any) -> Any:
    """Neutralise spreadsheet formula injection for text taken from OSM tags."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def export_geojson(filters: dict[str, Any]) -> Iterator[str]:
    yield '{"type":"FeatureCollection","features":['
    first = True
    for row in _stream_rows(filters, full_geometry=True):
        feature = {"type": "Feature", "geometry": row["geom"],
                   "properties": _row_to_properties(row, row["lon"], row["lat"])}
        yield ("" if first else ",") + json.dumps(feature, separators=(",", ":"), default=str)
        first = False
    yield "]}"


# ── Boundaries ─────────────────────────────────────────────────────────────

_BOUNDARY_TABLES = {"india": "india_boundary", "states": "states", "districts": "districts"}
_DEFAULT_SIMPLIFY = {"india": 0.02, "states": 0.01, "districts": 0.004}


def get_boundaries(level: str, state: str | None, bbox: str | None, simplify: float | None) -> dict[str, Any]:
    table = _BOUNDARY_TABLES[level]
    tolerance = simplify if simplify is not None else _DEFAULT_SIMPLIFY[level]
    conditions = ["TRUE"]
    params: dict[str, Any] = {"tol": tolerance, "level": level}
    box = parse_bbox(bbox)
    if box:
        conditions.append("ST_Intersects(b.geometry, ST_MakeEnvelope(:bw, :bs, :be, :bn, 4326))")
        params.update(bw=box[0], bs=box[1], be=box[2], bn=box[3])
    join = ""
    if state and level == "districts":
        join = "JOIN states st ON lower(st.name) = lower(:state) AND ST_Intersects(st.geometry, ST_PointOnSurface(b.geometry))"
        params["state"] = state
    elif state and level == "states":
        conditions.append("lower(b.name) = lower(:state)")
        params["state"] = state
    sql = text(
        f"SELECT b.name, b.source_id, b.source, "
        f"ST_AsGeoJSON(ST_SimplifyPreserveTopology(b.geometry, :tol), 5)::json AS geom "
        f"FROM {table} b {join} WHERE {' AND '.join(conditions)} ORDER BY b.name"
    )
    with get_engine().connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": r["geom"],
             "properties": {"boundary_level": level, "name": r["name"],
                            "source_id": r["source_id"], "source": r["source"]}}
            for r in rows
        ],
    }


# ── Sync tracking ─────────────────────────────────────────────────────────

def get_sync_rows() -> list[dict[str, Any]]:
    sql = text("SELECT * FROM sync_state ORDER BY source")
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(sql).mappings()]


def get_sync_history(source: str | None, limit: int) -> list[dict[str, Any]]:
    sql = text(
        "SELECT id, source, trigger, status, started_at, finished_at, from_cursor, to_cursor, "
        "created_count, updated_count, deleted_count, skipped_count, failed_count, revision, "
        "source_data_at, source_published_at, committed_at, ingest_latency_seconds, "
        "duration_seconds, error FROM sync_runs "
        "WHERE (:source IS NULL OR source = :source) ORDER BY id DESC LIMIT :limit"
    )
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(sql, {"source": source, "limit": limit}).mappings()]


def request_manual_sync(source: str) -> bool:
    sql = text(
        "UPDATE sync_state SET manual_requested_at = NOW() "
        "WHERE source = :source AND enabled RETURNING source"
    )
    with get_engine().begin() as conn:
        return conn.execute(sql, {"source": source}).first() is not None


def dataset_summary() -> dict[str, Any]:
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT COUNT(*) FILTER (WHERE is_active) AS active, "
            "COUNT(*) FILTER (WHERE NOT is_active) AS inactive, "
            "(SELECT COUNT(*) FROM states) AS states, (SELECT COUNT(*) FROM districts) AS districts "
            "FROM industrial_sites")).mappings().one()
        rev = get_revision(conn)
    return {"active_sites": int(row["active"]), "inactive_sites": int(row["inactive"]),
            "boundary_states": int(row["states"]), "boundary_districts": int(row["districts"]), **rev}
