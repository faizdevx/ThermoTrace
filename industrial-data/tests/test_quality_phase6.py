from __future__ import annotations

import json

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon

from src.data_quality.reporting import build_quality_report, get_data_quality_config, write_quality_report


def _quality_config() -> dict:
    return {
        "data_quality": {
            "report_file": "exports/data_quality_report.json",
            "india_bounds": {"min_longitude": 68.0, "max_longitude": 98.0, "min_latitude": 6.0, "max_latitude": 38.0},
            "duplicate_cluster_distance_meters": 100,
            "duplicate_cluster_name_similarity": 90,
            "duplicate_cluster_industry_similarity": 85,
        }
    }


def test_build_quality_report_counts_geometry_and_coordinate_issues(tmp_path):
    config = _quality_config()
    gdf = gpd.GeoDataFrame(
        [
            {"source": "test", "source_id": "1", "name": "Alpha", "geometry": Point(77.2, 28.5), "latitude": 28.5, "longitude": 77.2},
            {"source": "test", "source_id": "1", "name": "Alpha", "geometry": Point(77.2, 28.5), "latitude": 28.5, "longitude": 77.2},
            {"source": "test", "source_id": "2", "name": "Outside", "geometry": Point(10.0, 60.0), "latitude": 60.0, "longitude": 10.0},
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )

    report = build_quality_report(
        pipeline_name="boundary",
        raw_frames={"boundary_raw": gdf},
        cleaned_frames={"boundary": gdf},
        summaries={"boundary": {"total_rows": 3, "matched_records": 0}},
        config=config,
    )

    assert report["total_records"] == 3
    assert report["duplicates"] == 1
    assert report["records_outside_india"] == 1
    assert report["invalid_coordinates"] == 1
    assert report["null_coordinates"] == 0
    assert report["crs_consistency"]["boundary"] is True

    report_path = write_quality_report(report, str(tmp_path / "data_quality_report.json"))
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["pipeline_name"] == "boundary"


def test_build_quality_report_uses_master_table_for_matching_counts():
    config = _quality_config()
    master = gpd.GeoDataFrame(
        [
            {
                "site_id": "site-1",
                "name": "Alpha Industrial Estate",
                "normalized_name": "alpha industrial estate",
                "industry_type": "building=industrial",
                "geometry": Point(77.2, 28.5),
                "state": "State X",
                "district": "District Y",
                "address": "Industrial Area",
                "establishment_status": None,
                "establishment_date": None,
                "osm_ids": [10],
                "government_ids": ["G-1"],
                "matched_source_ids": {"all": ["osm:way:10", "government:government_industries_source_a:G-1"]},
                "source_count": 2,
                "source_confidence": 0.92,
                "match_score": 0.91,
                "match_method": "spatial+name",
                "match_confidence": "automatic_match",
                "review_required": False,
                "last_verified": pd.Timestamp("2026-09-03").date(),
                "created_at": pd.Timestamp("2026-09-03T00:00:00Z"),
                "updated_at": pd.Timestamp("2026-09-03T00:00:00Z"),
            },
            {
                "site_id": "site-2",
                "name": "Unmatched OSM",
                "normalized_name": "unmatched osm",
                "industry_type": "landuse=industrial",
                "geometry": Point(77.3, 28.6),
                "state": "State X",
                "district": "District Y",
                "address": None,
                "establishment_status": None,
                "establishment_date": None,
                "osm_ids": [11],
                "government_ids": [],
                "matched_source_ids": {"all": ["osm:node:11"]},
                "source_count": 1,
                "source_confidence": 1.0,
                "match_score": 1.0,
                "match_method": "singleton",
                "match_confidence": "singleton",
                "review_required": False,
                "last_verified": pd.Timestamp("2026-09-03").date(),
                "created_at": pd.Timestamp("2026-09-03T00:00:00Z"),
                "updated_at": pd.Timestamp("2026-09-03T00:00:00Z"),
            },
            {
                "site_id": "site-3",
                "name": "Unmatched Government",
                "normalized_name": "unmatched government",
                "industry_type": "factory",
                "geometry": Point(77.4, 28.7),
                "state": "State X",
                "district": "District Y",
                "address": None,
                "establishment_status": None,
                "establishment_date": None,
                "osm_ids": [],
                "government_ids": ["G-2"],
                "matched_source_ids": {"all": ["government:government_industries_source_a:G-2"]},
                "source_count": 1,
                "source_confidence": 1.0,
                "match_score": 1.0,
                "match_method": "singleton",
                "match_confidence": "singleton",
                "review_required": False,
                "last_verified": pd.Timestamp("2026-09-03").date(),
                "created_at": pd.Timestamp("2026-09-03T00:00:00Z"),
                "updated_at": pd.Timestamp("2026-09-03T00:00:00Z"),
            },
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )

    matches = gpd.GeoDataFrame(
        [
            {
                "candidate_id": "candidate-1",
                "site_id": "site-1",
                "osm_source_key": "osm:way:10",
                "government_source_key": "government:government_industries_source_a:G-1",
                "osm_id": 10,
                "government_source_id": "G-1",
                "government_table": "government_industries_source_a",
                "match_score": 0.91,
                "match_confidence": "automatic_match",
                "match_method": "spatial+name",
                "review_required": False,
                "matched_source_ids": {"all": ["osm:way:10", "government:government_industries_source_a:G-1"]},
                "spatial_distance_meters": 22.0,
                "name_score": 0.93,
                "industry_score": 0.88,
                "address_score": 0.67,
                "state_consistent": True,
                "district_consistent": True,
                "created_at": pd.Timestamp("2026-09-03T00:00:00Z"),
                "updated_at": pd.Timestamp("2026-09-03T00:00:00Z"),
            }
        ]
    )

    report = build_quality_report(
        pipeline_name="matching",
        raw_frames={"osm_raw": None, "government_raw": None},
        cleaned_frames={"master": master, "matches": matches},
        summaries={"matching": {"osm_rows": 2, "government_rows": 2, "candidate_matches": 1, "matched_records": 2, "total_records": 4}},
        config=config,
    )

    assert report["matched_records"] == 2
    assert report["unmatched_osm_records"] == 1
    assert report["unmatched_government_records"] == 1
    assert report["confidence_distribution"]["automatic_match"] == 2
