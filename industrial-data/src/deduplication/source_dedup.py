"""Source-level duplicate detection and removal.

Source duplicates arise from the same real-world entity appearing multiple
times in a single source dataset due to data entry errors, repeated exports,
or cross-district boundary processing of OSM features.

Two strategies are implemented:

1. **Identity dedup** — exact match on a source identity key:
   - OSM: ``(source, osm_type, osm_id)``
   - Government: ``(source, source_id)``
   The first occurrence is kept; all subsequent occurrences are removed.
   This is safe because the identity key uniquely identifies a real-world
   object in the source system.

2. **Geometry dedup** — identical coordinate footprint after rounding
   to ``precision`` decimal places.  Used when identity keys are missing
   or unreliable (e.g. government records without a source_id).

Both strategies are non-destructive of the original data.  The return value
is a filtered GeoDataFrame plus a summary dict.

Design constraints
------------------
* Never merge two records based on name similarity alone.
* Always keep at least one representative per identity key.
* Removal decisions must be traceable via the summary dict.
"""
from __future__ import annotations

from typing import Any

import geopandas as gpd
import pandas as pd


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class DeduplicationSummary:
    """Result of a deduplication pass."""

    def __init__(
        self,
        strategy: str,
        input_count: int,
        output_count: int,
        removed_count: int,
        removed_groups: dict[str, list[int]],
    ) -> None:
        self.strategy = strategy
        self.input_count = input_count
        self.output_count = output_count
        self.removed_count = removed_count
        self.removed_groups = removed_groups  # identity_key → list of dropped row indices

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "input_count": self.input_count,
            "output_count": self.output_count,
            "removed_count": self.removed_count,
            "removed_group_count": len(self.removed_groups),
        }


# ---------------------------------------------------------------------------
# OSM source dedup
# ---------------------------------------------------------------------------

def dedup_osm_by_id(
    gdf: gpd.GeoDataFrame,
    *,
    source_col: str = "source",
    osm_type_col: str = "osm_type",
    osm_id_col: str = "osm_id",
) -> tuple[gpd.GeoDataFrame, DeduplicationSummary]:
    """Remove OSM records with duplicate ``(source, osm_type, osm_id)`` keys.

    This is the primary cross-district dedup for OSM data.  When the same
    OSM feature (e.g. a large industrial estate spanning a district boundary)
    is returned by two adjacent district Overpass queries, both hits share the
    same ``osm_id``.  Only the first occurrence (by DataFrame order) is kept.

    Parameters
    ----------
    gdf : GeoDataFrame
        Normalized OSM GeoDataFrame from ``normalize_osm_gdf()``.
    source_col, osm_type_col, osm_id_col : str
        Column names for the identity key.

    Returns
    -------
    (filtered_gdf, summary)
    """
    if gdf.empty:
        return gdf.copy(), DeduplicationSummary("osm_id", 0, 0, 0, {})

    input_count = len(gdf)
    key_cols = [c for c in [source_col, osm_type_col, osm_id_col] if c in gdf.columns]

    if not key_cols:
        return gdf.copy(), DeduplicationSummary("osm_id", input_count, input_count, 0, {})

    dup_mask = gdf.duplicated(subset=key_cols, keep="first")
    removed_groups: dict[str, list[int]] = {}

    for key, group in gdf[dup_mask].groupby(key_cols):
        k = str(key)
        removed_groups[k] = list(group.index)

    filtered = gdf.loc[~dup_mask].copy()
    removed_count = int(dup_mask.sum())

    return filtered, DeduplicationSummary(
        strategy="osm_id",
        input_count=input_count,
        output_count=len(filtered),
        removed_count=removed_count,
        removed_groups=removed_groups,
    )


# ---------------------------------------------------------------------------
# Government source dedup
# ---------------------------------------------------------------------------

def dedup_government_by_source_id(
    gdf: gpd.GeoDataFrame,
    *,
    source_col: str = "source",
    source_id_col: str = "source_id",
    coordinate_precision: int = 6,
) -> tuple[gpd.GeoDataFrame, DeduplicationSummary]:
    """Remove government records that are exact duplicates.

    Two-pass strategy:
    1. Identity dedup on ``(source, source_id)`` where ``source_id`` is not null.
    2. Geometry dedup on rounded centroid coordinates for records with null
       ``source_id`` (last-resort; less aggressive).

    Parameters
    ----------
    gdf : GeoDataFrame
    source_col, source_id_col : str
        Column names.
    coordinate_precision : int
        Decimal places for geometry rounding (default: 6 ≈ 0.1 m).

    Returns
    -------
    (filtered_gdf, summary)
    """
    if gdf.empty:
        return gdf.copy(), DeduplicationSummary("government_source_id", 0, 0, 0, {})

    input_count = len(gdf)
    working = gdf.copy().reset_index(drop=True)
    removed_groups: dict[str, list[int]] = {}

    # --- Pass 1: identity dedup for rows WITH a source_id ---
    has_id = (
        source_id_col in working.columns
        and source_col in working.columns
        and working[source_id_col].notna().any()
    )

    if has_id:
        id_mask = working[source_id_col].notna()
        id_rows = working[id_mask]
        dup_id_mask = id_rows.duplicated(subset=[source_col, source_id_col], keep="first")
        for key, grp in id_rows[dup_id_mask].groupby([source_col, source_id_col]):
            removed_groups[str(key)] = list(grp.index)
        working = working.loc[~(id_mask & working.index.isin(
            id_rows[dup_id_mask].index
        ))].copy()

    # --- Pass 2: geometry dedup for rows WITHOUT a source_id ---
    no_id_mask = (
        working[source_id_col].isna()
        if source_id_col in working.columns
        else pd.Series([True] * len(working), index=working.index)
    )
    no_id_rows = working[no_id_mask]

    if not no_id_rows.empty and no_id_rows.geometry.notna().any():
        working.loc[no_id_mask, "_cx"] = no_id_rows.geometry.centroid.x.round(coordinate_precision)
        working.loc[no_id_mask, "_cy"] = no_id_rows.geometry.centroid.y.round(coordinate_precision)
        geom_dup_mask = working[no_id_mask].duplicated(subset=["_cx", "_cy"], keep="first")
        for _, grp in working[no_id_mask][geom_dup_mask].iterrows():
            key = f"geom:({grp.get('_cx')},{grp.get('_cy')})"
            removed_groups.setdefault(key, []).append(grp.name)
        working = working.loc[~(no_id_mask & working.index.isin(
            working[no_id_mask][geom_dup_mask].index
        ))].copy()
        working.drop(columns=["_cx", "_cy"], errors="ignore", inplace=True)

    filtered = working
    removed_count = input_count - len(filtered)

    return filtered, DeduplicationSummary(
        strategy="government_source_id",
        input_count=input_count,
        output_count=len(filtered),
        removed_count=removed_count,
        removed_groups=removed_groups,
    )


# ---------------------------------------------------------------------------
# Geometry-only dedup (cross-source, last-resort)
# ---------------------------------------------------------------------------

def dedup_by_geometry(
    gdf: gpd.GeoDataFrame,
    *,
    coordinate_precision: int = 6,
    group_col: str | None = None,
) -> tuple[gpd.GeoDataFrame, DeduplicationSummary]:
    """Remove records with identical centroid coordinates after rounding.

    Use this ONLY as a last resort when identity keys are unavailable.
    It is safe for exact geometry duplicates but will NOT merge genuinely
    separate features that happen to have similar locations.

    Parameters
    ----------
    gdf : GeoDataFrame
    coordinate_precision : int
        Decimal places.
    group_col : str | None
        Optional column to group within before deduplication.

    Returns
    -------
    (filtered_gdf, summary)
    """
    if gdf.empty:
        return gdf.copy(), DeduplicationSummary("geometry", 0, 0, 0, {})

    input_count = len(gdf)
    working = gdf.copy().reset_index(drop=True)
    working["_cx"] = working.geometry.centroid.x.round(coordinate_precision)
    working["_cy"] = working.geometry.centroid.y.round(coordinate_precision)

    subset = ["_cx", "_cy"]
    if group_col and group_col in working.columns:
        subset = [group_col, "_cx", "_cy"]

    dup_mask = working.duplicated(subset=subset, keep="first")
    removed_groups: dict[str, list[int]] = {}
    for idx in working[dup_mask].index:
        key = f"({working.at[idx, '_cx']},{working.at[idx, '_cy']})"
        removed_groups.setdefault(key, []).append(idx)

    filtered = working.loc[~dup_mask].drop(columns=["_cx", "_cy"])
    removed_count = int(dup_mask.sum())

    return filtered, DeduplicationSummary(
        strategy="geometry",
        input_count=input_count,
        output_count=len(filtered),
        removed_count=removed_count,
        removed_groups=removed_groups,
    )
