"""Tests for Phase 11 — GeoJSON Export & Validation.

Coverage:
  - Valid FeatureCollection root structure
  - All required properties present in every feature:
      site_id, name, normalized_name, industry_type, state, district,
      address, status, osm_ids, government_ids, source_count, match_score,
      match_confidence, last_verified
  - Robust serialization of complex types:
      * UUID objects
      * datetime / date objects
      * Decimal numbers
      * Arrays / lists
      * Null / NaN values
      * Shapely Point / Polygon geometries
  - Validation engine (validate_master_geojson):
      * Passes on conforming files
      * Raises FileNotFoundError if file does not exist
      * Raises ValueError on missing site_ids or malformed JSON
"""
from __future__ import annotations

import json
from datetime import date, datetime, UTC
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import geopandas as gpd
import pytest
from shapely.geometry import Point, box

from src.export.geojson import (
    REQUIRED_PROPERTIES,
    _to_json_compatible,
    export_master_sites_geojson,
    validate_master_geojson,
)


def _sample_master_gdf() -> gpd.GeoDataFrame:
    records = [
        {
            "site_id": str(uuid4()),
            "name": "Tata Motors Facility",
            "normalized_name": "tata motors facility",
            "industry_type": "Automotive",
            "normalized_industry_type": "automotive",
            "state": "Uttar Pradesh",
            "district": "Lucknow",
            "address": "Chinhat Industrial Area",
            "status": "operational",
            "operational_status": "operational",
            "osm_ids": [1001, 1002],
            "government_ids": ["UP-DIC-999"],
            "matched_source_ids": {
                "all": ["osm:way:1001", "osm:way:1002", "government:cpcb:UP-DIC-999"],
                "osm": ["osm:way:1001", "osm:way:1002"],
                "government": ["government:cpcb:UP-DIC-999"],
            },
            "source_count": 3,
            "match_score": 0.95,
            "match_confidence": "automatic_match",
            "match_method": "name+spatial",
            "review_required": False,
            "last_verified": date(2026, 8, 15),
            "extraction_date": date(2026, 9, 4),
            "first_seen": date(2025, 1, 1),
            "last_seen": date(2026, 9, 4),
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "geometry": box(80.900, 26.850, 80.905, 26.855),
        },
        {
            "site_id": str(uuid4()),
            "name": "Solo Small Unit",
            "normalized_name": "solo small unit",
            "industry_type": "Textile",
            "normalized_industry_type": "textile",
            "state": "Uttar Pradesh",
            "district": "Kanpur Nagar",
            "address": None,
            "status": None,
            "operational_status": None,
            "osm_ids": [2001],
            "government_ids": [],
            "matched_source_ids": {"all": ["osm:node:2001"]},
            "source_count": 1,
            "match_score": 1.0,
            "match_confidence": "singleton",
            "match_method": "singleton",
            "review_required": False,
            "last_verified": None,
            "geometry": Point(80.350, 26.450),
        },
    ]
    return gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")


class TestSerializationHelpers:
    def test_handles_uuid(self):
        uid = uuid4()
        assert _to_json_compatible(uid) == str(uid)

    def test_handles_datetime_and_date(self):
        d = date(2026, 9, 4)
        dt = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
        assert _to_json_compatible(d) == "2026-09-04"
        assert "2026-09-04T12:00:00" in _to_json_compatible(dt)

    def test_handles_decimal(self):
        dec = Decimal("123.456")
        assert _to_json_compatible(dec) == 123.456

    def test_handles_nan_and_null(self):
        assert _to_json_compatible(float("nan")) is None
        assert _to_json_compatible(None) is None

    def test_handles_nested_lists_and_json_strings(self):
        json_arr_str = "[1, 2, 3]"
        assert _to_json_compatible(json_arr_str) == [1, 2, 3]


class TestExportMasterSitesGeojson:
    def test_produces_valid_feature_collection(self, tmp_path: Path):
        gdf = _sample_master_gdf()
        out_file = tmp_path / "data" / "processed" / "test_state" / "master_industrial_sites.geojson"

        written = export_master_sites_geojson(gdf, out_file, validate=True)
        assert written.is_file()

        with open(written, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 2

        feature0 = data["features"][0]
        assert feature0["type"] == "Feature"
        assert feature0["geometry"]["type"] == "Polygon"

        props = feature0["properties"]
        for req in REQUIRED_PROPERTIES:
            assert req in props, f"Missing required property {req}"

        assert isinstance(props["osm_ids"], list)
        assert isinstance(props["government_ids"], list)
        assert props["source_count"] == 3
        assert props["match_confidence"] == "automatic_match"

    def test_empty_geodataframe_produces_empty_feature_collection(self, tmp_path: Path):
        empty_gdf = gpd.GeoDataFrame(columns=["site_id", "geometry"], geometry="geometry")
        out_file = tmp_path / "empty.geojson"

        written = export_master_sites_geojson(empty_gdf, out_file, validate=True)
        assert written.is_file()

        with open(written, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["type"] == "FeatureCollection"
        assert data["features"] == []


class TestValidateMasterGeojson:
    def test_fails_if_file_does_not_exist(self, tmp_path: Path):
        non_existent = tmp_path / "ghost.geojson"
        with pytest.raises(FileNotFoundError):
            validate_master_geojson(non_existent)

    def test_fails_if_not_valid_json(self, tmp_path: Path):
        bad_file = tmp_path / "corrupt.geojson"
        bad_file.write_text("NOT A JSON {", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            validate_master_geojson(bad_file)

    def test_fails_if_missing_site_id(self, tmp_path: Path):
        bad_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": None,
                    "properties": {"name": "No Site ID"},
                }
            ],
        }
        bad_file = tmp_path / "missing_id.geojson"
        with open(bad_file, "w", encoding="utf-8") as f:
            json.dump(bad_fc, f)

        with pytest.raises(ValueError, match="missing required 'site_id'"):
            validate_master_geojson(bad_file)
