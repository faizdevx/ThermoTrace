from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import MultiPolygon

try:
    from shapely import make_valid
except ImportError:  # pragma: no cover
    make_valid = None


def _first_non_empty_value(row: pd.Series, candidate_columns: list[str]) -> Any:
    for column in candidate_columns:
        if column in row and pd.notna(row[column]):
            value = row[column]
            if isinstance(value, str):
                value = value.strip()
            if value not in ("", None):
                return value
    return None


def _to_multipolygon(geometry):
    if geometry is None or geometry.is_empty:
        return None

    if geometry.geom_type == "MultiPolygon":
        return geometry

    if geometry.geom_type == "Polygon":
        return MultiPolygon([geometry])

    if geometry.geom_type == "GeometryCollection":
        polygons = []
        for part in geometry.geoms:
            if part.geom_type == "Polygon":
                polygons.append(part)
            elif part.geom_type == "MultiPolygon":
                polygons.extend(list(part.geoms))
        if polygons:
            return MultiPolygon(polygons)

    return None


def repair_geometry(geometry):
    if geometry is None or geometry.is_empty:
        return None, False, "empty"

    if geometry.is_valid:
        return _to_multipolygon(geometry), False, "valid"

    repaired = None
    repair_method = None

    if make_valid is not None:
        repaired = make_valid(geometry)
        repair_method = "make_valid"

    if repaired is None or repaired.is_empty or not repaired.is_valid:
        repaired = geometry.buffer(0)
        repair_method = "buffer_0"

    if repaired is not None and not repaired.is_empty and repaired.is_valid:
        repaired = _to_multipolygon(repaired)
        if repaired is not None and not repaired.is_empty and repaired.is_valid:
            return repaired, True, repair_method or "repaired"

    return None, False, "unrepairable"


def normalize_text(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        cleaned = " ".join(value.split())
        return cleaned if cleaned else None
    return value


def clean_boundary_gdf(
    gdf: gpd.GeoDataFrame,
    *,
    level_name: str,
    table_name: str,
    source_name: str,
    metadata: dict[str, Any],
    name_candidates: list[str],
    administrative_code_candidates: list[str],
    source_id_candidates: list[str],
    storage_crs: str,
    source_assumed_crs: str,
    allow_make_valid: bool = True,
    remove_exact_duplicates: bool = True,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    if gdf.empty:
        return gdf.copy(), {
            "level_name": level_name,
            "table_name": table_name,
            "source_name": source_name,
            "total_rows": 0,
            "valid_rows": 0,
            "repaired_rows": 0,
            "rejected_rows": 0,
            "duplicate_rows_removed": 0,
        }

    working = gdf.copy()
    if working.crs is None:
        working = working.set_crs(source_assumed_crs)

    working = working.to_crs(storage_crs)

    cleaned_rows = []
    rejected_rows = 0
    repaired_rows = 0
    rejected_records: list[dict[str, Any]] = []

    for _, row in working.iterrows():
        raw_geometry = row.geometry
        repaired_geometry, was_repaired, status = repair_geometry(raw_geometry) if allow_make_valid else (_to_multipolygon(raw_geometry), False, "valid")

        if repaired_geometry is None:
            rejected_rows += 1
            rejected_records.append({
                "name": _first_non_empty_value(row, name_candidates),
                "reason": status,
            })
            continue

        if was_repaired:
            repaired_rows += 1

        cleaned_rows.append(
            {
                "name": normalize_text(_first_non_empty_value(row, name_candidates)),
                "administrative_code": normalize_text(_first_non_empty_value(row, administrative_code_candidates)),
                "geometry": repaired_geometry,
                "source": source_name,
                "source_id": normalize_text(_first_non_empty_value(row, source_id_candidates)),
                "source_date": metadata.get("source_date"),
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "source_url": metadata.get("source_url"),
                "level_name": level_name,
            }
        )

    cleaned = gpd.GeoDataFrame(cleaned_rows, geometry="geometry", crs=storage_crs)

    if remove_exact_duplicates and not cleaned.empty:
        cleaned["_geometry_wkb"] = cleaned.geometry.apply(lambda geom: geom.wkb_hex if geom is not None else None)
        duplicate_mask = cleaned.duplicated(subset=["name", "administrative_code", "source_id", "_geometry_wkb"], keep="first")
        duplicate_rows_removed = int(duplicate_mask.sum())
        cleaned = cleaned.loc[~duplicate_mask].drop(columns=["_geometry_wkb"])
    else:
        duplicate_rows_removed = 0

    summary = {
        "level_name": level_name,
        "table_name": table_name,
        "source_name": source_name,
        "total_rows": int(len(gdf)),
        "valid_rows": int(len(cleaned)),
        "repaired_rows": int(repaired_rows),
        "rejected_rows": int(rejected_rows),
        "duplicate_rows_removed": int(duplicate_rows_removed),
        "rejected_records": rejected_records,
    }
    return cleaned, summary