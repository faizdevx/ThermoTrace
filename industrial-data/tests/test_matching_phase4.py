from __future__ import annotations

import geopandas as gpd
from shapely.geometry import Point

from src.matching.config import get_matching_source_config, get_matching_threshold_config, get_matching_weight_config
from src.matching.resolution import build_master_sites, classify_match_confidence, generate_candidate_matches, normalize_source_records


def _matching_config() -> dict:
    return {
        "matching": {
            "source": {
                "max_spatial_distance_meters": 1000,
                "require_state_district_consistency": True,
                "candidate_name_similarity_min": 70,
                "candidate_address_similarity_min": 70,
                "candidate_industry_similarity_min": 60,
            },
            "weights": {"spatial": 0.4, "name": 0.3, "industry": 0.2, "address": 0.1},
            "thresholds": {"automatic_match": 0.85, "uncertain_match": 0.65},
            "output": {"master_table": "industrial_sites", "match_table": "industrial_entity_matches"},
        }
    }


def _osm_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "osm_id": 100,
                "osm_type": "way",
                "name": "Alpha Industrial Estate",
                "normalized_name": "alpha industrial estate",
                "industrial_type": "building=industrial",
                "normalized_industrial_type": "building industrial",
                "source_timestamp": "2026-01-01T00:00:00Z",
                "raw_tags": {"name": "Alpha Industrial Estate", "building": "industrial"},
                "state": "State X",
                "district_name": "District Y",
                "geometry": Point(77.2000, 28.5000),
            },
            {
                "osm_id": 101,
                "osm_type": "node",
                "name": "Orphan OSM Site",
                "normalized_name": "orphan osm site",
                "industrial_type": "landuse=industrial",
                "normalized_industrial_type": "landuse industrial",
                "source_timestamp": "2026-01-01T00:00:00Z",
                "raw_tags": {"name": "Orphan OSM Site", "landuse": "industrial"},
                "state": "State X",
                "district_name": "District Y",
                "geometry": Point(78.0000, 29.0000),
            },
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )


def _government_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "source_id": "G-1",
                "name": "Alpha Industrial Est.",
                "industry_type": "Factory",
                "address": "Industrial Area",
                "state": "State X",
                "district": "District Y",
                "source_date": "2026-01-02",
                "geometry": Point(77.2002, 28.5002),
            },
            {
                "source_id": "G-2",
                "name": "Remote Government Site",
                "industry_type": "Workshop",
                "address": "Remote Area",
                "state": "State X",
                "district": "District Y",
                "source_date": "2026-01-03",
                "geometry": Point(79.0000, 30.0000),
            },
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )


def test_classify_match_confidence_uses_configurable_thresholds():
    config = get_matching_threshold_config(_matching_config())
    assert classify_match_confidence(0.9, config) == "automatic_match"
    assert classify_match_confidence(0.7, config) == "uncertain_review"
    assert classify_match_confidence(0.2, config) == "non_match"


def test_generate_candidate_matches_uses_spatial_and_name_features():
    config = _matching_config()
    source_config = get_matching_source_config(config)
    weight_config = get_matching_weight_config(config)
    threshold_config = get_matching_threshold_config(config)
    records = normalize_source_records(_osm_frame(), _government_frame(), "government_industries_source_a")
    osm_records = [record for record in records if record.source_type == "osm"]
    government_records = [record for record in records if record.source_type == "government"]

    candidates = generate_candidate_matches(osm_records, government_records, source_config, weight_config, threshold_config)

    assert len(candidates) == 1
    assert candidates[0].match_confidence in {"automatic_match", "uncertain_review"}
    assert candidates[0].spatial_distance_meters >= 0
    assert "spatial" in candidates[0].match_method or "name" in candidates[0].match_method


def test_build_master_sites_preserves_unmatched_singletons_and_provenance():
    config = _matching_config()
    source_config = get_matching_source_config(config)
    weight_config = get_matching_weight_config(config)
    threshold_config = get_matching_threshold_config(config)

    master_gdf, matches_gdf = build_master_sites(
        _osm_frame(),
        _government_frame(),
        "government_industries_source_a",
        source_config,
        weight_config,
        threshold_config,
    )

    assert len(matches_gdf) == 1
    assert len(master_gdf) == 3
    assert "osm" in master_gdf.iloc[0]["matched_source_ids"]
    assert master_gdf.iloc[0]["site_id"]
    assert master_gdf.crs.to_string() == "EPSG:4326"
    assert any(row["source_count"] == 1 for _, row in master_gdf.iterrows())
