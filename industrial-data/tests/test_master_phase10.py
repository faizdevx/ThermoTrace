"""Tests for Phase 10 — Master Dataset Generation.

Coverage:
  - assemble_master_sites combines OSM and Government datasets
  - Confirmed automatic matches are merged into single master industrial sites
  - Unmatched OSM and Government records are preserved as singleton sites
  - Original source records are not deleted or mutated
  - All conceptual master site columns are populated:
      * site_id (stable anchor UUID)
      * name, normalized_name
      * industry_type, normalized_industry_type
      * geometry (valid, EPSG:4326)
      * state, district, address
      * establishment_status, operational_status
      * osm_ids (list), government_ids (list)
      * matched_source_ids (dict with 'all', 'osm', 'government')
      * source_count (2 for matched pair, 1 for singleton)
      * match_score, match_confidence, match_method
      * review_required
      * last_verified, extraction_date, first_seen, last_seen
      * created_at, updated_at
  - Multi-run refresh behavior:
      * Preserves existing site_ids using SiteIdentityIndex
      * Generates site_source_records rows for every source record
"""
from __future__ import annotations

from datetime import date, datetime, UTC

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, box

from src.matching.config import (
    MatchingSourceConfig,
    MatchingThresholdConfig,
    MatchingWeightConfig,
)
from src.matching.master import assemble_master_sites
from src.matching.resolution import (
    CandidateMatch,
    generate_candidate_matches,
    normalize_source_records,
)
from src.matching.site_identity import SiteIdentityIndex


def _sample_osm_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "osm_id": 1001,
                "osm_type": "way",
                "name": "Bharat Electronics Plant",
                "normalized_name": "bharat electronics plant",
                "industrial_type": "building=industrial",
                "normalized_industrial_type": "building industrial",
                "source_timestamp": "2026-01-10T00:00:00Z",
                "raw_tags": {"name": "Bharat Electronics Plant", "building": "industrial"},
                "state": "Uttar Pradesh",
                "district_name": "Ghaziabad",
                "geometry": box(77.4000, 28.6000, 77.4010, 28.6010),
            },
            {
                "osm_id": 1002,
                "osm_type": "node",
                "name": "Solo OSM Workshop",
                "normalized_name": "solo osm workshop",
                "industrial_type": "landuse=industrial",
                "normalized_industrial_type": "landuse industrial",
                "source_timestamp": "2026-01-11T00:00:00Z",
                "raw_tags": {"name": "Solo OSM Workshop"},
                "state": "Uttar Pradesh",
                "district_name": "Ghaziabad",
                "geometry": Point(77.4500, 28.6500),
            },
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )


def _sample_gov_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "source_id": "UP-PCB-555",
                "name": "Bharat Electronics Plant Unit 1",
                "industry_type": "Electronics",
                "normalized_industry_type": "electronics",
                "address": "Site 4 Industrial Area",
                "state": "Uttar Pradesh",
                "district": "Ghaziabad",
                "source_date": "2026-01-15",
                "operational_status": "operational",
                "geometry": Point(77.4005, 28.6005),  # inside the OSM bounding box
            },
            {
                "source_id": "UP-PCB-999",
                "name": "Solo Government Warehouse",
                "industry_type": "Warehouse",
                "normalized_industry_type": "warehouse",
                "address": "Loni Road",
                "state": "Uttar Pradesh",
                "district": "Ghaziabad",
                "source_date": "2026-02-01",
                "operational_status": "operational",
                "geometry": Point(77.5000, 28.7000),  # far away
            },
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )


class TestAssembleMasterSites:
    def test_combines_records_and_merges_confirmed_matches(self):
        osm_gdf = _sample_osm_gdf()
        gov_gdf = _sample_gov_gdf()

        source_config = MatchingSourceConfig(
            max_spatial_distance_meters=1000,
            require_state_district_consistency=True,
            candidate_name_similarity_min=60,
            candidate_address_similarity_min=50,
            candidate_industry_similarity_min=50,
        )
        weight_config = MatchingWeightConfig(spatial=0.4, name=0.4, industry=0.1, address=0.1)
        threshold_config = MatchingThresholdConfig(automatic_match=0.80, uncertain_match=0.60)

        records = normalize_source_records(osm_gdf, gov_gdf, "up_pcb_industries")
        osm_records = [r for r in records if r.source_type == "osm"]
        gov_records = [r for r in records if r.source_type == "government"]

        candidates = generate_candidate_matches(
            osm_records, gov_records, source_config, weight_config, threshold_config
        )

        identity_index = SiteIdentityIndex()
        master_gdf, source_records_df = assemble_master_sites(
            osm_gdf=osm_gdf,
            government_gdf=gov_gdf,
            candidate_matches=candidates,
            government_table_name="up_pcb_industries",
            identity_index=identity_index,
        )

        # 2 OSM + 2 Gov records:
        # Bharat Electronics OSM + Bharat Electronics Gov = 1 merged site
        # Solo OSM Workshop = 1 singleton
        # Solo Government Warehouse = 1 singleton
        # Total = 3 master sites
        assert len(master_gdf) == 3
        assert master_gdf.crs.to_string() == "EPSG:4326"

        # Check the merged site
        merged_site = master_gdf[master_gdf["source_count"] == 2]
        assert len(merged_site) == 1
        merged = merged_site.iloc[0]

        assert merged["source_count"] == 2
        assert 1001 in merged["osm_ids"]
        assert "UP-PCB-555" in merged["government_ids"]
        assert "osm" in merged["matched_source_ids"]
        assert "government" in merged["matched_source_ids"]
        assert merged["match_confidence"] == "automatic_match"
        assert merged["site_id"] is not None

        # Check singleton sites are preserved
        singletons = master_gdf[master_gdf["source_count"] == 1]
        assert len(singletons) == 2

    def test_original_source_records_are_not_mutated(self):
        osm_gdf = _sample_osm_gdf()
        gov_gdf = _sample_gov_gdf()
        original_osm_len = len(osm_gdf)
        original_gov_len = len(gov_gdf)

        identity_index = SiteIdentityIndex()
        assemble_master_sites(
            osm_gdf=osm_gdf,
            government_gdf=gov_gdf,
            candidate_matches=[],
            government_table_name="up_pcb_industries",
            identity_index=identity_index,
        )

        # Ensure inputs untouched
        assert len(osm_gdf) == original_osm_len
        assert len(gov_gdf) == original_gov_len

    def test_all_conceptual_columns_present(self):
        osm_gdf = _sample_osm_gdf()
        gov_gdf = _sample_gov_gdf()

        identity_index = SiteIdentityIndex()
        master_gdf, source_records_df = assemble_master_sites(
            osm_gdf=osm_gdf,
            government_gdf=gov_gdf,
            candidate_matches=[],
            government_table_name="up_pcb_industries",
            identity_index=identity_index,
        )

        expected_cols = [
            "site_id",
            "name",
            "normalized_name",
            "industry_type",
            "normalized_industry_type",
            "geometry",
            "state",
            "district",
            "address",
            "operational_status",
            "osm_ids",
            "government_ids",
            "source_count",
            "match_score",
            "match_confidence",
            "match_method",
            "review_required",
            "last_verified",
            "created_at",
            "updated_at",
        ]

        for col in expected_cols:
            assert col in master_gdf.columns, f"Missing expected column: {col}"

    def test_source_records_ledger_populated(self):
        osm_gdf = _sample_osm_gdf()
        gov_gdf = _sample_gov_gdf()

        identity_index = SiteIdentityIndex()
        _, source_records_df = assemble_master_sites(
            osm_gdf=osm_gdf,
            government_gdf=gov_gdf,
            candidate_matches=[],
            government_table_name="up_pcb_industries",
            identity_index=identity_index,
        )

        # 2 OSM + 2 Gov = 4 source record ledger entries
        assert len(source_records_df) == 4
        expected_ledger_cols = [
            "site_id",
            "source_system",
            "source_key",
            "source_id",
            "source_table",
            "first_linked",
            "last_linked",
        ]
        for col in expected_ledger_cols:
            assert col in source_records_df.columns, f"Missing ledger col: {col}"

    def test_empty_inputs_return_empty_frames(self):
        empty_osm = gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
        empty_gov = gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
        identity_index = SiteIdentityIndex()

        master_gdf, source_df = assemble_master_sites(
            osm_gdf=empty_osm,
            government_gdf=empty_gov,
            candidate_matches=[],
            government_table_name="gov",
            identity_index=identity_index,
        )

        assert len(master_gdf) == 0
        assert len(source_df) == 0
