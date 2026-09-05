"""Tests for Phase 4 — refresh-aware OSM pipeline behaviour.

All tests are pure Python — no Overpass queries, no PostGIS.

Coverage:
  * detect_osm_changes() correctly classifies new / updated / unchanged / removed
  * osm_version is used for update detection when available
  * source_timestamp is used as fallback when version is absent
  * build_refresh_merge() preserves first_seen for existing features
  * build_refresh_merge() advances last_seen for seen features
  * build_refresh_merge() marks removed features without deleting them
  * Repeated pipeline runs do not duplicate features (idempotency)
  * OsmChangeReport serialises correctly to dict / str
  * overpass_elements_to_geodataframe() captures osm_version and osm_changeset
"""
from __future__ import annotations

from datetime import date, timedelta

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from src.osm.overpass import overpass_elements_to_geodataframe
from src.osm.refresh import (
    OsmChangeReport,
    RefreshMergeResult,
    build_refresh_merge,
    detect_osm_changes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TODAY = date(2026, 9, 4)
_YESTERDAY = _TODAY - timedelta(days=1)
_LAST_WEEK = _TODAY - timedelta(days=7)


def _make_osm_row(
    osm_id: int,
    osm_type: str = "way",
    version: int | None = None,
    timestamp: str | None = None,
    first_seen: date | None = None,
    last_seen: date | None = None,
    operational_status: str | None = None,
    geom: Point | Polygon | None = None,
) -> dict:
    return {
        "osm_id": osm_id,
        "osm_type": osm_type,
        "osm_version": version,
        "osm_changeset": None,
        "source_timestamp": timestamp,
        "name": f"Feature {osm_id}",
        "normalized_name": f"feature {osm_id}",
        "industrial_type": "landuse=industrial",
        "normalized_industrial_type": "landuse industrial",
        "source": "openstreetmap",
        "first_seen": first_seen or _LAST_WEEK,
        "last_seen": last_seen or _YESTERDAY,
        "extraction_date": last_seen or _YESTERDAY,
        "operational_status": operational_status,
        "raw_tags": {},
        "district_name": "Test District",
        "district_source_id": "IND-TEST-001",
        "geometry": geom or Point(77.2 + osm_id * 0.01, 28.5),
    }


def _osm_gdf(*rows: dict) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(list(rows), geometry="geometry", crs="EPSG:4326")


# ---------------------------------------------------------------------------
# detect_osm_changes — basic classification
# ---------------------------------------------------------------------------

class TestDetectOsmChanges:
    def test_all_new_when_no_existing(self):
        new = _osm_gdf(
            _make_osm_row(1, version=1),
            _make_osm_row(2, version=1),
        )
        report = detect_osm_changes(None, new, extraction_date=_TODAY)
        assert report.new_features == 2
        assert report.total_existing == 0
        assert report.updated_features == 0
        assert report.unchanged_features == 0
        assert report.removed_features == 0

    def test_all_removed_when_new_is_empty(self):
        existing = _osm_gdf(
            _make_osm_row(1), _make_osm_row(2)
        )
        new = _osm_gdf()
        report = detect_osm_changes(existing, new, extraction_date=_TODAY)
        assert report.removed_features == 2
        assert report.new_features == 0

    def test_identifies_new_features(self):
        existing = _osm_gdf(_make_osm_row(1, version=1))
        new = _osm_gdf(
            _make_osm_row(1, version=1),
            _make_osm_row(2, version=1),  # new
        )
        report = detect_osm_changes(existing, new, extraction_date=_TODAY)
        assert report.new_features == 1
        assert report.removed_features == 0

    def test_identifies_removed_features(self):
        existing = _osm_gdf(
            _make_osm_row(1, version=1),
            _make_osm_row(2, version=1),
        )
        new = _osm_gdf(_make_osm_row(1, version=1))  # 2 is gone
        report = detect_osm_changes(existing, new, extraction_date=_TODAY)
        assert report.removed_features == 1

    def test_detects_version_update(self):
        existing = _osm_gdf(_make_osm_row(1, version=3))
        new = _osm_gdf(_make_osm_row(1, version=4))  # higher version
        report = detect_osm_changes(existing, new, extraction_date=_TODAY)
        assert report.updated_features == 1
        assert report.unchanged_features == 0

    def test_same_version_is_unchanged(self):
        existing = _osm_gdf(_make_osm_row(1, version=5))
        new = _osm_gdf(_make_osm_row(1, version=5))
        report = detect_osm_changes(existing, new, extraction_date=_TODAY)
        assert report.unchanged_features == 1
        assert report.updated_features == 0

    def test_timestamp_fallback_when_no_version(self):
        existing = _osm_gdf(_make_osm_row(1, version=None, timestamp="2026-01-01T00:00:00Z"))
        new = _osm_gdf(_make_osm_row(1, version=None, timestamp="2026-06-01T00:00:00Z"))
        report = detect_osm_changes(existing, new, extraction_date=_TODAY)
        assert report.updated_features == 1

    def test_same_timestamp_no_version_is_unchanged(self):
        ts = "2026-01-01T00:00:00Z"
        existing = _osm_gdf(_make_osm_row(1, version=None, timestamp=ts))
        new = _osm_gdf(_make_osm_row(1, version=None, timestamp=ts))
        report = detect_osm_changes(existing, new, extraction_date=_TODAY)
        assert report.unchanged_features == 1

    def test_mixed_scenario(self):
        existing = _osm_gdf(
            _make_osm_row(1, version=2),   # will be updated
            _make_osm_row(2, version=1),   # will be unchanged
            _make_osm_row(3, version=1),   # will be removed
        )
        new = _osm_gdf(
            _make_osm_row(1, version=3),   # updated
            _make_osm_row(2, version=1),   # unchanged
            _make_osm_row(4, version=1),   # new
        )
        report = detect_osm_changes(existing, new, extraction_date=_TODAY)
        assert report.new_features == 1
        assert report.updated_features == 1
        assert report.unchanged_features == 1
        assert report.removed_features == 1

    def test_totals_are_consistent(self):
        existing = _osm_gdf(_make_osm_row(1, version=1), _make_osm_row(2, version=1))
        new = _osm_gdf(_make_osm_row(1, version=2), _make_osm_row(3, version=1))
        report = detect_osm_changes(existing, new, extraction_date=_TODAY)
        assert report.total_existing == 2
        assert report.total_fetched == 2
        # new=1, updated=1, unchanged=0, removed=1
        assert report.new_features + report.updated_features + report.unchanged_features == report.total_fetched


# ---------------------------------------------------------------------------
# OsmChangeReport — serialisation
# ---------------------------------------------------------------------------

class TestOsmChangeReport:
    def test_to_dict_has_all_keys(self):
        r = OsmChangeReport(
            extraction_date=_TODAY,
            total_existing=10,
            total_fetched=12,
            new_features=3,
            updated_features=2,
            unchanged_features=7,
            removed_features=1,
        )
        d = r.to_dict()
        for key in ("extraction_date", "total_existing", "total_fetched",
                    "new_features", "updated_features", "unchanged_features",
                    "removed_features", "hard_refresh"):
            assert key in d, f"Missing key: {key}"

    def test_str_representation_is_human_readable(self):
        r = OsmChangeReport(
            extraction_date=_TODAY,
            total_existing=5,
            total_fetched=6,
            new_features=2,
            updated_features=1,
            unchanged_features=3,
            removed_features=1,
        )
        s = str(r)
        assert "+2" in s   # new
        assert "~1" in s   # updated
        assert "=3" in s   # unchanged
        assert "-1" in s   # removed


# ---------------------------------------------------------------------------
# build_refresh_merge — temporal history preservation
# ---------------------------------------------------------------------------

class TestBuildRefreshMerge:
    def test_first_seen_preserved_for_updated_feature(self):
        original_first_seen = _LAST_WEEK
        existing = _osm_gdf(_make_osm_row(1, version=1, first_seen=original_first_seen))
        new = _osm_gdf(_make_osm_row(1, version=2))  # updated

        result = build_refresh_merge(existing, new, extraction_date=_TODAY)
        merged = result.merged_gdf

        row = merged[merged["osm_id"] == 1].iloc[0]
        assert row["first_seen"] == original_first_seen

    def test_last_seen_advanced_for_seen_feature(self):
        existing = _osm_gdf(_make_osm_row(1, version=1, last_seen=_LAST_WEEK))
        new = _osm_gdf(_make_osm_row(1, version=2))

        result = build_refresh_merge(existing, new, extraction_date=_TODAY)
        merged = result.merged_gdf

        row = merged[merged["osm_id"] == 1].iloc[0]
        assert row["last_seen"] == _TODAY

    def test_removed_feature_retained_with_status(self):
        existing = _osm_gdf(_make_osm_row(1, version=1))
        new = _osm_gdf(_make_osm_row(2, version=1))  # feature 1 absent

        result = build_refresh_merge(
            existing, new,
            extraction_date=_TODAY,
            mark_removed_as="not_seen_on_refresh",
        )
        merged = result.merged_gdf

        assert 1 in merged["osm_id"].values, "Removed feature must be retained"
        removed_row = merged[merged["osm_id"] == 1].iloc[0]
        assert removed_row["operational_status"] == "not_seen_on_refresh"

    def test_new_feature_appears_in_merged(self):
        existing = _osm_gdf(_make_osm_row(1, version=1))
        new = _osm_gdf(_make_osm_row(1, version=1), _make_osm_row(99, version=1))

        result = build_refresh_merge(existing, new, extraction_date=_TODAY)
        merged = result.merged_gdf

        assert 99 in merged["osm_id"].values

    def test_no_duplicates_after_merge(self):
        existing = _osm_gdf(_make_osm_row(1, version=1), _make_osm_row(2, version=1))
        new = _osm_gdf(_make_osm_row(1, version=2), _make_osm_row(3, version=1))

        result = build_refresh_merge(existing, new, extraction_date=_TODAY)
        merged = result.merged_gdf

        keys = merged["osm_type"].astype(str) + "|" + merged["osm_id"].astype(str)
        assert keys.duplicated().sum() == 0, "No (osm_type, osm_id) duplicates allowed"

    def test_idempotency_unchanged_features(self):
        """Running refresh twice with the same data should be stable."""
        existing = _osm_gdf(_make_osm_row(1, version=3, first_seen=_LAST_WEEK))
        new = _osm_gdf(_make_osm_row(1, version=3))  # same version — unchanged

        result1 = build_refresh_merge(existing, new, extraction_date=_TODAY)
        # Simulate writing result1 back and running again
        result2 = build_refresh_merge(result1.merged_gdf, new, extraction_date=_TODAY)

        row = result2.merged_gdf[result2.merged_gdf["osm_id"] == 1].iloc[0]
        assert row["first_seen"] == _LAST_WEEK  # still preserved

    def test_report_embedded_in_result(self):
        existing = _osm_gdf(_make_osm_row(1, version=1))
        new = _osm_gdf(_make_osm_row(1, version=2))

        result = build_refresh_merge(existing, new, extraction_date=_TODAY)
        assert isinstance(result.report, OsmChangeReport)
        assert result.report.updated_features == 1

    def test_empty_new_gdf_retains_all_existing(self):
        existing = _osm_gdf(_make_osm_row(1), _make_osm_row(2))
        new = _osm_gdf()

        result = build_refresh_merge(existing, new, extraction_date=_TODAY)
        assert len(result.merged_gdf) == 2

    def test_geometry_replaced_on_update(self):
        old_geom = Point(77.0, 28.0)
        new_geom = Point(77.5, 28.5)

        existing = _osm_gdf(_make_osm_row(1, version=1, geom=old_geom))
        new = _osm_gdf(_make_osm_row(1, version=2, geom=new_geom))

        result = build_refresh_merge(existing, new, extraction_date=_TODAY)
        row = result.merged_gdf[result.merged_gdf["osm_id"] == 1].iloc[0]
        assert row["geometry"].equals(new_geom)


# ---------------------------------------------------------------------------
# Overpass — osm_version / osm_changeset capture
# ---------------------------------------------------------------------------

class TestOverpassVersionCapture:
    def _payload_with_meta(self) -> dict:
        return {
            "elements": [
                {
                    "type": "node",
                    "id": 42,
                    "lat": 28.5,
                    "lon": 77.2,
                    "version": 7,
                    "changeset": 99001,
                    "timestamp": "2026-08-15T12:00:00Z",
                    "tags": {"name": "Test Factory", "landuse": "industrial"},
                },
                {
                    "type": "node",
                    "id": 43,
                    "lat": 28.6,
                    "lon": 77.3,
                    # version and changeset absent (older Overpass output)
                    "timestamp": "2026-01-01T00:00:00Z",
                    "tags": {"name": "Old Plant", "industrial": "works"},
                },
            ]
        }

    def test_version_captured_when_present(self):
        gdf = overpass_elements_to_geodataframe(self._payload_with_meta())
        row = gdf[gdf["osm_id"] == 42].iloc[0]
        assert row["osm_version"] == 7

    def test_changeset_captured_when_present(self):
        gdf = overpass_elements_to_geodataframe(self._payload_with_meta())
        row = gdf[gdf["osm_id"] == 42].iloc[0]
        assert row["osm_changeset"] == 99001

    def test_version_is_none_when_absent(self):
        gdf = overpass_elements_to_geodataframe(self._payload_with_meta())
        row = gdf[gdf["osm_id"] == 43].iloc[0]
        assert row["osm_version"] is None

    def test_changeset_is_none_when_absent(self):
        gdf = overpass_elements_to_geodataframe(self._payload_with_meta())
        row = gdf[gdf["osm_id"] == 43].iloc[0]
        assert row["osm_changeset"] is None

    def test_timestamp_still_captured(self):
        gdf = overpass_elements_to_geodataframe(self._payload_with_meta())
        row = gdf[gdf["osm_id"] == 42].iloc[0]
        assert row["source_timestamp"] == "2026-08-15T12:00:00Z"

    def test_both_fields_present_in_all_rows(self):
        gdf = overpass_elements_to_geodataframe(self._payload_with_meta())
        assert "osm_version" in gdf.columns
        assert "osm_changeset" in gdf.columns
        assert len(gdf) == 2
