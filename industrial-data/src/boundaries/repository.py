from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from sqlalchemy import text
from sqlalchemy.engine import Engine


def load_district_geometry_from_postgis(
    engine: Engine,
    *,
    table_name: str = "districts",
    district_name: str | None = None,
    district_source_id: str | None = None,
) -> gpd.GeoDataFrame:
    if not district_name and not district_source_id:
        raise ValueError("district_name or district_source_id is required")

    clauses = []
    parameters: dict[str, str] = {}

    if district_name:
        clauses.append("name = :district_name")
        parameters["district_name"] = district_name
    if district_source_id:
        clauses.append("source_id = :district_source_id")
        parameters["district_source_id"] = district_source_id

    query = (
        f"SELECT id, name, administrative_code, geometry, source, source_id, source_date "
        f"FROM {table_name} "
        f"WHERE {' AND '.join(clauses)} "
        f"ORDER BY id LIMIT 1"
    )
    return gpd.read_postgis(text(query), engine, params=parameters, geom_col="geometry")


def load_district_geometry_from_geojson(file_path: str | Path) -> gpd.GeoDataFrame:
    return gpd.read_file(file_path)


def load_districts_for_state(
    engine: Engine,
    *,
    state_name: str,
    states_table: str = "states",
    districts_table: str = "districts",
) -> gpd.GeoDataFrame:
    """Return all district rows whose geometry intersects the named state boundary.

    Strategy
    --------
    1. Look up the state polygon from *states_table* by name (case-insensitive).
    2. Spatially intersect all district geometries against the state polygon via
       ``ST_Intersects`` — this handles districts that straddle state borders.
    3. If the PostGIS join returns nothing, fall back to a Python-level Shapely
       filter over a full district scan.

    Parameters
    ----------
    engine : SQLAlchemy Engine
    state_name : str
        Human-readable state name, e.g. "Uttar Pradesh".
    states_table : str
        Table holding ADM1 boundaries (default: ``states``).
    districts_table : str
        Table holding ADM2 boundaries (default: ``districts``).

    Returns
    -------
    gpd.GeoDataFrame
        All districts with columns:
        id, name, administrative_code, geometry, source, source_id, source_date.

    Raises
    ------
    ValueError
        If the state is not found, or no districts can be resolved.
    """
    # ------------------------------------------------------------------
    # Step 1 — resolve state geometry
    # ------------------------------------------------------------------
    state_query = text(
        f"SELECT id, name, geometry "
        f"FROM {states_table} "
        f"WHERE LOWER(name) = LOWER(:state_name) "
        f"ORDER BY id LIMIT 1"
    )
    state_gdf = gpd.read_postgis(
        state_query,
        engine,
        params={"state_name": state_name},
        geom_col="geometry",
    )

    if state_gdf.empty:
        raise ValueError(
            f"State '{state_name}' not found in table '{states_table}'. "
            f"Run 'python scripts/run_boundaries.py' first to populate boundaries."
        )

    # ------------------------------------------------------------------
    # Step 2 — PostGIS spatial join (ST_Intersects)
    # ------------------------------------------------------------------
    district_spatial_query = text(
        f"""
        SELECT d.id, d.name, d.administrative_code, d.geometry,
               d.source, d.source_id, d.source_date
        FROM   {districts_table} d,
               {states_table}    s
        WHERE  LOWER(s.name) = LOWER(:state_name)
          AND  ST_Intersects(d.geometry, s.geometry)
        ORDER BY d.id
        """
    )
    try:
        district_gdf = gpd.read_postgis(
            district_spatial_query,
            engine,
            params={"state_name": state_name},
            geom_col="geometry",
        )
    except Exception:
        district_gdf = gpd.GeoDataFrame()

    if not district_gdf.empty:
        return district_gdf

    # ------------------------------------------------------------------
    # Step 3 — Python/Shapely fallback (full table scan + filter)
    # ------------------------------------------------------------------
    state_geom = state_gdf.geometry.iloc[0]
    all_districts_query = text(
        f"SELECT id, name, administrative_code, geometry, source, source_id, source_date "
        f"FROM {districts_table} ORDER BY id"
    )
    try:
        all_gdf = gpd.read_postgis(all_districts_query, engine, geom_col="geometry")
        if all_gdf.crs is None:
            all_gdf = all_gdf.set_crs("EPSG:4326")
        mask = all_gdf.geometry.intersects(state_geom)
        district_gdf = all_gdf.loc[mask].reset_index(drop=True)
    except Exception:
        district_gdf = gpd.GeoDataFrame()

    if district_gdf.empty:
        raise ValueError(
            f"No districts found for state '{state_name}'. "
            f"Ensure boundaries have been loaded with 'python scripts/run_boundaries.py'."
        )

    return district_gdf


def load_all_states(
    engine: Engine,
    *,
    states_table: str = "states",
) -> gpd.GeoDataFrame:
    """Return every row from *states_table*, ordered by name.

    This is the entry point for India-level orchestration.  The caller
    iterates the returned rows and invokes state-level processing for each.

    Parameters
    ----------
    engine : SQLAlchemy Engine
    states_table : str
        PostGIS table holding ADM1 boundaries (default: ``states``).

    Returns
    -------
    gpd.GeoDataFrame
        All state rows with columns:
        id, name, administrative_code, geometry, source, source_id, source_date.

    Raises
    ------
    ValueError
        If the states table is empty, which indicates boundaries have not yet
        been loaded.
    """
    query = text(
        f"SELECT id, name, administrative_code, geometry, source, source_id, source_date "
        f"FROM {states_table} "
        f"ORDER BY name"
    )
    states_gdf = gpd.read_postgis(query, engine, geom_col="geometry")

    if states_gdf.empty:
        raise ValueError(
            f"No states found in table '{states_table}'. "
            f"Run 'python scripts/run_boundaries.py' first to populate boundaries."
        )

    return states_gdf
