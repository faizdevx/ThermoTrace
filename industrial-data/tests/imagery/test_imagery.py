"""Imagery core: scene choice, indices, hot pixels, reasons, cache and the per-cycle enrichment (no network)."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.imagery import core, enrich

T0 = datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc)


def _feature(fid, when, cloud, tile="T45QVG", orbit=90, bands=core.TRUE_BANDS + core.SWIR_BANDS + ("scl",)):
    return {"id": fid, "properties": {"datetime": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "eo:cloud_cover": cloud, "grid:code": tile,
                                      "sat:relative_orbit": orbit, "platform": "sentinel-2b"},
            "assets": {b: {"href": f"https://x/{fid}/{b}.tif"} for b in bands}, "links": []}


# ---- scene choice ----------------------------------------------------------------------------------------------
def test_after_scene_is_clearest_in_window_and_never_before_the_first_detection():
    fs = [_feature("early", T0 - timedelta(days=3), 2.0), _feature("clear", T0 + timedelta(days=2), 5.0),
          _feature("cloudy", T0 + timedelta(days=1), 28.0), _feature("far", T0 + timedelta(days=9), 1.0),
          _feature("over", T0, 45.0)]
    picks = core.pick_after(fs, T0, not_before=T0 - timedelta(days=1))
    assert [p["feature"]["id"] for p in picks] == ["clear", "cloudy"]        # early (before first detection), far, over dropped
    assert core.pick_after(fs, T0)[0]["feature"]["id"] == "early"            # without the constraint the clearest wins


def test_before_scene_prefers_same_tile_and_orbit_then_cloud():
    after = core.pick_after([_feature("after", T0, 4.0)], T0)[0]
    fs = [_feature("other-tile", T0 - timedelta(days=6), 1.0, tile="T44QRG"),
          _feature("same-tile-other-orbit", T0 - timedelta(days=5), 2.0, orbit=40),
          _feature("same", T0 - timedelta(days=10), 15.0),
          _feature("too-old", T0 - timedelta(days=60), 0.0), _feature("too-recent", T0 - timedelta(days=1), 0.0)]
    b = core.pick_before(fs, after)
    assert b["feature"]["id"] == "same" and b["same_tile"] and b["gap_days"] == 10.0
    assert core.pick_before([_feature("after", T0, 4.0)], after) is None      # the AFTER scene is never its own reference


# ---- indices ---------------------------------------------------------------------------------------------------
def test_nbr_is_a_bounded_ratio_and_ignores_dark_pixels():
    nir = np.array([0.40, 0.30, 0.01, -0.05])
    swir = np.array([0.10, 0.30, 0.01, 0.20])
    out = core.nbr(nir, swir)
    assert out[0] == pytest.approx(0.6) and out[1] == pytest.approx(0.0)
    assert np.isnan(out[2]) and np.isnan(out[3])                              # too dark / negative reflectance
    assert np.nanmax(np.abs(core.nbr(np.array([0.5]), np.array([0.0])))) <= 1.0


def _scene(nir, swir22, swir16=None, size=40, scl=4):
    full = lambda v: np.full((size, size), v, dtype="float32")        # noqa: E731
    return {"nir08": full(nir), "swir22": full(swir22), "swir16": full(swir16 if swir16 is not None else swir22),
            "red": full(0.1), "green": full(0.1), "blue": full(0.1), "scl": full(scl)}


def test_dnbr_flags_a_burn_scar_and_classifies_severity():
    before, after = _scene(0.45, 0.12), _scene(0.20, 0.30)                       # vegetation -> char
    s = core.dnbr_summary(before, after)
    assert s["dnbr_p95"] > 0.66 and s["severity"] == "high severity" and s["burned_fraction"] == 1.0
    same = core.dnbr_summary(before, _scene(0.45, 0.12))
    assert same["dnbr_p95"] == pytest.approx(0.0) and same["burned_fraction"] == 0.0 and same["severity"].startswith("unburned")
    assert core.severity(0.05) == "unburned / no change" and core.severity(0.3) == "moderate-low severity" and core.severity(0.5) == "moderate-high severity"


def test_dnbr_refuses_when_the_chip_is_mostly_cloud():
    cloudy = _scene(0.4, 0.1, scl=9)
    assert core.dnbr_summary(_scene(0.45, 0.12), cloudy) is None


def test_hot_pixels_need_swir2_above_swir1_and_exclude_cloud():
    sc = _scene(0.3, 0.1)
    sc["swir22"][0, :5] = 0.40            # flame: B12 high and above B11 (0.1)
    sc["swir22"][1, :5] = 0.40
    sc["swir16"][1, :5] = 0.55            # bright bare ground: B11 above B12 -> not fire
    sc["swir22"][2, :5] = 0.40
    sc["scl"][2, :5] = 9                  # cloud
    h = core.hot_pixels(sc)
    assert h["count"] == 5


def test_reflectance_uses_the_assets_offset():
    arr = np.array([[0.0, 1100.0, 5100.0]], dtype="float32")
    out = core.reflectance(arr, {"raster:bands": [{"scale": 1e-4, "offset": -0.1}]})
    assert np.isnan(out[0, 0]) and out[0, 1] == pytest.approx(0.01) and out[0, 2] == pytest.approx(0.41)
    assert core.reflectance(arr, {})[0, 2] == pytest.approx(0.51)


# ---- reasons and features ---------------------------------------------------------------------------------------
def _result(**over):
    r = {"available": True, "scene": {"datetime": "2026-10-13T05:02:00+00:00", "cloud_cover_percent": 12.0, "chip_cloud_fraction": 0.05,
                                      "id": "S2B_x"},
         "before_scene": {"datetime": "2026-10-03T05:02:00+00:00", "gap_days": 10.0},
         "indices": {"dnbr_p95": 0.31, "dnbr_mean": 0.08, "burned_fraction": 0.12, "severity": "moderate-low severity"},
         "hot_pixels": {"count": 14, "fraction": 0.002}, "landsat": {"available": True, "scene": {"datetime": "2026-10-06T04:43:00+00:00"},
                                                                      "st_max_k": 324.0, "st_excess_k": 15.0}}
    r.update(over)
    return r


def test_reasons_carry_dnbr_hot_pixels_and_landsat():
    lines = core.imagery_reasons(_result())
    assert any("dNBR p95 +0.31 (moderate-low severity)" in l and "12% of the 3 x 3 km chip" in l for l in lines)
    assert any("14 SWIR hot pixel(s)" in l for l in lines) and any("Landsat TIRS 2026-10-06" in l and "15 K above" in l for l in lines)
    feats = core.imagery_features(_result())
    assert feats["s2_dnbr_p95"] == 0.31 and feats["s2_hot_pixels"] == 14.0 and feats["landsat_st_excess_k"] == 15.0


def test_reasons_say_so_when_there_is_no_scene_or_no_reference():
    none = core.imagery_reasons({"available": False})
    assert len(none) == 1 and "could not confirm or exclude" in none[0]
    no_ref = core.imagery_reasons(_result(before_scene=None, indices=None, hot_pixels={"count": 0}, landsat={"available": False}))
    assert len(no_ref) == 1 and "no dNBR" in no_ref[0]
    assert core.imagery_features(None)["s2_available"] == 0.0


# ---- cache and enrichment ---------------------------------------------------------------------------------------
def test_disk_cache_roundtrip_is_keyed_by_source_and_day_and_prunes(tmp_path):
    c = core.DiskCache(tmp_path / "img")
    assert c.get("ts-1", "2026-10-13T20:00:00+00:00") is None
    c.put("ts-1", "2026-10-13T20:00:00+00:00", {"available": True})
    assert c.get("ts-1", "2026-10-13T01:00:00+00:00") == {"available": True}      # same day -> same entry
    assert c.get("ts-1", "2026-10-14T01:00:00+00:00") is None
    p = c.path("ts-1", "2026-10-13")
    old = time.time() - 30 * 86400
    os.utime(p, (old, old))
    assert c.prune(14) == 1 and c.get("ts-1", "2026-10-13") is None


def _sources():
    return pd.DataFrame([
        {"source_id": "a", "lat": 21.0, "lon": 83.0, "last_seen": pd.Timestamp("2026-10-13T20:00", tz="UTC"),
         "first_seen": pd.Timestamp("2026-10-12T20:00", tz="UTC"), "alert_level": "high", "thermal_class": "industrial_fire_candidate",
         "frp_max": 40.0, "frp_z": 5.0, "model_class": "industrial_persistent", "reasons": ["r1"]},
        {"source_id": "b", "lat": 22.0, "lon": 84.0, "last_seen": pd.Timestamp("2026-10-13T20:00", tz="UTC"),
         "first_seen": pd.Timestamp("2026-10-12T20:00", tz="UTC"), "alert_level": None, "thermal_class": "agricultural_burning",
         "frp_max": 5.0, "frp_z": 1.0, "model_class": "agricultural_burning", "reasons": ["r2"]},
        {"source_id": "c", "lat": 23.0, "lon": 85.0, "last_seen": pd.Timestamp("2026-10-13T20:00", tz="UTC"),
         "first_seen": pd.Timestamp("2026-10-12T20:00", tz="UTC"), "alert_level": None, "thermal_class": "industrial_persistent",
         "frp_max": 12.0, "frp_z": 4.0, "model_class": "industrial_persistent", "reasons": []},
    ])


def test_candidates_are_alerts_then_fire_candidates_then_hot_industrial():
    order = enrich.candidate_order(_sources()).source_id.tolist()
    assert order == ["a", "c"]                         # the crop fire is never worth a request


def test_enrichment_computes_a_budgeted_few_caches_them_and_appends_reasons(tmp_path):
    calls = []

    def fake(lon, lat, last_seen, first_seen=None, with_images=True, **_):
        calls.append((lon, lat))
        return _result()

    out, stats = enrich.enrich_sources(_sources(), tmp_path, per_cycle=1, analyse=fake)
    assert len(calls) == 1 and stats["computed"] == 1 and stats["candidates"] == 2
    a = out.set_index("source_id").loc["a"]
    assert a.imagery_available and a.imagery_dnbr_p95 == 0.31 and a.imagery_swir_hot_pixels == 14
    assert a.reasons[0] == "r1" and any("dNBR" in r for r in a.reasons)
    assert out.set_index("source_id").loc["b"].reasons == ["r2"] and pd.isna(out.set_index("source_id").loc["b"].imagery_dnbr_p95)
    # next cycle: "a" comes from the cache, the budget goes to "c"
    out2, stats2 = enrich.enrich_sources(_sources(), tmp_path, per_cycle=1, analyse=fake)
    assert len(calls) == 2 and calls[1] == (85.0, 23.0) and stats2["cached"] == 1 and stats2["computed"] == 1


def test_enrichment_survives_a_failing_service(tmp_path):
    def boom(*a, **k):
        raise TimeoutError("stac down")
    out, stats = enrich.enrich_sources(_sources(), tmp_path, per_cycle=3, analyse=boom)
    assert stats["failed"] == 2 and stats["computed"] == 0
    assert out.set_index("source_id").loc["a"].reasons == ["r1"] and len(out) == 3
