from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import json

import geopandas as gpd
import pandas as pd
from pyproj import CRS
from rapidfuzz import fuzz
from shapely.geometry import box


@dataclass(frozen=True)
class DataQualityConfig:
    report_file: str
    india_bounds: dict[str, float]
    duplicate_cluster_distance_meters: float
    duplicate_cluster_name_similarity: int
    duplicate_cluster_industry_similarity: int


def get_data_quality_config(config: dict[str, Any]) -> DataQualityConfig:
    section = config.get("data_quality", {})
    return DataQualityConfig(
        report_file=section.get("report_file", "exports/data_quality_report.json"),
        india_bounds=dict(section.get("india_bounds", {"min_longitude": 68.0, "max_longitude": 98.0, "min_latitude": 6.0, "max_latitude": 38.0})),
        duplicate_cluster_distance_meters=float(section.get("duplicate_cluster_distance_meters", 100)),
        duplicate_cluster_name_similarity=int(section.get("duplicate_cluster_name_similarity", 90)),
        duplicate_cluster_industry_similarity=int(section.get("duplicate_cluster_industry_similarity", 85)),
    )


def _safe_len(frame: pd.DataFrame | gpd.GeoDataFrame | None) -> int:
    return int(len(frame)) if frame is not None else 0


def _has_geometry(frame: gpd.GeoDataFrame | None) -> bool:
    if frame is None or frame.empty:
        return False
    return hasattr(frame, "geometry") and "geometry" in frame.columns


def _as_geoseries(frame: gpd.GeoDataFrame | None) -> gpd.GeoSeries:
    if frame is None or frame.empty:
        return gpd.GeoSeries([], crs="EPSG:4326")
    if frame.crs is None:
        return frame.set_crs("EPSG:4326").geometry
    return frame.to_crs("EPSG:4326").geometry


def _count_invalid_geometries(frame: gpd.GeoDataFrame | None) -> int:
    if not _has_geometry(frame):
        return 0
    return int(sum(geometry is None or geometry.is_empty or not geometry.is_valid for geometry in frame.geometry))


def _count_duplicate_rows(frame: gpd.GeoDataFrame | pd.DataFrame | None, subset: list[str]) -> int:
    if frame is None or frame.empty:
        return 0
    existing_subset = [column for column in subset if column in frame.columns]
    if not existing_subset:
        return 0
    return int(frame.duplicated(subset=existing_subset, keep="first").sum())


def _count_null_coordinates(frame: gpd.GeoDataFrame | None) -> int:
    if frame is None or frame.empty or "latitude" not in frame.columns or "longitude" not in frame.columns:
        return 0
    null_mask = frame["latitude"].isna() | frame["longitude"].isna()
    return int(null_mask.sum())


def _count_invalid_coordinates(frame: gpd.GeoDataFrame | None) -> int:
    if frame is None or frame.empty or "latitude" not in frame.columns or "longitude" not in frame.columns:
        return 0
    valid_mask = frame["latitude"].between(-90, 90) & frame["longitude"].between(-180, 180)
    return int((~valid_mask).sum())


def _count_outside_india(frame: gpd.GeoDataFrame | None, india_bounds: dict[str, float]) -> int:
    if not _has_geometry(frame):
        return 0

    bounds_polygon = box(
        float(india_bounds.get("min_longitude", 68.0)),
        float(india_bounds.get("min_latitude", 6.0)),
        float(india_bounds.get("max_longitude", 98.0)),
        float(india_bounds.get("max_latitude", 38.0)),
    )
    geometries = _as_geoseries(frame)
    return int(sum(geometry is None or geometry.is_empty or not geometry.intersects(bounds_polygon) for geometry in geometries))


def _geometry_within_bounds(frame: gpd.GeoDataFrame | None, india_bounds: dict[str, float]) -> bool:
    if not _has_geometry(frame):
        return True
    bounds_polygon = box(
        float(india_bounds.get("min_longitude", 68.0)),
        float(india_bounds.get("min_latitude", 6.0)),
        float(india_bounds.get("max_longitude", 98.0)),
        float(india_bounds.get("max_latitude", 38.0)),
    )
    geometries = _as_geoseries(frame)
    return bool(all(geometry is not None and not geometry.is_empty and geometry.intersects(bounds_polygon) for geometry in geometries))


def _count_suspicious_duplicate_clusters(frame: gpd.GeoDataFrame | None, distance_meters: float, name_similarity: int, industry_similarity: int) -> int:
    if frame is None or frame.empty:
        return 0
    if "normalized_name" not in frame.columns and "name" not in frame.columns:
        return 0

    working = frame.copy()
    if working.crs is None:
        working = working.set_crs("EPSG:4326")
    else:
        working = working.to_crs("EPSG:4326")

    metric_crs = working.estimate_utm_crs() or CRS.from_epsg(3857)
    projected = working.to_crs(metric_crs)
    suspicious_count = 0

    for index, row in working.iterrows():
        for other_index, other_row in working.loc[index + 1 :].iterrows():
            if row.geometry is None or other_row.geometry is None:
                continue

            row_name = str(row.get("normalized_name") or row.get("name") or "")
            other_name = str(other_row.get("normalized_name") or other_row.get("name") or "")
            row_industry = str(row.get("normalized_industrial_type") or row.get("industry_type") or "")
            other_industry = str(other_row.get("normalized_industrial_type") or other_row.get("industry_type") or "")

            name_score = fuzz.token_set_ratio(row_name, other_name)
            industry_score = fuzz.token_set_ratio(row_industry, other_industry)
            spatial_distance = float(projected.loc[index].geometry.distance(projected.loc[other_index].geometry))

            if spatial_distance <= distance_meters and name_score >= name_similarity and industry_score >= industry_similarity:
                suspicious_count += 1

    return suspicious_count


def _matching_rate(summary: dict[str, Any] | None) -> float:
    if not summary:
        return 0.0
    matched_records = summary.get("matched_records")
    total_records = summary.get("total_records")
    if not total_records:
        return 0.0
    return float(matched_records or 0) / float(total_records)


def _json_list_length(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, list):
        return len(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return len(parsed) if isinstance(parsed, list) else 0
        except Exception:
            return 0
    return 0


def _count_master_matched_source_records(frame: gpd.GeoDataFrame | None) -> int:
    if frame is None or frame.empty or "source_count" not in frame.columns:
        return 0
    return int(frame.loc[frame["source_count"] > 1, "source_count"].fillna(0).sum())


def _count_master_singleton_records(frame: gpd.GeoDataFrame | None, source_kind: str) -> int:
    if frame is None or frame.empty or "source_count" not in frame.columns:
        return 0
    if source_kind == "osm" and "osm_ids" in frame.columns and "government_ids" in frame.columns:
        mask = (frame["source_count"] == 1) & frame["osm_ids"].apply(_json_list_length).astype(int).gt(0) & frame["government_ids"].apply(_json_list_length).astype(int).eq(0)
        return int(mask.sum())
    if source_kind == "government" and "osm_ids" in frame.columns and "government_ids" in frame.columns:
        mask = (frame["source_count"] == 1) & frame["government_ids"].apply(_json_list_length).astype(int).gt(0) & frame["osm_ids"].apply(_json_list_length).astype(int).eq(0)
        return int(mask.sum())
    return int((frame["source_count"] == 1).sum())


def build_quality_report(
    *,
    pipeline_name: str,
    raw_frames: dict[str, pd.DataFrame | gpd.GeoDataFrame | None],
    cleaned_frames: dict[str, gpd.GeoDataFrame | None],
    summaries: dict[str, dict[str, Any] | None],
    config: dict[str, Any],
) -> dict[str, Any]:
    quality_config = get_data_quality_config(config)
    report_frames = {name: frame for name, frame in cleaned_frames.items() if frame is not None}
    total_records = sum(_safe_len(frame) for frame in report_frames.values())
    valid_records = sum(_safe_len(frame) for frame in report_frames.values())
    invalid_records = sum(_count_invalid_geometries(frame) for frame in report_frames.values())
    duplicate_rows = sum(_count_duplicate_rows(frame, ["source", "source_id", "osm_type", "osm_id", "site_id", "candidate_id"]) for frame in report_frames.values())
    null_coordinates = sum(_count_null_coordinates(frame) for frame in report_frames.values())
    invalid_coordinates = sum(_count_invalid_coordinates(frame) for frame in report_frames.values())
    outside_india = sum(_count_outside_india(frame, quality_config.india_bounds) for frame in report_frames.values() if isinstance(frame, gpd.GeoDataFrame))
    suspicious_duplicate_clusters = sum(
        _count_suspicious_duplicate_clusters(
            frame,
            quality_config.duplicate_cluster_distance_meters,
            quality_config.duplicate_cluster_name_similarity,
            quality_config.duplicate_cluster_industry_similarity,
        )
        for frame in report_frames.values()
        if isinstance(frame, gpd.GeoDataFrame)
    )

    state_assignment = 0
    district_assignment = 0
    industry_coverage = 0
    if "government" in report_frames and report_frames["government"] is not None and not report_frames["government"].empty:
        government_frame = report_frames["government"]
        state_assignment = int((government_frame.get("state").notna().sum()) if "state" in government_frame.columns else 0)
        district_assignment = int((government_frame.get("district").notna().sum()) if "district" in government_frame.columns else 0)
        industry_coverage = int((government_frame.get("industry_type").notna().sum()) if "industry_type" in government_frame.columns else 0)

    if "osm" in report_frames and report_frames["osm"] is not None and not report_frames["osm"].empty:
        osm_frame = report_frames["osm"]
        industry_coverage += int((osm_frame.get("industry_type").notna().sum()) if "industry_type" in osm_frame.columns else 0)

    confidence_distribution = Counter()
    if "matches" in report_frames and report_frames["matches"] is not None and not report_frames["matches"].empty and "match_confidence" in report_frames["matches"].columns:
        confidence_distribution.update(report_frames["matches"]["match_confidence"].fillna("missing").astype(str).tolist())
    if "master" in report_frames and report_frames["master"] is not None and not report_frames["master"].empty and "match_confidence" in report_frames["master"].columns:
        confidence_distribution.update(report_frames["master"]["match_confidence"].fillna("missing").astype(str).tolist())

    unmatched_osm_records = 0
    unmatched_government_records = 0
    matched_records = 0
    if "master" in report_frames and report_frames["master"] is not None and not report_frames["master"].empty:
        master_frame = report_frames["master"]
        matched_records = _count_master_matched_source_records(master_frame)
        unmatched_osm_records = _count_master_singleton_records(master_frame, "osm")
        unmatched_government_records = _count_master_singleton_records(master_frame, "government")
    elif summaries.get("matching"):
        matching_summary = summaries["matching"] or {}
        matched_records = int(matching_summary.get("candidate_matches", 0))
        if "osm_rows" in matching_summary:
            unmatched_osm_records = max(int(matching_summary.get("osm_rows", 0)) - matched_records, 0)
        if "government_rows" in matching_summary:
            unmatched_government_records = max(int(matching_summary.get("government_rows", 0)) - matched_records, 0)
    elif "osm" in summaries or "government" in summaries:
        osm_summary = summaries.get("osm") or {}
        gov_summary = summaries.get("government") or {}
        unmatched_osm_records = max(int(osm_summary.get("rejected_rows", 0)), 0)
        unmatched_government_records = max(int(gov_summary.get("rejected_rows", 0)), 0)

    report = {
        "pipeline_name": pipeline_name,
        "processing_timestamp": datetime.now(UTC).isoformat(),
        "total_records": int(total_records),
        "valid_records": int(valid_records),
        "invalid_records": int(invalid_records),
        "duplicates": int(duplicate_rows),
        "matched_records": int(matched_records),
        "unmatched_records": int(unmatched_osm_records + unmatched_government_records),
        "confidence_distribution": dict(confidence_distribution),
        "geometry_validity": {name: int(_count_invalid_geometries(frame)) for name, frame in report_frames.items()},
        "crs_consistency": {
            name: bool(frame is None or frame.empty or not _has_geometry(frame) or (frame.crs is not None and frame.crs.to_string() == "EPSG:4326"))
            for name, frame in report_frames.items()
        },
        "duplicate_rate": float(duplicate_rows / total_records) if total_records else 0.0,
        "null_coordinates": int(null_coordinates),
        "invalid_coordinates": int(invalid_coordinates),
        "state_assignment": int(state_assignment),
        "district_assignment": int(district_assignment),
        "industry_classification_coverage": int(industry_coverage),
        "osm_to_government_matching_rate": _matching_rate(summaries.get("matching")),
        "unmatched_osm_records": int(unmatched_osm_records),
        "unmatched_government_records": int(unmatched_government_records),
        "suspicious_duplicate_clusters": int(suspicious_duplicate_clusters),
        "impossible_coordinates": int(invalid_coordinates),
        "records_outside_india": int(outside_india),
        "source_summaries": summaries,
    }
    return report


def write_quality_report(report: dict[str, Any], report_file: str) -> Path:
    path = Path(report_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path
