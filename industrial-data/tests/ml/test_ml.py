"""Model package: features, calibrated model, reasons, live scoring and graceful fallback."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.ml import features as F
from src.ml import live
from src.ml import model as M


def _synthetic(n=450, seed=0) -> pd.DataFrame:
    """Three separable regimes: persistent+facility, crop-season brief, forest brief; plus noise features."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        cls = M.CLASSES[i % 3]
        r = {c: np.nan for c in F.ALL_FEATURES}
        r.update(n_detections=rng.integers(1, 30), n_days=1, frp_max=rng.gamma(2, 3), frp_mean=2.0, frp_total=10.0, frp_p90=4.0,
                 night_fraction=0.0, ti4_mean=330.0, ti4_max=340.0, ti_diff_mean=40.0, scan_mean=0.4, track_mean=0.4, extent_m=500.0,
                 high_conf_fraction=0.1, n_satellites=1, baseline_active_fraction=0.0, frp_z=2.0, facility_dist_m=20000.0,
                 facility_within=0.0, facility_source_count=1, facility_radius_m=1000.0, authoritative_dist_m=30000.0,
                 lc_crop=0.1, lc_woody=0.1, lc_vegetation=0.2, lc_built=0.05, lc_bare=0.05, lc_water=0.0,
                 month_sin=rng.normal(), month_cos=rng.normal(), lat_abs=rng.uniform(10, 30))
        for k in F.KIND_NAMES:
            r[f"kind_{k}"] = 0.0
        if cls == "industrial_persistent":
            r.update(n_days=6, night_fraction=0.95, baseline_active_fraction=0.6, facility_dist_m=150.0, facility_within=1.0,
                     authoritative_dist_m=150.0, kind_power_thermal=1.0, frp_z=0.2)
        elif cls == "agricultural_burning":
            r.update(lc_crop=0.9, month_sin=-0.9, night_fraction=0.0)
        else:
            r.update(lc_woody=0.95, lc_vegetation=0.97, extent_m=2500.0)
        r.update(label=cls, state=f"S{i % 8}", cell_key=f"c{i}")
        rows.append(r)
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def trained():
    df = _synthetic()
    return M.train(df, F.ALL_FEATURES, seed=1, max_iter=60), df


def test_model_separates_the_regimes_and_probabilities_sum_to_one(trained):
    model, df = trained
    proba = model.predict_proba(df)
    assert np.allclose(proba.sum(axis=1), 1.0)
    assert (model.predict(df) == df.label.to_numpy()).mean() > 0.95
    assert model.version.startswith("thermal-gbm-")


def test_calibration_regions_are_disjoint_from_the_fit_rows(trained):
    model, df = trained
    cal = set(model.meta["calibration_regions"])
    assert cal and cal < set(df.state)
    assert model.meta["n_fit"] + model.meta["n_calibration"] == len(df)


def test_reasons_name_real_features_and_the_top_one_matters(trained):
    model, df = trained
    row = df[df.label == "industrial_persistent"].iloc[0]
    e = model.explain(row, top=5, n_permutations=12)
    assert e["class"] == "industrial_persistent" and 0 < e["probability"] <= 1
    assert 1 <= len(e["reasons"]) <= 5
    assert all(r["feature"] in model.features and "supports" in r["text"] or "argues against" in r["text"] for r in e["reasons"])
    # the persistent regime is defined by facility context / persistence: one of those must lead
    assert e["reasons"][0]["feature"] in {"facility_dist_m", "facility_within", "authoritative_dist_m", "n_days", "night_fraction",
                                          "baseline_active_fraction", "kind_power_thermal", "frp_z"}


def test_model_round_trips_through_disk(trained, tmp_path):
    model, df = trained
    model.save(tmp_path / "m.joblib")
    again = M.ThermalModel.load(tmp_path / "m.joblib")
    assert again.version == model.version
    assert np.allclose(again.predict_proba(df.head(20)), model.predict_proba(df.head(20)))


def test_aggregate_units_matches_hand_computation():
    ts = pd.Timestamp("2026-05-01 20:00", tz="UTC")
    det = pd.DataFrame({"latitude": [21.0, 21.002], "longitude": [83.0, 83.0], "frp": [4.0, 12.0],
                        "acq_datetime": [ts, ts + pd.Timedelta(days=1)], "daynight": ["N", "D"],
                        "instrument": ["VIIRS_SNPP_SP", "VIIRS_NOAA20_SP"], "bright_ti4": [330.0, 350.0], "bright_ti5": [290.0, 300.0],
                        "scan": [0.4, 0.5], "track": [0.4, 0.5], "confidence": ["nominal", "high"], "u": ["a", "a"]})
    u = F.aggregate_units(det, "u").iloc[0]
    assert u.n_detections == 2 and u.n_days == 2 and u.frp_max == 12.0 and u.frp_total == 16.0
    assert u.night_fraction == 0.5 and u.ti_diff_mean == 45.0 and u.n_satellites == 2 and u.high_conf_fraction == 0.5
    assert 200 < u.extent_m < 250


def test_blind_group_has_no_place_knowledge():
    for forbidden in F.FACILITY + F.LANDCOVER + F.SEASON_REGION + F.BASELINE:
        assert forbidden not in F.BLIND
    assert set(F.ALL_FEATURES) == set(F.THERMAL + F.BASELINE + F.FACILITY + F.LANDCOVER + F.SEASON_REGION)


def test_live_scoring_falls_back_to_rules_only_without_a_model():
    src = pd.DataFrame({"cluster": [0], "lat": [21.0], "lon": [83.0], "last_seen": [pd.Timestamp("2026-10-01", tz="UTC")]})
    out = live.score_sources(src, pd.DataFrame(), None, None)
    assert out.model_class.isna().all() and out.model_version.isna().all()
    assert list(out.columns)[: len(src.columns)] == list(src.columns)


def test_live_scoring_adds_model_fields(trained):
    model, df = trained
    fac = pd.DataFrame([{"facility_id": "f1", "name": "Plant", "kind": "power_thermal", "lat": 21.0, "lon": 83.0, "radius_m": 1500.0,
                         "source_count": 2, "authoritative": True}])
    ts = pd.Timestamp("2026-10-10 20:00", tz="UTC")
    det = pd.DataFrame({"cluster": [0, 0], "latitude": [21.0, 21.0], "longitude": [83.0, 83.0], "frp": [5.0, 6.0],
                        "acq_datetime": [ts, ts + pd.Timedelta(days=1)], "daynight": ["N", "N"],
                        "instrument": ["VIIRS_SNPP_NRT"] * 2, "bright_ti4": [330.0, 331.0], "bright_ti5": [290.0, 290.0],
                        "confidence": ["nominal", "nominal"]})
    src = pd.DataFrame({"cluster": [0], "lat": [21.0], "lon": [83.0], "last_seen": [ts], "baseline_active_fraction": [0.6], "frp_z": [0.1],
                        "lc_crop": [0.05], "lc_woody": [0.05], "lc_vegetation": [0.1], "lc_built": [0.3], "lc_bare": [0.2], "lc_water": [0.0]})
    out = live.score_sources(src, det, model, fac, n_permutations=4).iloc[0]
    assert out.model_class in model.classes and 0 < out.model_probability <= 1
    assert abs(sum(out.model_probabilities.values()) - 1) < 1e-6 and out.model_version == model.version


def test_false_alarm_threshold_never_splits_a_tie():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("evaluate", Path(__file__).resolve().parents[2] / "scripts" / "evaluate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    scores = np.array([0.9, 0.5, 0.5, 0.5, 0.1])
    thr = mod.threshold_for_false_alarms(scores, 2)          # k=2 but three scores tie at 0.5
    assert (scores >= thr).sum() <= 2 and thr > 0.5
    assert mod.threshold_for_false_alarms(scores, 4) <= 0.5 and (scores >= mod.threshold_for_false_alarms(scores, 4)).sum() == 4
    assert mod.threshold_for_false_alarms(np.array([]), 3) == 1.0
