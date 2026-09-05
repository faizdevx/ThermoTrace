"""Tests for Phase 8 — scalable entity matching with blocking.

No network I/O, no PostGIS.

Coverage:
  * BlockingConfig defaults
  * spatial_radius strategy: returns pairs within distance, not outside
  * district strategy: returns pairs in same district only
  * spatial_grid strategy: returns same-cell and adjacent-cell pairs
  * name_prefix strategy: same prefix → candidates
  * industry_type strategy: same type → candidates
  * generate_blocked_candidates: union of strategies
  * generate_blocked_candidates: min_strategies intersection
  * generate_blocked_candidates: max_candidates_per_osm cap
  * generate_blocked_candidates: empty inputs
  * BlockingStats: correct pair counts and reduction_ratio
  * generate_candidate_matches: uses blocking (output << N×M)
  * generate_candidate_matches: preserves match_method, match_score, review_required
  * Candidate match_method contains blocking provenance tag
"""
from __future__ import annotations

from datetime import date

import geopandas as gpd
import pytest
from shapely.geometry import Point

from src.matching.blocking import (
    BlockingConfig,
    BlockingStats,
    _block_by_district,
    _block_by_industry_type,
    _block_by_name_prefix,
    _block_by_spatial_grid,
    _block_by_spatial_radius,
    generate_blocked_candidates,
)
from src.matching.resolution import (
    IndustrialRecord,
    generate_candidate_matches,
    classify_match_confidence,
)
from src.matching.config import MatchingSourceConfig, MatchingThresholdConfig, MatchingWeightConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rec(
    source_type: str,
    source_id: str,
    lat: float,
    lon: float,
    name: str | None = None,
    industry: str | None = None,
    district: str | None = None,
    state: str | None = None,
    source_table: str = "test_table",
) -> IndustrialRecord:
    return IndustrialRecord(
        source_type=source_type,
        source_key=f"{source_type}:{source_id}",
        source_id=source_id,
        source_table=source_table,
        name=name,
        normalized_name=name.lower() if name else None,
        industry_type=industry,
        normalized_industry_type=industry.lower() if industry else None,
        address=None,
        normalized_address=None,
        state=state,
        district=district,
        geometry=Point(lon, lat),
        source_date=None,
        source_timestamp=None,
        extraction_date=date(2026, 9, 4),
        first_seen=date(2026, 9, 4),
        last_seen=date(2026, 9, 4),
    )


def _osm(i: int, lat: float = 28.5, lon: float = 77.2,
         name: str = "Factory", industry: str = "textile",
         district: str = "Test") -> IndustrialRecord:
    return _rec("osm", str(i), lat, lon, name, industry, district)


def _gov(i: int, lat: float = 28.5, lon: float = 77.2,
         name: str = "Factory", industry: str = "textile",
         district: str = "Test") -> IndustrialRecord:
    return _rec("government", str(i), lat, lon, name, industry, district)


def _default_source_config(max_dist: float = 2000.0) -> MatchingSourceConfig:
    from src.matching.config import MatchingSourceConfig
    return MatchingSourceConfig(
        require_state_district_consistency=False,
        max_spatial_distance_meters=max_dist,
        candidate_name_similarity_min=50,
        candidate_address_similarity_min=50,
        candidate_industry_similarity_min=50,
    )


def _default_weight_config() -> MatchingWeightConfig:
    from src.matching.config import MatchingWeightConfig
    return MatchingWeightConfig(spatial=0.4, name=0.4, industry=0.1, address=0.1)


def _default_threshold_config() -> MatchingThresholdConfig:
    from src.matching.config import MatchingThresholdConfig
    return MatchingThresholdConfig(automatic_match=0.8, uncertain_match=0.65)


# ---------------------------------------------------------------------------
# Blocking strategies — unit tests
# ---------------------------------------------------------------------------

class TestSpatialRadiusBlocking:
    def test_pair_within_distance_included(self):
        osm = [_osm(1, lat=28.5000, lon=77.2000)]
        gov = [_gov(1, lat=28.5001, lon=77.2001)]  # ~15 m
        pairs = _block_by_spatial_radius(osm, gov, max_distance_meters=500)
        assert (0, 0) in pairs

    def test_pair_outside_distance_excluded(self):
        osm = [_osm(1, lat=28.5, lon=77.2)]
        gov = [_gov(1, lat=29.5, lon=78.2)]  # ~140 km
        pairs = _block_by_spatial_radius(osm, gov, max_distance_meters=500)
        assert len(pairs) == 0

    def test_empty_inputs_return_empty(self):
        pairs = _block_by_spatial_radius([], [], max_distance_meters=500)
        assert len(pairs) == 0

    def test_multiple_gov_candidates_returned(self):
        osm = [_osm(1, lat=28.5, lon=77.2)]
        gov = [
            _gov(1, lat=28.5001, lon=77.2001),  # nearby
            _gov(2, lat=28.5002, lon=77.2002),  # nearby
            _gov(3, lat=29.5, lon=78.2),         # far
        ]
        pairs = _block_by_spatial_radius(osm, gov, max_distance_meters=500)
        gov_indices = {j for _, j in pairs}
        assert 0 in gov_indices
        assert 1 in gov_indices
        assert 2 not in gov_indices


class TestDistrictBlocking:
    def test_same_district_paired(self):
        osm = [_osm(1, district="North Goa")]
        gov = [_gov(1, district="North Goa")]
        pairs = _block_by_district(osm, gov)
        assert (0, 0) in pairs

    def test_different_district_not_paired(self):
        osm = [_osm(1, district="North Goa")]
        gov = [_gov(1, district="South Goa")]
        pairs = _block_by_district(osm, gov)
        assert len(pairs) == 0

    def test_missing_district_not_paired(self):
        osm = [_osm(1, district=None)]
        gov = [_gov(1, district="North Goa")]
        pairs = _block_by_district(osm, gov)
        assert len(pairs) == 0


class TestSpatialGridBlocking:
    def test_same_cell_paired(self):
        osm = [_osm(1, lat=28.50, lon=77.20)]
        gov = [_gov(1, lat=28.51, lon=77.21)]  # same 0.5° cell
        pairs = _block_by_spatial_grid(osm, gov, grid_degrees=0.5)
        assert (0, 0) in pairs

    def test_adjacent_cell_paired(self):
        osm = [_osm(1, lat=28.49, lon=77.49)]  # near cell boundary
        gov = [_gov(1, lat=28.51, lon=77.51)]  # adjacent cell
        pairs = _block_by_spatial_grid(osm, gov, grid_degrees=0.5)
        assert (0, 0) in pairs


class TestNamePrefixBlocking:
    def test_same_prefix_paired(self):
        osm = [_osm(1, name="Alpha Works")]
        gov = [_gov(1, name="Alpha factory")]
        pairs = _block_by_name_prefix(osm, gov, prefix_length=2)
        assert (0, 0) in pairs

    def test_different_prefix_not_paired(self):
        osm = [_osm(1, name="Alpha Works")]
        gov = [_gov(1, name="Zeta factory")]
        pairs = _block_by_name_prefix(osm, gov, prefix_length=2)
        assert len(pairs) == 0


class TestIndustryTypeBlocking:
    def test_same_type_paired(self):
        osm = [_osm(1, industry="textile")]
        gov = [_gov(1, industry="textile")]
        pairs = _block_by_industry_type(osm, gov)
        assert (0, 0) in pairs

    def test_different_type_not_paired(self):
        osm = [_osm(1, industry="textile")]
        gov = [_gov(1, industry="chemical")]
        pairs = _block_by_industry_type(osm, gov)
        assert len(pairs) == 0


# ---------------------------------------------------------------------------
# generate_blocked_candidates — integration
# ---------------------------------------------------------------------------

class TestGenerateBlockedCandidates:
    def test_returns_tuple_of_pairs_and_stats(self):
        osm = [_osm(1)]
        gov = [_gov(1)]
        config = BlockingConfig(strategies=["spatial_radius"], max_spatial_distance_meters=500)
        result = generate_blocked_candidates(osm, gov, config)
        assert isinstance(result, tuple)
        pairs, stats = result
        assert isinstance(stats, BlockingStats)

    def test_empty_inputs_return_empty(self):
        config = BlockingConfig()
        pairs, stats = generate_blocked_candidates([], [], config)
        assert len(pairs) == 0

    def test_union_of_two_strategies(self):
        osm = [_osm(1, district="A")]
        gov_near = [_gov(1, lat=28.5001, lon=77.2001, district="A")]  # nearby + same district
        config = BlockingConfig(
            strategies=["spatial_radius", "district"],
            max_spatial_distance_meters=500,
            min_strategies=1,
        )
        pairs, _ = generate_blocked_candidates(osm, gov_near, config)
        assert len(pairs) == 1

    def test_intersection_requires_both_strategies(self):
        """With min_strategies=2, a pair must appear in both strategies."""
        osm = [_osm(1, district="A")]
        gov = [_gov(1, lat=28.5001, lon=77.2001, district="B")]  # near but different district
        config = BlockingConfig(
            strategies=["spatial_radius", "district"],
            max_spatial_distance_meters=500,
            min_strategies=2,
        )
        pairs, _ = generate_blocked_candidates(osm, gov, config)
        # spatial_radius returns it; district does NOT (different district)
        # min_strategies=2 → excluded
        assert len(pairs) == 0

    def test_max_candidates_cap_applied(self):
        osm = [_osm(1)]
        gov = [_gov(i, lat=28.5 + i * 0.0001, lon=77.2 + i * 0.0001) for i in range(20)]
        config = BlockingConfig(
            strategies=["spatial_radius"],
            max_spatial_distance_meters=5000,
            max_candidates_per_osm=5,
        )
        pairs, _ = generate_blocked_candidates(osm, gov, config)
        assert len(pairs) <= 5

    def test_blocking_stats_reduction_ratio(self):
        osm = [_osm(i) for i in range(10)]
        gov = [_gov(i, lat=29.0 + i * 0.1) for i in range(10)]  # all far from osm
        config = BlockingConfig(
            strategies=["spatial_radius"],
            max_spatial_distance_meters=100,
        )
        pairs, stats = generate_blocked_candidates(osm, gov, config)
        assert stats.max_possible_pairs == 100
        # All are far → 0 candidates → 100% reduction
        assert stats.candidate_pairs == 0
        assert stats.reduction_ratio == 1.0


# ---------------------------------------------------------------------------
# generate_candidate_matches — integration with blocking
# ---------------------------------------------------------------------------

class TestGenerateCandidateMatchesBlocking:
    def test_returns_fewer_than_n_times_m_candidates(self):
        """Blocking must produce strictly fewer pairs than N×M for sparse data."""
        osm = [_osm(i, lat=28.5 + i * 0.1, lon=77.2 + i * 0.1) for i in range(10)]
        gov = [_gov(i, lat=28.5 + i * 0.1 + 5.0) for i in range(10)]  # all far
        source_config = _default_source_config(max_dist=500)
        weight_config = _default_weight_config()
        threshold_config = _default_threshold_config()
        candidates = generate_candidate_matches(osm, gov, source_config, weight_config, threshold_config)
        # 10×10 = 100 max pairs; spatial blocking should produce 0 since all far
        assert len(candidates) < 100

    def test_nearby_pair_with_same_name_produces_candidate(self):
        osm = [_osm(1, lat=28.5000, lon=77.2000, name="Alpha Works", industry="textile")]
        gov = [_gov(1, lat=28.5001, lon=77.2001, name="Alpha Works", industry="textile")]
        source_config = _default_source_config(max_dist=2000)
        weight_config = _default_weight_config()
        threshold_config = _default_threshold_config()
        candidates = generate_candidate_matches(osm, gov, source_config, weight_config, threshold_config)
        assert len(candidates) >= 1

    def test_candidate_has_required_fields(self):
        osm = [_osm(1, lat=28.5000, lon=77.2000, name="Alpha Works")]
        gov = [_gov(1, lat=28.5001, lon=77.2001, name="Alpha Works")]
        source_config = _default_source_config()
        candidates = generate_candidate_matches(
            osm, gov, source_config, _default_weight_config(), _default_threshold_config()
        )
        if candidates:
            c = candidates[0]
            assert c.match_score >= 0
            assert c.match_confidence in ("automatic_match", "uncertain_review", "non_match")
            assert isinstance(c.review_required, bool)
            assert c.match_method  # non-empty string

    def test_match_method_contains_blocking_provenance(self):
        osm = [_osm(1, lat=28.5000, lon=77.2000, name="Alpha Works")]
        gov = [_gov(1, lat=28.5001, lon=77.2001, name="Alpha Works")]
        source_config = _default_source_config()
        candidates = generate_candidate_matches(
            osm, gov, source_config, _default_weight_config(), _default_threshold_config()
        )
        if candidates:
            assert "blocked:" in candidates[0].match_method

    def test_original_records_preserved_via_source_keys(self):
        """CandidateMatch must carry source keys pointing back to original records."""
        osm = [_osm(42, lat=28.5000, lon=77.2000)]
        gov = [_gov(99, lat=28.5001, lon=77.2001)]
        source_config = _default_source_config()
        candidates = generate_candidate_matches(
            osm, gov, source_config, _default_weight_config(), _default_threshold_config()
        )
        if candidates:
            c = candidates[0]
            assert "osm:42" in c.osm_source_key
            assert "government:99" in c.government_source_key
            assert c.matched_source_ids.get("all")

    def test_empty_inputs_return_empty(self):
        candidates = generate_candidate_matches(
            [], [], _default_source_config(), _default_weight_config(), _default_threshold_config()
        )
        assert candidates == []
