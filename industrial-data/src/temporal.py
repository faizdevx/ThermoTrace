from __future__ import annotations

from datetime import date, datetime
from typing import Any

import geopandas as gpd
import pandas as pd


def current_extraction_date() -> date:
    return datetime.utcnow().date()


def _normalize_temporal_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed


def _pick_last_non_null(series: pd.Series) -> Any:
    non_null = series.dropna()
    if non_null.empty:
        return None
    return non_null.iloc[-1]


def _merge_json_like(series: pd.Series) -> Any:
    values = [value for value in series if value is not None and not pd.isna(value)]
    if not values:
        return None
    if isinstance(values[0], dict):
        merged: dict[str, list[Any]] = {}
        for value in values:
            if not isinstance(value, dict):
                continue
            for key, entry in value.items():
                merged.setdefault(key, [])
                if isinstance(entry, list):
                    for item in entry:
                        if item not in merged[key]:
                            merged[key].append(item)
                elif entry not in merged[key]:
                    merged[key].append(entry)
        return merged
    if isinstance(values[0], list):
        merged_list: list[Any] = []
        for value in values:
            if not isinstance(value, list):
                continue
            for item in value:
                if item not in merged_list:
                    merged_list.append(item)
        return merged_list
    return values[-1]


def merge_temporal_snapshots(
    existing: gpd.GeoDataFrame | pd.DataFrame | None,
    current: gpd.GeoDataFrame | pd.DataFrame,
    *,
    key_columns: list[str],
    geometry_column: str | None = "geometry",
) -> gpd.GeoDataFrame | pd.DataFrame:
    if existing is None or len(existing) == 0:
        return current.copy()

    combined = pd.concat([existing, current], ignore_index=True, sort=False)
    if not key_columns:
        return current.copy()

    aggregations: dict[str, Any] = {}
    for column in combined.columns:
        if column in key_columns:
            continue
        if column == geometry_column:
            aggregations[column] = _pick_last_non_null
        elif column == "first_seen":
            aggregations[column] = lambda series: min((value for value in (_normalize_temporal_value(item) for item in series) if value is not None), default=None)
        elif column in {"last_seen", "extraction_date", "last_verified", "updated_at"}:
            aggregations[column] = lambda series: max((value for value in (_normalize_temporal_value(item) for item in series) if value is not None), default=None)
        elif column == "created_at":
            aggregations[column] = lambda series: min((value for value in (_normalize_temporal_value(item) for item in series) if value is not None), default=None)
        elif column in {"osm_ids", "government_ids", "matched_source_ids", "raw_record", "raw_tags"}:
            aggregations[column] = _merge_json_like
        elif column == "operational_status":
            aggregations[column] = _pick_last_non_null
        else:
            aggregations[column] = _pick_last_non_null

    merged = combined.groupby(key_columns, dropna=False, as_index=False).agg(aggregations)
    if geometry_column and geometry_column in merged.columns:
        try:
            merged = gpd.GeoDataFrame(merged, geometry=geometry_column, crs=getattr(existing, "crs", None) or getattr(current, "crs", None))
        except Exception:
            pass
    return merged
