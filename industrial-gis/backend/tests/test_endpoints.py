"""
Tests for:
  GET /api/industries           — filter endpoint
  GET /api/industries/{site_id} — single site
  GET /api/industries/search    — full-text search
  GET /api/filters/options      — dropdown options
  GET /api/statistics           — aggregate stats

All tests run against DATA_MODE=synthetic (no database required).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_feature_collection(path: str) -> dict:
    resp = client.get(path)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code} for {path}\n{resp.text}"
    data = resp.json()
    assert data["type"] == "FeatureCollection"
    assert isinstance(data["features"], list)
    return data


def get_all_sites() -> list:
    return get_feature_collection("/api/industries")["features"]


# ─────────────────────────────────────────────────────────────────────────────
# /api/industries — list + filter
# ─────────────────────────────────────────────────────────────────────────────

class TestIndustriesEndpoint:
    def test_returns_feature_collection(self) -> None:
        get_feature_collection("/api/industries")

    def test_returns_at_least_one_site(self) -> None:
        features = get_all_sites()
        assert len(features) > 0, "Expected at least one synthetic site"

    def test_each_feature_has_required_fields(self) -> None:
        REQUIRED_PROPS = {
            "site_id", "name", "state", "district",
            "industry_type", "match_confidence", "match_score",
            "latitude", "longitude",
        }
        features = get_all_sites()
        for f in features[:5]:  # spot-check first 5
            assert f["type"] == "Feature"
            assert f["geometry"]["type"] == "Point"
            coords = f["geometry"]["coordinates"]
            assert len(coords) == 2
            assert isinstance(coords[0], float)  # longitude
            assert isinstance(coords[1], float)  # latitude
            missing = REQUIRED_PROPS - set(f["properties"])
            assert not missing, f"Feature missing properties: {missing}"

    def test_state_filter_returns_only_matching_state(self) -> None:
        all_features = get_all_sites()
        # Find a state that exists
        state = all_features[0]["properties"]["state"]
        filtered = get_feature_collection(f"/api/industries?state={state}")["features"]
        assert len(filtered) > 0
        for f in filtered:
            assert f["properties"]["state"] == state

    def test_bbox_covering_india_returns_all(self) -> None:
        all_count = len(get_all_sites())
        bbox_features = get_feature_collection("/api/industries?bbox=68,6,98,38")["features"]
        # A bbox covering all of India should return all sites
        assert len(bbox_features) == all_count

    def test_bbox_empty_region_returns_zero(self) -> None:
        # Middle of Pacific Ocean — no Indian industry sites
        result = get_feature_collection("/api/industries?bbox=-180,-80,-90,-10")
        assert len(result["features"]) == 0

    def test_limit_param_respected(self) -> None:
        result = get_feature_collection("/api/industries?limit=3")
        assert len(result["features"]) <= 3

    def test_invalid_bbox_handled_gracefully(self) -> None:
        # Should not crash; falls back to no bbox filter
        resp = client.get("/api/industries?bbox=not_a_number")
        assert resp.status_code == 200

    def test_unknown_state_returns_empty(self) -> None:
        result = get_feature_collection("/api/industries?state=ZzzFakeStateName")
        assert len(result["features"]) == 0


# ─────────────────────────────────────────────────────────────────────────────
# /api/industries/{site_id}
# ─────────────────────────────────────────────────────────────────────────────

class TestSiteDetailEndpoint:
    def test_valid_site_id_returns_feature(self) -> None:
        # Grab a real site_id from the list
        site_id = get_all_sites()[0]["properties"]["site_id"]
        resp = client.get(f"/api/industries/{site_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "Feature"
        assert data["properties"]["site_id"] == site_id

    def test_nonexistent_site_id_returns_404(self) -> None:
        resp = client.get("/api/industries/does-not-exist-xyz-99999")
        assert resp.status_code == 404

    def test_returned_feature_has_geometry(self) -> None:
        site_id = get_all_sites()[0]["properties"]["site_id"]
        data = client.get(f"/api/industries/{site_id}").json()
        assert data["geometry"]["type"] == "Point"
        coords = data["geometry"]["coordinates"]
        assert len(coords) == 2


# ─────────────────────────────────────────────────────────────────────────────
# /api/industries/search
# ─────────────────────────────────────────────────────────────────────────────

class TestSearchEndpoint:
    def test_search_returns_feature_collection(self) -> None:
        get_feature_collection("/api/industries/search?q=a")

    def test_search_missing_q_returns_422(self) -> None:
        resp = client.get("/api/industries/search")
        assert resp.status_code == 422  # FastAPI validation error

    def test_search_gibberish_returns_empty(self) -> None:
        result = get_feature_collection("/api/industries/search?q=ZzzQqq99XYZGibberish")
        assert len(result["features"]) == 0

    def test_search_by_state_name(self) -> None:
        # Find a state in the data and search for it
        all_features = get_all_sites()
        state = all_features[0]["properties"]["state"]
        result = get_feature_collection(f"/api/industries/search?q={state}")
        assert len(result["features"]) > 0, f"Expected results for state '{state}'"

    def test_search_results_are_feature_collection(self) -> None:
        resp = client.get("/api/industries/search?q=industrial")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"

    def test_search_with_state_filter(self) -> None:
        # Search for a term that exists, combined with a valid state
        all_features = get_all_sites()
        state = all_features[0]["properties"]["state"]
        resp = client.get(f"/api/industries/search?q=a&state={state}")
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# /api/statistics
# ─────────────────────────────────────────────────────────────────────────────

class TestStatisticsEndpoint:
    REQUIRED_KEYS = {
        "total_sites",
        "by_state",
        "by_industry_type",
        "osm_records",
        "government_records",
        "matched_records",
        "unmatched_records",
        "high_confidence_matches",
        "confidence_distribution",
    }

    def test_returns_200(self) -> None:
        resp = client.get("/api/statistics")
        assert resp.status_code == 200

    def test_has_all_required_keys(self) -> None:
        data = client.get("/api/statistics").json()
        missing = self.REQUIRED_KEYS - set(data)
        assert not missing, f"Statistics response missing keys: {missing}"

    def test_total_sites_is_positive(self) -> None:
        data = client.get("/api/statistics").json()
        assert data["total_sites"] > 0

    def test_matched_plus_unmatched_equals_total(self) -> None:
        data = client.get("/api/statistics").json()
        assert data["matched_records"] + data["unmatched_records"] == data["total_sites"]

    def test_by_state_is_dict_with_counts(self) -> None:
        data = client.get("/api/statistics").json()
        by_state = data["by_state"]
        assert isinstance(by_state, dict)
        assert len(by_state) > 0
        for state, count in by_state.items():
            assert isinstance(state, str)
            assert isinstance(count, int)
            assert count > 0

    def test_by_industry_type_is_dict(self) -> None:
        data = client.get("/api/statistics").json()
        assert isinstance(data["by_industry_type"], dict)

    def test_by_state_sum_equals_total(self) -> None:
        data = client.get("/api/statistics").json()
        state_sum = sum(data["by_state"].values())
        assert state_sum == data["total_sites"]


# ─────────────────────────────────────────────────────────────────────────────
# /api/filters/options
# ─────────────────────────────────────────────────────────────────────────────

class TestFilterOptionsEndpoint:
    def test_returns_200(self) -> None:
        assert client.get("/api/filters/options").status_code == 200

    def test_has_all_required_keys(self) -> None:
        data = client.get("/api/filters/options").json()
        for key in ("states", "districts", "industry_types", "statuses", "confidence_levels"):
            assert key in data, f"Missing key: {key}"

    def test_states_is_non_empty_sorted_list(self) -> None:
        data = client.get("/api/filters/options").json()
        states = data["states"]
        assert isinstance(states, list)
        assert len(states) > 0
        assert states == sorted(states)

    def test_states_match_industry_data(self) -> None:
        options = client.get("/api/filters/options").json()
        industries = get_all_sites()
        industry_states = {f["properties"]["state"] for f in industries}
        option_states = set(options["states"])
        # Every state in the options should have at least one site
        assert industry_states == option_states
