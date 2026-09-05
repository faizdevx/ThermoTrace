from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import polygonize, unary_union
from urllib3.util.retry import Retry

from src.osm.tags import tag_filters_from_config

try:
    from shapely import make_valid
except ImportError:  # pragma: no cover
    make_valid = None


@dataclass(frozen=True)
class OverpassArea:
    mode: str
    value: str


def create_retry_session(total_retries: int, backoff_factor: float) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=total_retries,
        read=total_retries,
        connect=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _largest_polygon(geometry) -> Polygon | None:
    if geometry is None or geometry.is_empty:
        return None
    if geometry.geom_type == "Polygon":
        return geometry
    if geometry.geom_type == "MultiPolygon":
        return max(list(geometry.geoms), key=lambda item: item.area, default=None)
    if geometry.geom_type == "GeometryCollection":
        polygons = [part for part in geometry.geoms if part.geom_type == "Polygon"]
        if polygons:
            return max(polygons, key=lambda item: item.area)
    return None


def geometry_to_overpass_poly(geometry) -> str:
    polygon = _largest_polygon(geometry)
    if polygon is None:
        raise ValueError("A polygon geometry is required to build an Overpass poly query")
    coordinates = list(polygon.exterior.coords)
    return " ".join(f"{latitude} {longitude}" for longitude, latitude in coordinates)


def geometry_to_bbox(geometry) -> str:
    minx, miny, maxx, maxy = geometry.bounds
    return f"{miny},{minx},{maxy},{maxx}"


def build_overpass_query(
    config: dict[str, Any],
    geometry,
    *,
    use_bbox: bool = False,
    timeout_seconds: int | None = None,
) -> str:
    filters = tag_filters_from_config(config)
    selector = (
        geometry_to_bbox(geometry)
        if use_bbox
        else f'poly:"{geometry_to_overpass_poly(geometry)}"'
    )
    timeout = timeout_seconds or int(
        config["osm"]["source"].get("request_timeout_seconds", 180)
    )

    clauses = []
    for key, value in filters:
        if value is None:
            clauses.append(f'nwr["{key}"]({selector});')
        elif key == "man_made":
            clauses.append(f'nwr["{key}"~"^({value})$"]({selector});')
        else:
            clauses.append(f'nwr["{key}"="{value}"]({selector});')

    return "\n".join(
        [f"[out:json][timeout:{timeout}];", "(", *clauses, ");", "out tags geom center;"]
    )


def execute_overpass_query(
    overpass_url: str,
    query: str,
    *,
    request_timeout_seconds: int,
    retry_attempts: int,
    retry_backoff_seconds: float,
) -> dict[str, Any]:
    session = create_retry_session(retry_attempts, retry_backoff_seconds)
    response = session.post(
        overpass_url,
        data={"data": query},
        timeout=request_timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Relation geometry reconstruction
# ---------------------------------------------------------------------------

def _repair_geometry(geometry):
    """Attempt to repair an invalid Shapely geometry.

    Returns the repaired geometry, or the original if already valid,
    or None if the geometry cannot be repaired.
    """
    if geometry is None or geometry.is_empty:
        return None
    if geometry.is_valid:
        return geometry
    # Try shapely.make_valid (Shapely ≥2.0)
    if make_valid is not None:
        try:
            repaired = make_valid(geometry)
            if repaired is not None and not repaired.is_empty and repaired.is_valid:
                return repaired
        except Exception:
            pass
    # Fall back to buffer(0) trick
    try:
        repaired = geometry.buffer(0)
        if repaired is not None and not repaired.is_empty and repaired.is_valid:
            return repaired
    except Exception:
        pass
    return None


def _way_coords_to_linestring(geometry_points: list[dict]) -> LineString | None:
    """Convert a list of Overpass geometry dicts to a Shapely LineString."""
    if not geometry_points:
        return None
    coords = [(pt["lon"], pt["lat"]) for pt in geometry_points if "lon" in pt and "lat" in pt]
    if len(coords) < 2:
        return None
    return LineString(coords)


def _reconstruct_relation_geometry(element: dict[str, Any]) -> tuple[Any, str]:
    """Build the best possible geometry for an OSM relation element.

    Returns
    -------
    (geometry, geometry_source) where geometry_source is one of:
        "relation_reconstructed"    — valid MultiPolygon/Polygon from member ways
        "relation_center_fallback"  — centroid point from the 'center' field
        "relation_incomplete"       — None; no usable geometry available

    Notes
    -----
    The Overpass ``out tags geom center;`` directive returns:
    - ``members[].geometry`` for each member way (if the way has geometry)
    - ``center`` with {lat, lon} for the relation centroid

    We collect all outer-role member way LineStrings and attempt to polygonize
    them.  Inner-role ways are subtracted as holes if geometry is valid.
    """
    # ------------------------------------------------------------------
    # Legacy / test-mode: some Overpass response modes return a top-level
    # `geometry` array on relation elements (not `members`).  Handle this
    # the same way we handle way geometry so existing tests keep passing.
    # ------------------------------------------------------------------
    top_level_geom_points = element.get("geometry") or []
    if top_level_geom_points:
        coords = [
            (pt["lon"], pt["lat"])
            for pt in top_level_geom_points
            if "lon" in pt and "lat" in pt
        ]
        if len(coords) >= 4 and coords[0] == coords[-1]:
            raw = Polygon(coords)
            repaired = _repair_geometry(raw) or raw
            return repaired, "relation_reconstructed"
        elif len(coords) >= 2:
            return LineString(coords), "relation_reconstructed"

    members = element.get("members") or []

    outer_lines: list[LineString] = []
    inner_lines: list[LineString] = []

    for member in members:
        if member.get("type") != "way":
            continue
        geom_points = member.get("geometry") or []
        line = _way_coords_to_linestring(geom_points)
        if line is None:
            continue
        role = member.get("role", "")
        if role == "inner":
            inner_lines.append(line)
        else:
            # "outer" or empty role — treat as outer ring
            outer_lines.append(line)

    # Attempt to polygonize outer rings from member ways
    outer_polygons: list[Polygon] = []
    if outer_lines:
        try:
            outer_union = unary_union(outer_lines)
            outer_polygons = list(polygonize(outer_union))
        except Exception:
            outer_polygons = []

    if outer_polygons:
        # Subtract inner holes where possible
        inner_polygons: list[Polygon] = []
        if inner_lines:
            try:
                inner_union = unary_union(inner_lines)
                inner_polygons = list(polygonize(inner_union))
            except Exception:
                inner_polygons = []

        if inner_polygons:
            hole_union = unary_union(inner_polygons)
            outer_polygons = [
                poly.difference(hole_union) for poly in outer_polygons
            ]

        combined = unary_union(outer_polygons)
        repaired = _repair_geometry(combined)
        if repaired is not None and not repaired.is_empty:
            return repaired, "relation_reconstructed"

    # Fall back to center point
    center = element.get("center")
    if center and center.get("lat") is not None and center.get("lon") is not None:
        try:
            point = Point(float(center["lon"]), float(center["lat"]))
            return point, "relation_center_fallback"
        except Exception:
            pass

    return None, "relation_incomplete"


# ---------------------------------------------------------------------------
# Main conversion function
# ---------------------------------------------------------------------------

def overpass_elements_to_geodataframe(
    payload: dict[str, Any],
    crs: str = "EPSG:4326",
) -> gpd.GeoDataFrame:
    """Convert a raw Overpass JSON payload to a GeoDataFrame.

    Handles:
    * ``node``     → ``Point``
    * ``way``      → ``Polygon`` (closed) or ``LineString`` (open)
    * ``relation`` → Reconstructed ``MultiPolygon``/``Polygon``, or center
                     ``Point`` fallback, or dropped if no geometry available.

    A ``geometry_source`` column is added to indicate the provenance of each
    geometry:
        "node"                    - from node lat/lon
        "way"                     - from way geometry array
        "relation_reconstructed"  - Polygon/MultiPolygon from member ways
        "relation_center_fallback"- Point from Overpass center field
        "relation_incomplete"     - element skipped (geometry is None)
    """
    rows = []
    geometries = []

    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        element_type = element["type"]
        geometry = None
        geometry_source = element_type

        if element_type == "node":
            try:
                geometry = Point(float(element["lon"]), float(element["lat"]))
                geometry_source = "node"
            except (KeyError, TypeError, ValueError):
                pass

        elif element_type == "way":
            geometry_points = element.get("geometry") or []
            coords = [
                (pt["lon"], pt["lat"])
                for pt in geometry_points
                if "lon" in pt and "lat" in pt
            ]
            if len(coords) >= 4 and coords[0] == coords[-1]:
                raw = Polygon(coords)
                geometry = _repair_geometry(raw) or raw
                geometry_source = "way"
            elif len(coords) >= 2:
                geometry = LineString(coords)
                geometry_source = "way"

        elif element_type == "relation":
            geometry, geometry_source = _reconstruct_relation_geometry(element)

        if geometry is None or geometry_source == "relation_incomplete":
            # Drop elements with no usable geometry
            continue

        rows.append(
            {
                "osm_id": int(element["id"]),
                "osm_type": element_type,
                "name": tags.get("name"),
                "industrial_type": None,
                "source_timestamp": element.get("timestamp"),
                # Version and changeset are populated by Overpass when
                # the `out meta;` directive is used.  They enable the
                # refresh pipeline to detect whether an element has been
                # edited since the previous extraction without a full diff.
                "osm_version": element.get("version"),      # int | None
                "osm_changeset": element.get("changeset"),  # int | None
                "raw_tags": tags,
                "geometry_source": geometry_source,
            }
        )
        geometries.append(geometry)

    return gpd.GeoDataFrame(rows, geometry=geometries, crs=crs)
