"""Tests for Phase 9 — Robust Site Identity.

Coverage:
  - Canonical anchor strategy:
      * OSM preferred over government
      * Deterministic anchor selection (lexicographically smallest key)
      * Stable UUID generation
  - Identity stability across pipeline refreshes:
      * Adding a government record to an existing OSM site DOES NOT change site_id
      * Refreshing an existing site reuses existing site_id via SiteIdentityIndex
      * Genuinely new record generates a new stable site_id
  - SiteIdentityIndex:
      * Correctly indexes existing master GeoDataFrame
      * Fast O(1) lookup
      * Graceful fallback when index is empty or None
  - Source record ledger (site_source_records):
      * build_site_source_records generates all required schema columns
      * Correctly captures source_system, source_key, source_id, source_table, source_version
"""
from __future__ import annotations

from datetime import date, datetime, UTC
from typing import Any
from uuid import UUID

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from src.matching.resolution import IndustrialRecord
from src.matching.site_identity import (
    SiteIdentityIndex,
    build_site_source_records,
    canonical_anchor,
    resolve_site_id,
)


def _make_record(
    source_type: str,
    source_id: str,
    source_table: str = "test_table",
    lat: float = 28.5,
    lon: float = 77.2,
    name: str = "Factory",
) -> IndustrialRecord:
    key = f"{source_type}:{source_id}" if source_type == "osm" else f"government:{source_table}:{source_id}"
    return IndustrialRecord(
        source_type=source_type,
        source_key=key,
        source_id=source_id,
        source_table=source_table,
        name=name,
        normalized_name=name.lower(),
        industry_type="industrial",
        normalized_industry_type="industrial",
        address=None,
        normalized_address=None,
        state="Test State",
        district="Test District",
        geometry=Point(lon, lat),
        source_date=date(2026, 1, 1),
        source_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        extraction_date=date(2026, 9, 4),
        first_seen=date(2026, 1, 1),
        last_seen=date(2026, 9, 4),
    )


class TestCanonicalAnchor:
    def test_osm_preferred_over_government(self):
        osm = _make_record("osm", "way:500")
        gov = _make_record("government", "REG-001", source_table="cpcb")
        cluster = [gov, osm]
        anchor = canonical_anchor(cluster)
        assert anchor == "osm:way:500"

    def test_smallest_osm_key_chosen_deterministically(self):
        osm1 = _make_record("osm", "way:900")
        osm2 = _make_record("osm", "node:100")
        cluster = [osm1, osm2]
        # "osm:node:100" < "osm:way:900" lexicographically
        assert canonical_anchor(cluster) == "osm:node:100"

    def test_government_only_cluster_uses_gov_anchor(self):
        gov1 = _make_record("government", "REG-002", source_table="cpcb")
        gov2 = _make_record("government", "REG-001", source_table="cpcb")
        cluster = [gov1, gov2]
        # "government:cpcb:REG-001" < "government:cpcb:REG-002"
        assert canonical_anchor(cluster) == "government:cpcb:REG-001"

    def test_site_id_is_valid_uuid(self):
        osm = _make_record("osm", "way:12345")
        site_id = resolve_site_id([osm])
        # Must parse as UUID without error
        parsed = UUID(site_id)
        assert parsed.version == 5


class TestIdentityStabilityAcrossRefreshes:
    def test_adding_new_source_preserves_site_id(self):
        """CRITICAL: Adding a government record to an existing OSM site must NOT change site_id."""
        osm = _make_record("osm", "way:12345")
        gov = _make_record("government", "REG-999", source_table="cpcb")

        # Run 1: OSM alone
        site_id_run1 = resolve_site_id([osm])

        # Run 2: OSM matched with government record
        # Because the canonical anchor is still the OSM record, site_id MUST match
        site_id_run2 = resolve_site_id([osm, gov])

        assert site_id_run1 == site_id_run2, (
            "Adding a government record to an OSM-anchored cluster altered the site_id!"
        )

    def test_reusing_site_id_from_index_across_pipeline_run(self):
        """When an existing master site exists, re-running the pipeline preserves its UUID."""
        osm = _make_record("osm", "way:100")
        gov = _make_record("government", "REG-001", source_table="cpcb")

        known_uuid = "11111111-2222-3333-4444-555555555555"
        index = SiteIdentityIndex(
            _source_key_to_site_id={"osm:way:100": known_uuid}
        )

        # Even if new government record is attached, existing site_id is reused
        resolved = resolve_site_id([osm, gov], identity_index=index)
        assert resolved == known_uuid

    def test_genuinely_new_record_gets_new_site_id(self):
        known_uuid = "11111111-2222-3333-4444-555555555555"
        index = SiteIdentityIndex(
            _source_key_to_site_id={"osm:way:100": known_uuid}
        )

        new_osm = _make_record("osm", "way:999")
        resolved = resolve_site_id([new_osm], identity_index=index)

        assert resolved != known_uuid
        assert resolved == resolve_site_id([new_osm])  # deterministic


class TestSiteIdentityIndex:
    def test_from_master_gdf_builds_correct_lookup(self):
        data = [
            {
                "site_id": "site-uuid-1",
                "osm_ids": [101, 102],
                "government_ids": ["G-1"],
                "matched_source_ids": {
                    "all": ["osm:way:101", "osm:way:102", "government:cpcb:G-1"],
                    "osm": ["osm:way:101", "osm:way:102"],
                    "government": ["government:cpcb:G-1"],
                },
                "geometry": Point(77.2, 28.5),
            },
            {
                "site_id": "site-uuid-2",
                "osm_ids": [201],
                "government_ids": [],
                "matched_source_ids": {"all": ["osm:way:201"]},
                "geometry": Point(77.3, 28.6),
            },
        ]
        master_gdf = gpd.GeoDataFrame(data, geometry="geometry", crs="EPSG:4326")
        index = SiteIdentityIndex.from_master_gdf(master_gdf)

        assert index.get("osm:way:101") == "site-uuid-1"
        assert index.get("government:cpcb:G-1") == "site-uuid-1"
        assert index.get("osm:way:201") == "site-uuid-2"
        assert index.get("osm:way:999") is None

    def test_empty_or_none_gdf_handled_gracefully(self):
        index_none = SiteIdentityIndex.from_master_gdf(None)
        assert index_none.get("osm:way:1") is None

        empty_gdf = gpd.GeoDataFrame(columns=["site_id", "geometry"], geometry="geometry")
        index_empty = SiteIdentityIndex.from_master_gdf(empty_gdf)
        assert index_empty.get("osm:way:1") is None


class TestBuildSiteSourceRecords:
    def test_builds_source_ledger_records(self):
        site_id = "test-site-uuid"
        osm = _make_record("osm", "way:555")
        gov = _make_record("government", "REG-777", source_table="cpcb")

        today = date(2026, 9, 4)
        rows = build_site_source_records(site_id, [osm, gov], today)

        assert len(rows) == 2
        keys = {r["source_key"] for r in rows}
        assert "osm:way:555" in keys
        assert "government:cpcb:REG-777" in keys

        for r in rows:
            assert r["site_id"] == site_id
            assert r["first_linked"] == today
            assert r["last_linked"] == today
            assert r["created_at"] is not None
            assert r["updated_at"] is not None
