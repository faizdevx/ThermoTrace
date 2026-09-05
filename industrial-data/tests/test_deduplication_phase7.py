"""Tests for Phase 7 — duplicate handling at all three levels.

No network I/O, no PostGIS.  All tests use in-memory GeoDataFrames.

Coverage:
  Source-level dedup (dedup_osm_by_id, dedup_government_by_source_id, dedup_by_geometry)
  Geographic dedup (detect_geographic_duplicates with STRtree)
  STRtree-based _flag_probable_duplicates in osm/processing.py
  Explainability: removed_groups dict always populated
  Conservatism: similar-but-different features must NOT be merged
"""
from __future__ import annotations

from datetime import date

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from src.deduplication.source_dedup import (
    dedup_by_geometry,
    dedup_government_by_source_id,
    dedup_osm_by_id,
)
from src.deduplication.geographic_dedup import detect_geographic_duplicates
from src.osm.processing import normalize_osm_gdf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _osm_row(osm_id: int, osm_type: str = "way", name: str = "Factory",
             ind_type: str = "landuse=industrial", lat: float = 28.5, lon: float = 77.2,
             source: str = "openstreetmap") -> dict:
    return {
        "osm_id": osm_id, "osm_type": osm_type,
        "name": name, "normalized_name": name.lower(),
        "industrial_type": ind_type, "normalized_industrial_type": ind_type.lower(),
        "source": source, "source_url": "https://overpass-api.de",
        "source_timestamp": None, "raw_tags": {}, "district_name": "Test District",
        "district_source_id": "IND-TEST-001",
        "operational_status": None, "probable_duplicate": False, "duplicate_group_id": None,
        "geometry": Point(lon, lat),
        "extraction_date": date(2026, 9, 4),
        "first_seen": date(2026, 9, 4), "last_seen": date(2026, 9, 4),
    }


def _gov_row(source_id: str, name: str, lat: float, lon: float,
             ind_type: str = "Textile", source: str = "gov_test",
             district: str = "Test District") -> dict:
    return {
        "source_id": source_id, "name": name,
        "industry_type": ind_type, "normalized_industry_type": ind_type.lower(),
        "source": source, "district": district,
        "geometry": Point(lon, lat),
    }


def _osm_gdf(*rows) -> gpd.GeoDataFrame:
    if not rows:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
    return gpd.GeoDataFrame(list(rows), geometry="geometry", crs="EPSG:4326")


def _gov_gdf(*rows) -> gpd.GeoDataFrame:
    if not rows:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
    return gpd.GeoDataFrame(list(rows), geometry="geometry", crs="EPSG:4326")


# ---------------------------------------------------------------------------
# Source dedup — OSM
# ---------------------------------------------------------------------------

class TestDeduplicateOsmById:
    def test_removes_exact_duplicate_osm_id(self):
        gdf = _osm_gdf(
            _osm_row(1001, osm_type="way"),
            _osm_row(1001, osm_type="way"),   # duplicate
        )
        filtered, summary = dedup_osm_by_id(gdf)
        assert len(filtered) == 1
        assert summary.removed_count == 1

    def test_keeps_different_osm_types_same_id(self):
        """way:1001 and relation:1001 are different features."""
        gdf = _osm_gdf(
            _osm_row(1001, osm_type="way"),
            _osm_row(1001, osm_type="relation"),
        )
        filtered, summary = dedup_osm_by_id(gdf)
        assert len(filtered) == 2
        assert summary.removed_count == 0

    def test_cross_district_duplicate_removed(self):
        """Same osm_id appearing from two district queries → one removed."""
        row_a = _osm_row(9999, lat=28.5, lon=77.2)
        row_b = _osm_row(9999, lat=28.5001, lon=77.2001)  # slightly different coords
        gdf = _osm_gdf(row_a, row_b)
        filtered, summary = dedup_osm_by_id(gdf)
        assert len(filtered) == 1
        assert summary.removed_count == 1

    def test_summary_has_removed_groups(self):
        gdf = _osm_gdf(_osm_row(1), _osm_row(1))
        _, summary = dedup_osm_by_id(gdf)
        assert len(summary.removed_groups) == 1

    def test_empty_gdf_returns_empty(self):
        gdf = _osm_gdf()
        filtered, summary = dedup_osm_by_id(gdf)
        assert len(filtered) == 0
        assert summary.removed_count == 0


# ---------------------------------------------------------------------------
# Source dedup — Government
# ---------------------------------------------------------------------------

class TestDeduplicateGovernmentById:
    def test_removes_duplicate_source_id(self):
        gdf = _gov_gdf(
            _gov_row("R1", "Alpha Works", 28.5, 77.2),
            _gov_row("R1", "Alpha Works", 28.5, 77.2),  # duplicate
        )
        filtered, summary = dedup_government_by_source_id(gdf)
        assert len(filtered) == 1
        assert summary.removed_count == 1

    def test_different_source_ids_kept(self):
        gdf = _gov_gdf(
            _gov_row("R1", "Alpha Works", 28.5, 77.2),
            _gov_row("R2", "Beta Works", 28.6, 77.3),
        )
        filtered, summary = dedup_government_by_source_id(gdf)
        assert len(filtered) == 2
        assert summary.removed_count == 0

    def test_null_source_id_uses_geometry_fallback(self):
        gdf = _gov_gdf(
            {"source_id": None, "name": "X", "source": "gov", "geometry": Point(77.2, 28.5)},
            {"source_id": None, "name": "X", "source": "gov", "geometry": Point(77.2, 28.5)},
        )
        gdf = gpd.GeoDataFrame(
            [
                {"source_id": None, "name": "X", "source": "gov", "geometry": Point(77.2, 28.5)},
                {"source_id": None, "name": "X", "source": "gov", "geometry": Point(77.2, 28.5)},
            ],
            geometry="geometry", crs="EPSG:4326",
        )
        filtered, summary = dedup_government_by_source_id(gdf)
        assert len(filtered) == 1

    def test_to_dict_has_expected_keys(self):
        gdf = _gov_gdf(_gov_row("R1", "Alpha", 28.5, 77.2))
        _, summary = dedup_government_by_source_id(gdf)
        d = summary.to_dict()
        for key in ("strategy", "input_count", "output_count", "removed_count"):
            assert key in d


# ---------------------------------------------------------------------------
# Geographic dedup — STRtree
# ---------------------------------------------------------------------------

class TestGeographicDedup:
    def _make_gdf(self, rows) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")

    def test_no_duplicates_when_features_far_apart(self):
        gdf = self._make_gdf([
            {"normalized_name": "alpha factory", "normalized_industrial_type": "textile",
             "district": "A", "geometry": Point(77.0, 28.0)},
            {"normalized_name": "beta factory", "normalized_industrial_type": "textile",
             "district": "A", "geometry": Point(77.5, 28.5)},  # ~55 km away
        ])
        result = detect_geographic_duplicates(gdf, proximity_meters=200)
        assert result.removed_count == 0

    def test_removes_high_confidence_geographic_duplicate(self):
        """Two records at <50m with identical names → one removed."""
        gdf = self._make_gdf([
            {"normalized_name": "alpha factory", "normalized_industrial_type": "textile",
             "district": "A", "geometry": Point(77.20000, 28.50000)},
            {"normalized_name": "alpha factory", "normalized_industrial_type": "textile",
             "district": "A", "geometry": Point(77.20001, 28.50001)},  # ~15 m away
        ])
        result = detect_geographic_duplicates(
            gdf, proximity_meters=100, name_threshold=90.0
        )
        assert result.removed_count == 1
        assert len(result.filtered_gdf) == 1

    def test_does_not_merge_different_factories_close_together(self):
        """Different factories in the same industrial estate must NOT be merged."""
        gdf = self._make_gdf([
            {"normalized_name": "alpha textile mill", "normalized_industrial_type": "textile",
             "district": "A", "geometry": Point(77.2000, 28.5000)},
            {"normalized_name": "beta chemical plant", "normalized_industrial_type": "chemical",
             "district": "A", "geometry": Point(77.2001, 28.5001)},
        ])
        result = detect_geographic_duplicates(
            gdf, proximity_meters=200, name_threshold=90.0
        )
        # Different industry types → not merged regardless of proximity
        assert result.removed_count == 0

    def test_flags_uncertain_pairs_not_removes_them(self):
        """Name score 70-89: flag for review, do NOT remove."""
        gdf = self._make_gdf([
            {"normalized_name": "alpha ind works pvt ltd", "normalized_industrial_type": "textile",
             "district": "A", "geometry": Point(77.2000, 28.5000)},
            {"normalized_name": "alpha industrial pvt", "normalized_industrial_type": "textile",
             "district": "A", "geometry": Point(77.2001, 28.5001)},
        ])
        result = detect_geographic_duplicates(
            gdf, proximity_meters=200, name_threshold=95.0, uncertain_threshold=70.0
        )
        # Must not remove (score < 95), but may flag
        assert result.removed_count == 0
        assert len(result.filtered_gdf) == 2

    def test_different_district_never_merged(self):
        """Even identical names at <50m in different districts must not be merged."""
        gdf = self._make_gdf([
            {"normalized_name": "alpha factory", "normalized_industrial_type": "textile",
             "district": "North Goa", "geometry": Point(77.2000, 28.5000)},
            {"normalized_name": "alpha factory", "normalized_industrial_type": "textile",
             "district": "South Goa", "geometry": Point(77.2001, 28.5001)},
        ])
        result = detect_geographic_duplicates(
            gdf, proximity_meters=200, name_threshold=90.0
        )
        assert result.removed_count == 0

    def test_result_has_to_dict(self):
        gdf = self._make_gdf([
            {"normalized_name": "x", "normalized_industrial_type": "t",
             "district": "A", "geometry": Point(77.0, 28.0)},
        ])
        result = detect_geographic_duplicates(gdf, proximity_meters=100)
        d = result.to_dict()
        assert "removed_count" in d
        assert "flagged_candidate_count" in d


# ---------------------------------------------------------------------------
# OSM processing — STRtree flag_probable_duplicates
# ---------------------------------------------------------------------------

class TestFlagProbableDuplicatesSTRtree:
    """Verify the replacement STRtree-based _flag_probable_duplicates."""

    def _make_norm_gdf(self, rows) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")

    def test_no_flags_when_features_far_apart(self):
        gdf = self._make_norm_gdf([
            {"normalized_name": "alpha factory", "normalized_industrial_type": "textile",
             "probable_duplicate": False, "duplicate_group_id": None,
             "geometry": Point(77.0, 28.0)},
            {"normalized_name": "alpha factory", "normalized_industrial_type": "textile",
             "probable_duplicate": False, "duplicate_group_id": None,
             "geometry": Point(78.0, 29.0)},  # ~120 km
        ])
        from src.osm.processing import _flag_probable_duplicates
        count = _flag_probable_duplicates(
            gdf,
            probable_duplicate_distance_meters=100,
            probable_duplicate_name_similarity=80,
            probable_duplicate_industry_similarity=80,
        )
        assert count == 0
        assert gdf["probable_duplicate"].sum() == 0

    def test_flags_nearby_identical_features(self):
        gdf = self._make_norm_gdf([
            {"normalized_name": "alpha factory", "normalized_industrial_type": "textile",
             "probable_duplicate": False, "duplicate_group_id": None,
             "geometry": Point(77.2000, 28.5000)},
            {"normalized_name": "alpha factory", "normalized_industrial_type": "textile",
             "probable_duplicate": False, "duplicate_group_id": None,
             "geometry": Point(77.2001, 28.5001)},  # ~15 m
        ])
        from src.osm.processing import _flag_probable_duplicates
        count = _flag_probable_duplicates(
            gdf,
            probable_duplicate_distance_meters=100,
            probable_duplicate_name_similarity=80,
            probable_duplicate_industry_similarity=80,
        )
        assert count == 2
        assert gdf["probable_duplicate"].sum() == 2

    def test_does_not_flag_different_industry_types(self):
        gdf = self._make_norm_gdf([
            {"normalized_name": "works", "normalized_industrial_type": "textile",
             "probable_duplicate": False, "duplicate_group_id": None,
             "geometry": Point(77.2000, 28.5000)},
            {"normalized_name": "works", "normalized_industrial_type": "chemical",
             "probable_duplicate": False, "duplicate_group_id": None,
             "geometry": Point(77.2001, 28.5001)},
        ])
        from src.osm.processing import _flag_probable_duplicates
        count = _flag_probable_duplicates(
            gdf,
            probable_duplicate_distance_meters=100,
            probable_duplicate_name_similarity=80,
            probable_duplicate_industry_similarity=95,
        )
        assert count == 0

    def test_every_feature_gets_a_duplicate_group_id(self):
        gdf = self._make_norm_gdf([
            {"normalized_name": "alpha", "normalized_industrial_type": "t",
             "probable_duplicate": False, "duplicate_group_id": None,
             "geometry": Point(77.0, 28.0)},
            {"normalized_name": "beta", "normalized_industrial_type": "t",
             "probable_duplicate": False, "duplicate_group_id": None,
             "geometry": Point(78.0, 29.0)},
        ])
        from src.osm.processing import _flag_probable_duplicates
        _flag_probable_duplicates(gdf, probable_duplicate_distance_meters=100,
                                  probable_duplicate_name_similarity=80,
                                  probable_duplicate_industry_similarity=80)
        assert gdf["duplicate_group_id"].notna().all()
