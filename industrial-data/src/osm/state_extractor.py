"""State-level OSM industrial data extractor.

Orchestrates per-district Overpass queries for all districts that belong to a
given Indian state.  Supports:

* Sequential or bounded-concurrent extraction (``max_workers``)
* Per-district checkpointing so a long run can resume after failure
* Configurable inter-district pause to avoid Overpass rate-limiting
* Detailed summary statistics for logging and data-quality reporting
* Cross-district deduplication on ``(osm_type, osm_id)``

Usage
-----
::

    from src.osm.state_extractor import extract_osm_for_state

    combined_gdf, summary = extract_osm_for_state(
        state_name="Uttar Pradesh",
        engine=engine,
        config=config,
        checkpoint_dir=Path("processed/Uttar_Pradesh/osm"),
        refresh=False,
    )
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from src.boundaries.repository import load_districts_for_state
from src.osm.config import get_osm_cleaning_config, get_osm_source_config
from src.osm.overpass import (
    build_overpass_query,
    execute_overpass_query,
    overpass_elements_to_geodataframe,
)
from src.osm.processing import normalize_osm_gdf

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Checkpoint helpers
# -----------------------------------------------------------------------


def _checkpoint_path(checkpoint_dir: Path, district_name: str) -> Path:
    """Return the sentinel file path for a completed district."""
    safe_name = district_name.replace(" ", "_").replace("/", "_")
    return checkpoint_dir / f"{safe_name}.complete"


def _is_district_complete(checkpoint_dir: Path, district_name: str) -> bool:
    return _checkpoint_path(checkpoint_dir, district_name).exists()


def _mark_district_complete(checkpoint_dir: Path, district_name: str) -> None:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    _checkpoint_path(checkpoint_dir, district_name).touch()


# -----------------------------------------------------------------------
# Single-district extraction
# -----------------------------------------------------------------------


def _extract_single_district(
    district_row: pd.Series,
    config: dict[str, Any],
    *,
    overpass_urls: list[str],
    source_config,
    cleaning_config,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Run Overpass extraction + normalization for one district row.

    Returns
    -------
    (normalized_gdf, summary_dict)
    """
    district_name: str = str(district_row.get("name") or "")
    district_source_id: str | None = str(district_row.get("source_id")) if district_row.get("source_id") else None
    geometry = district_row.geometry

    if geometry is None or geometry.is_empty:
        logger.warning("District '%s' has no geometry — skipping.", district_name)
        return gpd.GeoDataFrame(), {
            "district": district_name,
            "status": "skipped_no_geometry",
            "osm_features": 0,
        }

    query = build_overpass_query(config, geometry, use_bbox=False)

    payload: dict[str, Any] | None = None
    last_error: Exception | None = None

    for url in overpass_urls:
        try:
            payload = execute_overpass_query(
                url,
                query,
                request_timeout_seconds=source_config.request_timeout_seconds,
                retry_attempts=source_config.retry_attempts,
                retry_backoff_seconds=source_config.retry_backoff_seconds,
            )
            break
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Overpass request failed for district='%s' url='%s': %s",
                district_name,
                url,
                exc,
            )

    if payload is None:
        raise RuntimeError(
            f"All Overpass endpoints failed for district '{district_name}': {last_error}"
        )

    raw_gdf = overpass_elements_to_geodataframe(payload)

    if raw_gdf.empty:
        return gpd.GeoDataFrame(), {
            "district": district_name,
            "status": "ok_empty",
            "raw_features": 0,
            "osm_features": 0,
        }

    normalized_gdf, norm_summary = normalize_osm_gdf(
        raw_gdf,
        source_name=source_config.provider,
        district_name=district_name,
        district_source_id=district_source_id,
        source_url=source_config.overpass_url,
        exact_duplicate_coordinate_precision=cleaning_config.exact_duplicate_coordinate_precision,
        probable_duplicate_distance_meters=cleaning_config.probable_duplicate_distance_meters,
        probable_duplicate_name_similarity=cleaning_config.probable_duplicate_name_similarity,
        probable_duplicate_industry_similarity=cleaning_config.probable_duplicate_industry_similarity,
    )

    return normalized_gdf, {
        "district": district_name,
        "status": "ok",
        **norm_summary,
    }


# -----------------------------------------------------------------------
# State-level orchestrator
# -----------------------------------------------------------------------


def extract_osm_for_state(
    state_name: str,
    engine,
    config: dict[str, Any],
    *,
    checkpoint_dir: Path | None = None,
    refresh: bool = False,
    max_workers: int = 1,
    pause_between_districts: float | None = None,
    states_table: str = "states",
    districts_table: str = "districts",
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Extract all industrial OSM features for every district in *state_name*.

    Parameters
    ----------
    state_name : str
        The Indian state to process, e.g. ``"Uttar Pradesh"``.
    engine : SQLAlchemy Engine
        Database engine pointing at the PostGIS instance.
    config : dict
        Loaded ``config.yaml`` dictionary.
    checkpoint_dir : Path | None
        Directory where per-district ``.complete`` sentinel files are written.
        Defaults to ``processed/<state_slug>/osm/`` relative to the current
        working directory.
    refresh : bool
        If ``True``, ignore existing checkpoint files and re-extract every
        district.
    max_workers : int
        Number of concurrent district threads.  Keep at ``1`` (default) unless
        you have explicit permission from the Overpass server operator.
        The Overpass API is a shared public service — high concurrency is
        antisocial and will trigger rate-limiting.
    pause_between_districts : float | None
        Seconds to sleep after each district query.  Defaults to
        ``config["osm"]["source"]["query_pause_seconds"]``.
    states_table : str
        PostGIS table name for state boundaries.
    districts_table : str
        PostGIS table name for district boundaries.

    Returns
    -------
    (combined_gdf, summary)
        *combined_gdf* — GeoDataFrame of all normalized OSM features for the
        state, deduplicated on ``(osm_type, osm_id)``.

        *summary* — dict with keys:
        ``state``, ``total_districts``, ``completed_districts``,
        ``skipped_districts``, ``failed_districts``, ``total_features``,
        ``deduplicated_features``, ``district_summaries``.

    Raises
    ------
    ValueError
        If the state is not found in the boundaries table.
    RuntimeError
        If *all* districts fail (no data at all).
    """
    state_slug = state_name.replace(" ", "_")
    if checkpoint_dir is None:
        checkpoint_dir = Path("processed") / state_slug / "osm"

    source_config = get_osm_source_config(config)
    cleaning_config = get_osm_cleaning_config(config)
    overpass_urls = [source_config.overpass_url, *source_config.backup_overpass_urls]
    pause = (
        pause_between_districts
        if pause_between_districts is not None
        else source_config.query_pause_seconds
    )

    # ------------------------------------------------------------------ #
    # 1. Load all districts for the state                                 #
    # ------------------------------------------------------------------ #
    logger.info("Loading district boundaries for state='%s'", state_name)
    districts_gdf = load_districts_for_state(
        engine,
        state_name=state_name,
        states_table=states_table,
        districts_table=districts_table,
    )
    if districts_gdf.crs is None:
        districts_gdf = districts_gdf.set_crs("EPSG:4326")
    else:
        districts_gdf = districts_gdf.to_crs("EPSG:4326")

    total_districts = len(districts_gdf)
    logger.info("Found %d districts for state='%s'", total_districts, state_name)

    # ------------------------------------------------------------------ #
    # 2. Determine which districts need processing                        #
    # ------------------------------------------------------------------ #
    to_process: list[pd.Series] = []
    already_done: list[str] = []

    for _, row in districts_gdf.iterrows():
        dname = str(row.get("name") or "")
        if not refresh and _is_district_complete(checkpoint_dir, dname):
            already_done.append(dname)
            logger.debug("District '%s' already complete — skipping.", dname)
        else:
            to_process.append(row)

    logger.info(
        "Districts to process: %d  |  Already complete: %d",
        len(to_process),
        len(already_done),
    )

    # ------------------------------------------------------------------ #
    # 3. Extract per-district (sequential or threaded)                   #
    # ------------------------------------------------------------------ #
    all_gdfs: list[gpd.GeoDataFrame] = []
    district_summaries: list[dict[str, Any]] = []
    failed_districts: list[str] = []
    completed_districts: list[str] = list(already_done)

    def _process_row(row: pd.Series) -> tuple[gpd.GeoDataFrame, dict]:
        dname = str(row.get("name") or "")
        try:
            gdf, summary = _extract_single_district(
                row,
                config,
                overpass_urls=overpass_urls,
                source_config=source_config,
                cleaning_config=cleaning_config,
            )
            return gdf, summary
        except Exception as exc:
            logger.error("Failed to extract district '%s': %s", dname, exc, exc_info=True)
            return gpd.GeoDataFrame(), {"district": dname, "status": "failed", "error": str(exc), "osm_features": 0}

    if max_workers <= 1:
        # Sequential — safest for Overpass API
        for row in to_process:
            dname = str(row.get("name") or "")
            gdf, summary = _process_row(row)
            district_summaries.append(summary)

            if summary.get("status") == "failed":
                failed_districts.append(dname)
            else:
                all_gdfs.append(gdf)
                completed_districts.append(dname)
                _mark_district_complete(checkpoint_dir, dname)
                logger.info(
                    "  ✓ %s — %d features", dname, summary.get("osm_features", 0)
                )

            if pause > 0:
                time.sleep(pause)

    else:
        # Threaded — use with care
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_row = {executor.submit(_process_row, row): row for row in to_process}
            for future in as_completed(future_to_row):
                row = future_to_row[future]
                dname = str(row.get("name") or "")
                try:
                    gdf, summary = future.result()
                    district_summaries.append(summary)
                    if summary.get("status") == "failed":
                        failed_districts.append(dname)
                    else:
                        all_gdfs.append(gdf)
                        completed_districts.append(dname)
                        _mark_district_complete(checkpoint_dir, dname)
                        logger.info(
                            "  ✓ %s — %d features", dname, summary.get("osm_features", 0)
                        )
                except Exception as exc:
                    logger.error("Unexpected error for district '%s': %s", dname, exc)
                    failed_districts.append(dname)
                    district_summaries.append({"district": dname, "status": "failed", "error": str(exc)})

    # ------------------------------------------------------------------ #
    # 4. Combine and cross-district deduplicate                           #
    # ------------------------------------------------------------------ #
    non_empty = [g for g in all_gdfs if g is not None and not g.empty]

    if not non_empty:
        if failed_districts:
            raise RuntimeError(
                f"All {len(failed_districts)} districts failed for state '{state_name}'. "
                f"Check logs. Failed: {failed_districts[:5]}..."
            )
        logger.warning("No OSM features found for state '%s'.", state_name)
        combined_gdf = gpd.GeoDataFrame(columns=["osm_id", "osm_type", "geometry"], crs="EPSG:4326")
        total_features = 0
        dedup_features = 0
    else:
        combined_gdf = gpd.GeoDataFrame(
            pd.concat(non_empty, ignore_index=True),
            crs="EPSG:4326",
        )
        total_features = len(combined_gdf)

        # Deduplicate on (osm_type, osm_id) — removes cross-district overlaps
        before_dedup = len(combined_gdf)
        combined_gdf = combined_gdf.drop_duplicates(
            subset=["osm_type", "osm_id"], keep="first"
        ).reset_index(drop=True)
        dedup_features = len(combined_gdf)
        cross_district_dupes = before_dedup - dedup_features
        if cross_district_dupes:
            logger.info(
                "Cross-district deduplication removed %d duplicate features.",
                cross_district_dupes,
            )

    summary: dict[str, Any] = {
        "state": state_name,
        "total_districts": total_districts,
        "completed_districts": len(completed_districts),
        "skipped_districts": len(already_done),
        "failed_districts": len(failed_districts),
        "failed_district_names": failed_districts,
        "total_features_before_dedup": total_features if non_empty else 0,
        "total_features": dedup_features if non_empty else 0,
        "district_summaries": district_summaries,
    }

    return combined_gdf, summary
