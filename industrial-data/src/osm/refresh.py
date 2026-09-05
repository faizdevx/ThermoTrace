"""OSM refresh detection and change-tracking utilities.

This module provides the analytical layer between two OSM extractions of the
same area at different points in time.  It answers three questions:

1. **What changed?**  Which features are new, updated, or absent since the
   last extraction?
2. **What should be written?**  Produce a ``RefreshMergeResult`` ready for
   ``write_osm_table()``.
3. **What is the refresh summary?**  A dict suitable for logging, JSON output,
   or the data-quality report.

The module is intentionally side-effect-free — no database I/O.

Design Notes
------------
* OSM element identity is ``(osm_type, osm_id)``.
* Change detection uses ``osm_version`` when available (populated by Overpass
  when ``out meta;`` or ``out tags geom meta;`` is requested).  When version
  is absent, ``source_timestamp`` is used as a fallback proxy.
* Features present in the existing snapshot but absent from the new fetch are
  not deleted.  Their ``last_seen`` is left at its existing value and they are
  marked ``operational_status = "not_seen_on_refresh"`` only if that column
  is not already set.  Deletion semantics require an explicit ``--hard-refresh``
  strategy (out of scope for this module).
* All merge decisions preserve ``first_seen`` (minimum date) and advance
  ``last_seen`` (maximum date), consistent with ``merge_temporal_snapshots()``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import geopandas as gpd
import pandas as pd

from src.temporal import current_extraction_date


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class OsmChangeReport:
    """Summary of changes between an existing and a fresh OSM extract.

    Attributes
    ----------
    extraction_date : date
        The date the new snapshot was fetched.
    total_existing : int
        Number of features in the pre-existing table.
    total_fetched : int
        Number of features returned by the new Overpass query.
    new_features : int
        Features in the new snapshot that were not in the existing table.
    updated_features : int
        Features in both snapshots but with a higher ``osm_version`` (or a
        newer ``source_timestamp`` when version is unavailable).
    unchanged_features : int
        Features in both snapshots with identical version/timestamp.
    removed_features : int
        Features in the existing table that are absent from the new fetch.
        These are *retained* — not deleted — unless ``hard_refresh=True``.
    """
    extraction_date: date
    total_existing: int
    total_fetched: int
    new_features: int
    updated_features: int
    unchanged_features: int
    removed_features: int
    hard_refresh: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "extraction_date": str(self.extraction_date),
            "total_existing": self.total_existing,
            "total_fetched": self.total_fetched,
            "new_features": self.new_features,
            "updated_features": self.updated_features,
            "unchanged_features": self.unchanged_features,
            "removed_features": self.removed_features,
            "hard_refresh": self.hard_refresh,
        }

    def __str__(self) -> str:
        return (
            f"OSM refresh {self.extraction_date}: "
            f"+{self.new_features} new  "
            f"~{self.updated_features} updated  "
            f"={self.unchanged_features} unchanged  "
            f"-{self.removed_features} not seen"
        )


@dataclass
class RefreshMergeResult:
    """Output of :func:`build_refresh_merge`.

    Attributes
    ----------
    merged_gdf : gpd.GeoDataFrame
        The complete merged GeoDataFrame ready to be written to PostGIS via
        ``write_osm_table()``.  Includes all existing features with temporal
        columns updated, plus all genuinely new features.
    report : OsmChangeReport
        Detailed breakdown of the changes.
    """
    merged_gdf: gpd.GeoDataFrame
    report: OsmChangeReport


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _identity_key(df: pd.DataFrame) -> pd.Series:
    """Composite key Series: ``osm_type|osm_id``."""
    return df["osm_type"].astype(str) + "|" + df["osm_id"].astype(str)


def _coerce_version(series: pd.Series) -> pd.Series:
    """Return a numeric version series, NaN where version is absent."""
    return pd.to_numeric(series, errors="coerce")


def _coerce_timestamp(series: pd.Series) -> pd.Series:
    """Return a UTC-aware datetime series, NaT where parsing fails."""
    return pd.to_datetime(series, utc=True, errors="coerce")


def _is_newer(existing_row: pd.Series, new_row: pd.Series) -> bool:
    """Return True if the new row has a higher version or newer timestamp."""
    ex_ver = _coerce_version(pd.Series([existing_row.get("osm_version")])).iloc[0]
    nw_ver = _coerce_version(pd.Series([new_row.get("osm_version")])).iloc[0]

    if pd.notna(ex_ver) and pd.notna(nw_ver):
        return nw_ver > ex_ver

    # Fallback: compare source_timestamp
    ex_ts = _coerce_timestamp(pd.Series([existing_row.get("source_timestamp")])).iloc[0]
    nw_ts = _coerce_timestamp(pd.Series([new_row.get("source_timestamp")])).iloc[0]

    if pd.notna(ex_ts) and pd.notna(nw_ts):
        return nw_ts > ex_ts

    # Cannot determine — treat as unchanged (conservative)
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_osm_changes(
    existing_gdf: gpd.GeoDataFrame | None,
    new_gdf: gpd.GeoDataFrame,
    *,
    extraction_date: date | None = None,
) -> OsmChangeReport:
    """Compare two OSM snapshots and return a change report.

    This function performs **no writes** — it is purely analytical.

    Parameters
    ----------
    existing_gdf : GeoDataFrame | None
        The previously stored OSM features (from the ``osm_industries`` table).
        If ``None`` or empty, all features in ``new_gdf`` are considered new.
    new_gdf : GeoDataFrame
        The freshly fetched OSM features.
    extraction_date : date | None
        The date to stamp on the report (defaults to today).

    Returns
    -------
    OsmChangeReport
    """
    today = extraction_date or current_extraction_date()

    if existing_gdf is None or existing_gdf.empty:
        return OsmChangeReport(
            extraction_date=today,
            total_existing=0,
            total_fetched=len(new_gdf),
            new_features=len(new_gdf),
            updated_features=0,
            unchanged_features=0,
            removed_features=0,
        )

    if new_gdf.empty:
        return OsmChangeReport(
            extraction_date=today,
            total_existing=len(existing_gdf),
            total_fetched=0,
            new_features=0,
            updated_features=0,
            unchanged_features=0,
            removed_features=len(existing_gdf),
        )

    existing_keys = set(_identity_key(existing_gdf))
    new_keys = set(_identity_key(new_gdf))

    truly_new = new_keys - existing_keys
    removed = existing_keys - new_keys
    shared = existing_keys & new_keys

    # For shared features, check whether they were edited
    updated = 0
    unchanged = 0

    if shared:
        ex_indexed = existing_gdf.copy()
        ex_indexed["_key"] = _identity_key(ex_indexed)
        ex_indexed = ex_indexed.set_index("_key")

        nw_indexed = new_gdf.copy()
        nw_indexed["_key"] = _identity_key(nw_indexed)
        nw_indexed = nw_indexed.set_index("_key")

        for key in shared:
            if key not in ex_indexed.index or key not in nw_indexed.index:
                continue
            ex_row = ex_indexed.loc[key]
            nw_row = nw_indexed.loc[key]
            if isinstance(ex_row, pd.DataFrame):
                ex_row = ex_row.iloc[0]
            if isinstance(nw_row, pd.DataFrame):
                nw_row = nw_row.iloc[0]
            if _is_newer(ex_row, nw_row):
                updated += 1
            else:
                unchanged += 1

    return OsmChangeReport(
        extraction_date=today,
        total_existing=len(existing_gdf),
        total_fetched=len(new_gdf),
        new_features=len(truly_new),
        updated_features=updated,
        unchanged_features=unchanged,
        removed_features=len(removed),
    )


def build_refresh_merge(
    existing_gdf: gpd.GeoDataFrame | None,
    new_gdf: gpd.GeoDataFrame,
    *,
    extraction_date: date | None = None,
    mark_removed_as: str | None = "not_seen_on_refresh",
) -> RefreshMergeResult:
    """Merge an existing snapshot with fresh OSM data, preserving history.

    This function implements the **refresh-aware merge** strategy:

    * **New features** — appended with ``first_seen = extraction_date``.
    * **Updated features** — geometry and tags replaced; ``last_seen``,
      ``updated_at``, ``osm_version``, ``osm_changeset`` advanced.
      ``first_seen`` is preserved from the existing record.
    * **Unchanged features** — ``last_seen`` advanced to ``extraction_date``.
    * **Removed features** — retained; ``operational_status`` set to
      *mark_removed_as* (if not already set to something meaningful);
      ``last_seen`` is **not** advanced (it reflects the last known-good date).

    The result is ready to pass directly to ``write_osm_table()`` which will
    perform a further temporal merge against whatever is already in the DB.

    Parameters
    ----------
    existing_gdf : GeoDataFrame | None
        Existing features from the ``osm_industries`` PostGIS table.
    new_gdf : GeoDataFrame
        Freshly extracted and normalised OSM features.
    extraction_date : date | None
        Override for today's date.
    mark_removed_as : str | None
        Value to assign to ``operational_status`` for features not seen in the
        new fetch.  Set to ``None`` to leave the column unchanged.

    Returns
    -------
    RefreshMergeResult
        Contains the merged GeoDataFrame and a change report.
    """
    today = extraction_date or current_extraction_date()
    report = detect_osm_changes(existing_gdf, new_gdf, extraction_date=today)

    # Fast path — no existing data
    if existing_gdf is None or existing_gdf.empty:
        return RefreshMergeResult(merged_gdf=new_gdf.copy(), report=report)

    # Fast path — nothing new fetched (network/API failure guard: don't wipe)
    if new_gdf.empty:
        result = existing_gdf.copy()
        if mark_removed_as and "operational_status" in result.columns:
            # Only mark features that don't already have a meaningful status
            mask = result["operational_status"].isna() | (result["operational_status"] == "")
            result.loc[mask, "operational_status"] = mark_removed_as
        return RefreshMergeResult(merged_gdf=result, report=report)

    # Build keyed lookups
    ex = existing_gdf.copy()
    ex["_key"] = _identity_key(ex)

    nw = new_gdf.copy()
    nw["_key"] = _identity_key(nw)

    existing_keys = set(ex["_key"])
    new_keys = set(nw["_key"])

    # ------------------------------------------------------------------
    # 1. Existing features that ARE in the new fetch — update temporal cols
    # ------------------------------------------------------------------
    ex_shared = ex[ex["_key"].isin(new_keys)].copy()
    nw_shared = nw[nw["_key"].isin(existing_keys)].copy()

    # Index by key for fast lookup
    nw_shared_idx = nw_shared.set_index("_key")

    for idx in ex_shared.index:
        key = ex_shared.at[idx, "_key"]
        if key not in nw_shared_idx.index:
            continue
        nw_row = nw_shared_idx.loc[key]
        if isinstance(nw_row, pd.DataFrame):
            nw_row = nw_row.iloc[0]

        # Advance last_seen and updated_at
        ex_shared.at[idx, "last_seen"] = today
        ex_shared.at[idx, "extraction_date"] = today

        # Propagate version metadata if available in new fetch
        for version_col in ("osm_version", "osm_changeset"):
            if version_col in nw_row.index and version_col in ex_shared.columns:
                nw_val = nw_row[version_col]
                if pd.notna(nw_val):
                    ex_shared.at[idx, version_col] = nw_val

        # Replace geometry with new geometry when the feature was updated
        if _is_newer(ex_shared.loc[idx], nw_row):
            ex_shared.at[idx, "geometry"] = nw_row["geometry"]
            # Update tags
            if "raw_tags" in nw_row.index:
                ex_shared.at[idx, "raw_tags"] = nw_row["raw_tags"]
            if "source_timestamp" in nw_row.index:
                ex_shared.at[idx, "source_timestamp"] = nw_row["source_timestamp"]

    # ------------------------------------------------------------------
    # 2. Existing features NOT in the new fetch — mark as removed
    # ------------------------------------------------------------------
    ex_removed = ex[~ex["_key"].isin(new_keys)].copy()
    if mark_removed_as and not ex_removed.empty and "operational_status" in ex_removed.columns:
        mask = ex_removed["operational_status"].isna() | (ex_removed["operational_status"] == "")
        ex_removed.loc[mask, "operational_status"] = mark_removed_as

    # ------------------------------------------------------------------
    # 3. Brand-new features (not in existing table)
    # ------------------------------------------------------------------
    nw_new = nw[~nw["_key"].isin(existing_keys)].copy()

    # ------------------------------------------------------------------
    # 4. Combine all three groups
    # ------------------------------------------------------------------
    frames = [f for f in [ex_shared, ex_removed, nw_new] if not f.empty]
    merged = gpd.GeoDataFrame(
        pd.concat([f.drop(columns=["_key"], errors="ignore") for f in frames], ignore_index=True),
        crs=new_gdf.crs or existing_gdf.crs,
    )

    return RefreshMergeResult(merged_gdf=merged, report=report)
