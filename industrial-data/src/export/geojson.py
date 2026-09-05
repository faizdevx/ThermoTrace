"""Master industrial site GeoJSON export and validation.

Implements Phase 11 — GeoJSON Export:
1. Produces a standard, valid GeoJSON FeatureCollection.
2. Formats all required feature properties:
     - site_id (UUID string)
     - name, normalized_name
     - industry_type
     - state, district, address
     - status
     - osm_ids (list)
     - government_ids (list)
     - source_count (int)
     - match_score (float)
     - match_confidence (str)
     - last_verified (ISO date or null)
3. Robust serialization for:
     - UUIDs
     - datetime / date objects
     - Decimal numbers
     - Arrays, lists, and JSON strings
     - Null / NaN / NaT values (cleanly mapped to JSON null)
     - Shapely / PostGIS geometries (converted to standard GeoJSON geometry dicts)
4. Comprehensive post-write validation ensuring file exists, is valid JSON,
   and meets the GeoJSON RFC 7946 FeatureCollection specification.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

logger = logging.getLogger(__name__)

# Expected conceptual property names according to Phase 11 specification
REQUIRED_PROPERTIES = [
    "site_id",
    "name",
    "normalized_name",
    "industry_type",
    "state",
    "district",
    "address",
    "status",
    "osm_ids",
    "government_ids",
    "source_count",
    "match_score",
    "match_confidence",
    "last_verified",
]


def _to_json_compatible(val: Any) -> Any:
    """Recursively convert a Python object into standard JSON-serializable types."""
    if val is None:
        return None
    if isinstance(val, (float, np.floating)):
        return None if (np.isnan(val) or np.isinf(val)) else float(val)
    if isinstance(val, (int, np.integer)):
        return int(val)
    if isinstance(val, (bool, np.bool_)):
        return bool(val)
    if isinstance(val, (UUID, Path)):
        return str(val)
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    if pd.isna(val):
        return None
    if isinstance(val, (list, tuple, set)):
        return [_to_json_compatible(item) for item in val]
    if isinstance(val, dict):
        return {str(k): _to_json_compatible(v) for k, v in val.items()}
    if isinstance(val, str):
        # Attempt parsing if it looks like a JSON string list/dict
        stripped = val.strip()
        if (stripped.startswith("[") and stripped.endswith("]")) or (
            stripped.startswith("{") and stripped.endswith("}")
        ):
            try:
                parsed = json.loads(stripped)
                return _to_json_compatible(parsed)
            except Exception:
                pass
        return val
    return str(val)


def _extract_feature_properties(row: pd.Series | dict[str, Any]) -> dict[str, Any]:
    """Extract and normalize all required properties for a master site feature."""
    site_id = _to_json_compatible(row.get("site_id"))
    name = _to_json_compatible(row.get("name"))
    normalized_name = _to_json_compatible(row.get("normalized_name"))
    industry_type = _to_json_compatible(
        row.get("industry_type") or row.get("normalized_industry_type")
    )
    state = _to_json_compatible(row.get("state"))
    district = _to_json_compatible(row.get("district"))
    address = _to_json_compatible(row.get("address"))

    # status: prefer operational_status, then establishment_status, else fallback
    status = (
        row.get("status")
        or row.get("operational_status")
        or row.get("establishment_status")
    )
    status = _to_json_compatible(status)

    # osm_ids & government_ids must be lists
    osm_ids = _to_json_compatible(row.get("osm_ids"))
    if osm_ids is None:
        osm_ids = []
    elif not isinstance(osm_ids, list):
        osm_ids = [osm_ids]

    gov_ids = _to_json_compatible(row.get("government_ids"))
    if gov_ids is None:
        gov_ids = []
    elif not isinstance(gov_ids, list):
        gov_ids = [gov_ids]

    source_count = _to_json_compatible(row.get("source_count"))
    if source_count is None:
        source_count = len(osm_ids) + len(gov_ids)

    match_score = _to_json_compatible(row.get("match_score"))
    if match_score is None:
        match_score = 1.0

    match_confidence = _to_json_compatible(row.get("match_confidence"))
    if match_confidence is None:
        match_confidence = "singleton" if source_count == 1 else "automatic_match"

    last_verified = _to_json_compatible(
        row.get("last_verified")
        or row.get("last_seen")
        or row.get("extraction_date")
    )

    props: dict[str, Any] = {
        "site_id": site_id,
        "name": name,
        "normalized_name": normalized_name,
        "industry_type": industry_type,
        "state": state,
        "district": district,
        "address": address,
        "status": status,
        "osm_ids": osm_ids,
        "government_ids": gov_ids,
        "source_count": source_count,
        "match_score": match_score,
        "match_confidence": match_confidence,
        "last_verified": last_verified,
    }

    # Optional extra provenance fields if present
    if "matched_source_ids" in row:
        props["matched_source_ids"] = _to_json_compatible(row.get("matched_source_ids"))
    if "review_required" in row:
        props["review_required"] = _to_json_compatible(row.get("review_required"))

    return props


def export_master_sites_geojson(
    master_gdf: gpd.GeoDataFrame,
    output_path: str | Path,
    *,
    validate: bool = True,
    indent: int | None = 2,
) -> Path:
    """Export the master industrial dataset to a standard GeoJSON FeatureCollection.

    Parameters
    ----------
    master_gdf : GeoDataFrame
        The master industrial_sites GeoDataFrame.
    output_path : str or Path
        Destination path for the GeoJSON file.
    validate : bool, default True
        Whether to run post-export validation before returning.
    indent : int or None, default 2
        JSON indentation formatting.

    Returns
    -------
    Path
        Absolute Path to the written and validated GeoJSON file.

    Raises
    ------
    ValueError
        If validation is enabled and the generated file fails GeoJSON checks.
    """
    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    features: list[dict[str, Any]] = []

    if master_gdf is not None and not master_gdf.empty:
        for _, row in master_gdf.iterrows():
            geom = row.get("geometry")
            geom_dict: dict[str, Any] | None = None
            if geom is not None and isinstance(geom, BaseGeometry) and not geom.is_empty:
                geom_dict = mapping(geom)

            properties = _extract_feature_properties(row)

            feature: dict[str, Any] = {
                "type": "Feature",
                "geometry": geom_dict,
                "properties": properties,
            }
            features.append(feature)

    feature_collection: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(feature_collection, f, indent=indent, ensure_ascii=False)

    logger.info(
        "Exported %d master industrial sites to GeoJSON: %s",
        len(features),
        out_file,
    )

    if validate:
        validate_master_geojson(out_file)

    return out_file


def validate_master_geojson(path_or_dict: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Validate that a GeoJSON file or dict conforms to FeatureCollection standards.

    Parameters
    ----------
    path_or_dict : str, Path, or dict
        Path to file on disk or already-parsed JSON dict.

    Returns
    -------
    dict
        Validation report dictionary with feature count and property summary.

    Raises
    ------
    FileNotFoundError
        If file does not exist on disk.
    ValueError
        If GeoJSON schema or required properties are invalid.
    """
    if isinstance(path_or_dict, (str, Path)):
        p = Path(path_or_dict)
        if not p.is_file():
            raise FileNotFoundError(f"GeoJSON export file does not exist: {p}")
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            raise ValueError(f"GeoJSON file is not valid JSON: {exc}") from exc
    elif isinstance(path_or_dict, dict):
        data = path_or_dict
    else:
        raise TypeError("path_or_dict must be str, Path, or dict")

    # 1. Root structure checks
    if not isinstance(data, dict):
        raise ValueError("GeoJSON root must be an object (dictionary)")

    root_type = data.get("type")
    if root_type != "FeatureCollection":
        raise ValueError(
            f"Expected root type 'FeatureCollection', got '{root_type}'"
        )

    features = data.get("features")
    if not isinstance(features, list):
        raise ValueError("FeatureCollection 'features' must be a list")

    # 2. Individual feature checks
    valid_geometries = 0
    missing_site_ids = 0
    prop_coverage: dict[str, int] = {k: 0 for k in REQUIRED_PROPERTIES}

    for i, feature in enumerate(features):
        if not isinstance(feature, dict):
            raise ValueError(f"Feature at index {i} must be an object")

        if feature.get("type") != "Feature":
            raise ValueError(f"Feature at index {i} must have type='Feature'")

        geom = feature.get("geometry")
        if geom is not None:
            if not isinstance(geom, dict):
                raise ValueError(
                    f"Feature at index {i} geometry must be a dict or null"
                )
            if "type" not in geom or "coordinates" not in geom:
                raise ValueError(
                    f"Feature at index {i} geometry missing 'type' or 'coordinates'"
                )
            valid_geometries += 1

        props = feature.get("properties")
        if not isinstance(props, dict):
            raise ValueError(f"Feature at index {i} properties must be an object")

        for key in REQUIRED_PROPERTIES:
            if key in props:
                prop_coverage[key] += 1

        if not props.get("site_id"):
            missing_site_ids += 1

    if missing_site_ids > 0:
        raise ValueError(
            f"Validation failed: {missing_site_ids} feature(s) missing required 'site_id'"
        )

    return {
        "valid": True,
        "feature_count": len(features),
        "valid_geometry_count": valid_geometries,
        "missing_site_ids": missing_site_ids,
        "property_coverage": prop_coverage,
    }
