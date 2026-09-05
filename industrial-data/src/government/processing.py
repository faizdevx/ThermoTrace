from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import geopandas as gpd
import pandas as pd
from pyproj import CRS
from shapely.geometry import Point

try:
    from shapely import make_valid
except ImportError:  # pragma: no cover
    make_valid = None

from src.government.config import GovernmentSchemaConfig
from src.government.schema import DetectedCoordinateColumns, detect_coordinate_columns, standardize_column_names
from src.temporal import current_extraction_date


# Lazy-load cleaning pipeline to avoid circular imports and heavy boot cost
def _get_taxonomy():
    """Load the industry taxonomy (cached after first call)."""
    try:
        from src.cleaning.industry_taxonomy import load_taxonomy
        return load_taxonomy()
    except Exception:
        return None


def _normalize_name(name):
    """Normalize a facility name, returning None on failure."""
    try:
        from src.cleaning.names import normalize_facility_name
        return normalize_facility_name(name)
    except Exception:
        return None


def normalize_classification_value(value: Any, *, missing_values: list[str], unknown_values: list[str], not_applicable_values: list[str]) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    normalized = str(value).strip()
    if not normalized:
        return None

    lowered = normalized.lower()
    if lowered in {item.lower() for item in missing_values if item is not None}:
        return None
    if lowered in {item.lower() for item in unknown_values}:
        return "unknown"
    if lowered in {item.lower() for item in not_applicable_values}:
        return "not_applicable"
    return normalized


def _repair_geometry(geometry):
    if geometry is None or geometry.is_empty:
        return None, False
    if geometry.is_valid:
        return geometry, False
    if make_valid is not None:
        repaired = make_valid(geometry)
        if repaired is not None and not repaired.is_empty and repaired.is_valid:
            return repaired, True
    repaired = geometry.buffer(0)
    if repaired is not None and not repaired.is_empty and repaired.is_valid:
        return repaired, True
    return None, False


def _to_numeric_series(frame: pd.DataFrame, column_name: str | None) -> pd.Series | None:
    if not column_name:
        return None
    return pd.to_numeric(frame[column_name], errors="coerce")


def _coerce_schema_config(schema_config: GovernmentSchemaConfig | dict[str, Any]) -> GovernmentSchemaConfig:
    if isinstance(schema_config, GovernmentSchemaConfig):
        return schema_config

    coordinate_candidates = schema_config.get("coordinate_candidates", {})
    return GovernmentSchemaConfig(
        missing_values=list(schema_config.get("missing_values", [""])),
        unknown_values=list(schema_config.get("unknown_values", [])),
        not_applicable_values=list(schema_config.get("not_applicable_values", [])),
        coordinate_candidates={key: list(values) for key, values in coordinate_candidates.items()},
    )


def _build_geometry(
    frame: pd.DataFrame,
    coordinate_columns: DetectedCoordinateColumns,
    *,
    source_crs: str,
    output_crs: str,
) -> gpd.GeoSeries:
    latitude_series = _to_numeric_series(frame, coordinate_columns.latitude)
    longitude_series = _to_numeric_series(frame, coordinate_columns.longitude)

    if latitude_series is not None and longitude_series is not None:
        geometry = gpd.GeoSeries(
            [Point(longitude, latitude) if pd.notna(latitude) and pd.notna(longitude) else None for latitude, longitude in zip(latitude_series, longitude_series)],
            crs=source_crs,
        )
        return geometry.to_crs(output_crs)

    easting_series = _to_numeric_series(frame, coordinate_columns.easting)
    northing_series = _to_numeric_series(frame, coordinate_columns.northing)
    if easting_series is not None and northing_series is not None:
        geometry = gpd.GeoSeries(
            [Point(easting, northing) if pd.notna(easting) and pd.notna(northing) else None for easting, northing in zip(easting_series, northing_series)],
            crs=source_crs,
        )
        return geometry.to_crs(output_crs)

    raise ValueError("Latitude/longitude or easting/northing columns are required for government ingestion")


def _resolve_source_row_id(row: pd.Series, preferred_columns: list[str]) -> str | None:
    for column in preferred_columns:
        if column in row and pd.notna(row[column]):
            value = str(row[column]).strip()
            if value:
                return value
    return None


def clean_government_dataframe(
    frame: pd.DataFrame,
    *,
    source_name: str,
    source_table_name: str,
    source_date: str | None,
    source_id_column: str | None,
    name_column: str | None,
    industry_type_column: str | None,
    address_column: str | None,
    state_column: str | None,
    district_column: str | None,
    establishment_status_column: str | None,
    establishment_date_column: str | None,
    source_crs: str,
    output_crs: str,
    schema_config: GovernmentSchemaConfig,
    exact_duplicate_coordinate_precision: int,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    schema_config = _coerce_schema_config(schema_config)
    standardized = standardize_column_names(frame).reset_index(drop=True)
    schema_report = {
        "source_table_name": source_table_name,
        "row_count": int(len(standardized)),
        "columns": list(standardized.columns),
        "coordinate_columns": None,
    }

    coordinate_columns = detect_coordinate_columns(standardized, schema_config.coordinate_candidates)
    schema_report["coordinate_columns"] = coordinate_columns.__dict__

    geometry = _build_geometry(
        standardized,
        coordinate_columns,
        source_crs=source_crs,
        output_crs=output_crs,
    )

    taxonomy = _get_taxonomy()
    rows = []
    rejected_rows = 0
    repaired_rows = 0

    for position, (_, row) in enumerate(standardized.iterrows()):
        row_geometry = geometry.iloc[position]
        if row_geometry is None:
            rejected_rows += 1
            continue

        repaired_geometry, was_repaired = _repair_geometry(row_geometry)
        if repaired_geometry is None:
            rejected_rows += 1
            continue

        if was_repaired:
            repaired_rows += 1

        operational_status = normalize_classification_value(
            row.get("operational_status") or row.get("status") or row.get("facility_status"),
            missing_values=schema_config.missing_values,
            unknown_values=schema_config.unknown_values,
            not_applicable_values=schema_config.not_applicable_values,
        )
        extraction_date = current_extraction_date()

        raw_name = row.get(name_column) if name_column else None
        raw_industry_type = row.get(industry_type_column) if industry_type_column else None
        rows.append(
            {
                "source_id": _resolve_source_row_id(row, [source_id_column] if source_id_column else []),
                # Original values preserved — never overwritten
                "name": raw_name,
                "normalized_name": _normalize_name(raw_name),
                "industry_type": raw_industry_type,
                "normalized_industry_type": (
                    taxonomy.classify(raw_industry_type) if taxonomy else None
                ),
                "address": row.get(address_column) if address_column else None,
                "state": row.get(state_column) if state_column else None,
                "district": row.get(district_column) if district_column else None,
                "latitude": float(repaired_geometry.y) if repaired_geometry.geom_type == "Point" else None,
                "longitude": float(repaired_geometry.x) if repaired_geometry.geom_type == "Point" else None,
                "geometry": repaired_geometry,
                "establishment_status": normalize_classification_value(
                    row.get(establishment_status_column) if establishment_status_column else None,
                    missing_values=schema_config.missing_values,
                    unknown_values=schema_config.unknown_values,
                    not_applicable_values=schema_config.not_applicable_values,
                ),
                "establishment_date": pd.to_datetime(row.get(establishment_date_column), errors="coerce").date() if establishment_date_column and pd.notna(row.get(establishment_date_column)) else None,
                "extraction_date": extraction_date,
                "first_seen": extraction_date,
                "last_seen": extraction_date,
                "operational_status": operational_status,
                "source": source_name,
                "source_date": source_date,
                "raw_record": row.to_dict(),
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        )

    cleaned = gpd.GeoDataFrame(rows, geometry="geometry", crs=output_crs)
    cleaned["name"] = cleaned["name"].where(cleaned["name"].notna(), None)
    cleaned["industry_type"] = cleaned["industry_type"].where(cleaned["industry_type"].notna(), None)
    cleaned["address"] = cleaned["address"].where(cleaned["address"].notna(), None)
    cleaned["state"] = cleaned["state"].where(cleaned["state"].notna(), None)
    cleaned["district"] = cleaned["district"].where(cleaned["district"].notna(), None)

    cleaned["_coord_key"] = cleaned.apply(
        lambda row: (
            row["source_id"],
            round(float(row["latitude"]), exact_duplicate_coordinate_precision) if row["latitude"] is not None else None,
            round(float(row["longitude"]), exact_duplicate_coordinate_precision) if row["longitude"] is not None else None,
        ),
        axis=1,
    )
    duplicate_mask = cleaned.duplicated(subset=["source", "source_id", "_coord_key"], keep="first")
    duplicate_rows_removed = int(duplicate_mask.sum())
    cleaned = cleaned.loc[~duplicate_mask].drop(columns=["_coord_key"])

    summary = {
        "source_table_name": source_table_name,
        "total_rows": int(len(frame)),
        "valid_rows": int(len(cleaned)),
        "rejected_rows": int(rejected_rows),
        "repaired_rows": int(repaired_rows),
        "duplicate_rows_removed": int(duplicate_rows_removed),
        "schema_report": schema_report,
    }
    return cleaned, summary
