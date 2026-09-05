"""Geographic/spatial duplicate detection using a spatial index (STRtree).

Problem
-------
When processing adjacent districts, an industrial estate near the district
boundary may be returned by BOTH district Overpass queries.  Source-level
dedup (by osm_id) already handles this for OSM data.

This module handles the harder case: government records (which have no OSM id)
where two entries describe the same physical location but with slight coordinate
or name variations — e.g. from different years of the same survey.

Key design constraints
----------------------
* Uses ``shapely.STRtree`` for O(N log N) spatial index — NOT O(N²).
* Does NOT merge features solely because they are spatially close.
  Spatial proximity is a **necessary but not sufficient** condition.
* The deciding criteria require BOTH:
  - Distance ≤ ``proximity_meters`` (default 100 m — same building footprint)
  - Name similarity ≥ ``name_threshold`` (default 90 — near-identical names)
  - Same ``industry_type`` (unless both are None/unknown)
* Records in different state or district are never merged regardless of proximity.
* All merge decisions are recorded in the returned summary for auditability.
* Uncertain cases (proximate but name score 70-89) are FLAGGED with
  ``spatial_duplicate_candidate: True`` but NOT removed.

This module is intentionally conservative — when in doubt, keep both records.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS
from shapely.strtree import STRtree

from src.deduplication.source_dedup import DeduplicationSummary

logger = logging.getLogger(__name__)

# Threshold for "uncertain — flag but don't remove"
_UNCERTAIN_NAME_THRESHOLD = 70


@dataclass
class GeographicDedupResult:
    """Result of geographic deduplication."""
    filtered_gdf: gpd.GeoDataFrame
    flagged_candidates_gdf: gpd.GeoDataFrame   # rows with spatial_duplicate_candidate=True
    removed_count: int
    flagged_count: int
    removed_pairs: list[dict[str, Any]] = field(default_factory=list)
    flagged_pairs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_count": len(self.filtered_gdf),
            "removed_count": self.removed_count,
            "flagged_candidate_count": self.flagged_count,
        }


def _to_meters(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, CRS]:
    """Reproject to a local metric CRS for distance calculations."""
    metric_crs = gdf.estimate_utm_crs() or CRS.from_epsg(3857)
    return gdf.to_crs(metric_crs), metric_crs


def _name_similarity(a: str | None, b: str | None) -> float:
    """Token-set ratio similarity [0-100]."""
    if not a or not b:
        return 0.0
    try:
        from rapidfuzz import fuzz
        return fuzz.token_set_ratio(a, b)
    except Exception:
        return 0.0


def _industry_compatible(a: str | None, b: str | None) -> bool:
    """Return True if industry types are compatible (same, or either unknown)."""
    if not a or not b:
        return True  # unknown → compatible with anything
    return a.strip().lower() == b.strip().lower()


def _admin_compatible(row_a: pd.Series, row_b: pd.Series) -> bool:
    """Return True if state/district do not conflict."""
    for col in ("state", "district", "district_name"):
        a_val = str(row_a.get(col, "") or "").strip().lower()
        b_val = str(row_b.get(col, "") or "").strip().lower()
        if a_val and b_val and a_val != b_val:
            return False
    return True


def detect_geographic_duplicates(
    gdf: gpd.GeoDataFrame,
    *,
    proximity_meters: float = 100.0,
    name_threshold: float = 90.0,
    uncertain_threshold: float = 70.0,
    name_col: str = "normalized_name",
    industry_col: str = "normalized_industrial_type",
) -> GeographicDedupResult:
    """Detect geographic duplicates using STRtree spatial index.

    Parameters
    ----------
    gdf : GeoDataFrame
        Input features (OSM or government, already source-deduped).
    proximity_meters : float
        Features closer than this are spatial duplicate candidates.
        Default 100 m — same building / compound footprint.
    name_threshold : float
        Name similarity score [0-100] to confirm a match.
        Default 90 — near-identical names only.
    uncertain_threshold : float
        Features with name similarity in [uncertain_threshold, name_threshold)
        are flagged as ``spatial_duplicate_candidate`` but NOT removed.
    name_col : str
        Column used for name similarity.
    industry_col : str
        Column used for industry type compatibility check.

    Returns
    -------
    GeographicDedupResult
    """
    if gdf.empty or len(gdf) < 2:
        empty_flag_gdf = gdf.copy()
        empty_flag_gdf["spatial_duplicate_candidate"] = False
        return GeographicDedupResult(
            filtered_gdf=empty_flag_gdf,
            flagged_candidates_gdf=gdf.iloc[0:0].copy(),
            removed_count=0,
            flagged_count=0,
        )

    working = gdf.copy().reset_index(drop=True)
    working["spatial_duplicate_candidate"] = False

    # Project to metric CRS for distance calculations
    try:
        projected, metric_crs = _to_meters(working)
    except Exception as exc:
        logger.warning("Cannot project to metric CRS for geographic dedup: %s", exc)
        empty_flag_gdf = working.copy()
        return GeographicDedupResult(
            filtered_gdf=empty_flag_gdf,
            flagged_candidates_gdf=gdf.iloc[0:0].copy(),
            removed_count=0,
            flagged_count=0,
        )

    # Build STRtree on projected geometries
    geoms = projected.geometry.values
    tree = STRtree(geoms)

    remove_indices: set[int] = set()
    flag_indices: set[int] = set()
    removed_pairs: list[dict[str, Any]] = []
    flagged_pairs: list[dict[str, Any]] = []

    for i, geom in enumerate(geoms):
        if i in remove_indices:
            continue

        # Query the tree for features within proximity_meters
        candidate_indices = tree.query(geom.buffer(proximity_meters), predicate="intersects")
        candidate_indices = [int(j) for j in candidate_indices if int(j) != i and int(j) not in remove_indices]

        for j in candidate_indices:
            if j <= i:  # avoid processing pair (i,j) and (j,i)
                continue
            if j in remove_indices:
                continue

            # Compute actual distance
            try:
                dist = float(geoms[i].distance(geoms[j]))
            except Exception:
                continue

            if dist > proximity_meters:
                continue

            row_i = working.iloc[i]
            row_j = working.iloc[j]

            # Administrative consistency check
            if not _admin_compatible(row_i, row_j):
                continue

            # Industry compatibility
            ind_i = row_i.get(industry_col) if industry_col in working.columns else None
            ind_j = row_j.get(industry_col) if industry_col in working.columns else None
            if not _industry_compatible(ind_i, ind_j):
                continue

            # Name similarity
            name_i = row_i.get(name_col) if name_col in working.columns else None
            name_j = row_j.get(name_col) if name_col in working.columns else None
            score = _name_similarity(name_i, name_j)

            pair_info = {
                "index_kept": int(i),
                "index_removed_or_flagged": int(j),
                "distance_meters": round(dist, 2),
                "name_score": round(score, 1),
                "name_i": name_i,
                "name_j": name_j,
            }

            if score >= name_threshold:
                # High confidence: mark j as duplicate of i
                remove_indices.add(j)
                removed_pairs.append({**pair_info, "decision": "removed"})

            elif score >= uncertain_threshold:
                # Uncertain: flag both for review, keep both
                flag_indices.add(i)
                flag_indices.add(j)
                flagged_pairs.append({**pair_info, "decision": "flagged_for_review"})

    # Apply flags
    if flag_indices:
        working.loc[list(flag_indices), "spatial_duplicate_candidate"] = True

    # Filter out confirmed duplicates
    keep_mask = ~working.index.isin(remove_indices)
    filtered = working.loc[keep_mask].copy()
    flagged_gdf = filtered[filtered["spatial_duplicate_candidate"]].copy()

    return GeographicDedupResult(
        filtered_gdf=filtered,
        flagged_candidates_gdf=flagged_gdf,
        removed_count=len(remove_indices),
        flagged_count=len(flag_indices),
        removed_pairs=removed_pairs,
        flagged_pairs=flagged_pairs,
    )
