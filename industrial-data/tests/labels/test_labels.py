"""Label rules, region/time split and the manifest: deterministic, no leakage across the split."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.labels import build as B
from src.labels import rules as R


def _units(n=40, state="Punjab", month=11, **over):
    base = {"cell_key": [f"c{i}" for i in range(n)], "lat": 30.9, "lon": 75.5, "state": state,
            "window_start": date(2025, month, 3), "window_month": month,
            "distance_to_any_facility_m": 9000.0, "distance_to_authoritative_facility_m": 9000.0,
            "facility_baseline_active_fraction": 0.0, "cell_baseline_active_fraction": 0.0, "lc_crop": 0.9, "lc_woody": 0.0}
    base.update(over)
    return pd.DataFrame(base)


def test_state_names_with_diacritics_still_match_the_agri_belt():
    s = pd.Series(["Haryāna", "Punjab", "Uttar Pradesh", "Uttar Pradesh", "Bihār"])
    lon = pd.Series([76.0, 75.0, 78.0, 82.0, 85.0])
    assert B.in_agri_region(s, lon).tolist() == [True, True, True, False, False]   # east UP is not "western UP"


def test_rules_are_exclusive_and_use_the_documented_thresholds():
    u = pd.concat([
        _units(1),                                                                                   # crop, Punjab, Nov
        _units(1, state="Odisha", lc_crop=0.0, lc_woody=0.95, month=3),                              # forest
        _units(1, distance_to_authoritative_facility_m=400.0, facility_baseline_active_fraction=0.5,
               lc_crop=0.0, distance_to_any_facility_m=400.0),                                       # persistent
        _units(1, distance_to_authoritative_facility_m=400.0, facility_baseline_active_fraction=0.1,
               lc_crop=0.0, distance_to_any_facility_m=400.0),                                       # facility but only 10 % active
        _units(1, month=7),                                                                          # crop but monsoon
        _units(1, distance_to_any_facility_m=2000.0),                                                # crop but a plant 2 km away
        _units(1, cell_baseline_active_fraction=0.30),                                               # crop but not episodic
    ], ignore_index=True)
    out = B.apply_rules(u)
    cls = out[["is_industrial_persistent", "is_agricultural_burning", "is_vegetation_fire"]]
    assert cls.sum(axis=1).max() <= 1
    assert cls.idxmax(axis=1).tolist()[:3] == ["is_agricultural_burning", "is_vegetation_fire", "is_industrial_persistent"]
    assert cls.iloc[3:].sum().sum() == 0            # every near-miss stays unlabelled


def test_split_never_mixes_regions_or_periods():
    u = pd.DataFrame({"state": ["Punjab", "Punjab", "Goa", "Goa"],
                      "window_start": [date(2025, 11, 1), date(2026, 5, 1), date(2025, 11, 1), date(2026, 5, 1)]})
    got = B.assign_split(u, held_out=["Goa"]).tolist()
    assert got == ["train", "unused", "unused", "test"]


def test_held_out_choice_gives_every_class_train_and_test_support():
    rows = []
    for state, cls_counts in {"A": (400, 0, 0), "B": (0, 400, 0), "C": (0, 0, 400), "D": (200, 200, 200), "E": (200, 200, 200)}.items():
        for k, (flag, n) in enumerate(zip(("is_industrial_persistent", "is_agricultural_burning", "is_vegetation_fire"), cls_counts)):
            for j in range(n):
                rows.append({"state": state, "window_start": date(2025, 11, 1) if j % 2 == 0 else date(2026, 5, 1),
                             "is_industrial_persistent": flag == "is_industrial_persistent",
                             "is_agricultural_burning": flag == "is_agricultural_burning",
                             "is_vegetation_fire": flag == "is_vegetation_fire"})
    held = B.choose_held_out(pd.DataFrame(rows), min_support=100)
    assert held and len(held) < 5
    again = B.choose_held_out(pd.DataFrame(rows), min_support=100)
    assert held == again                           # deterministic


def test_units_aggregate_per_cell_and_window():
    ts = pd.Timestamp("2025-10-02 01:30", tz="UTC")
    det = pd.DataFrame({
        "latitude": [21.0, 21.0005, 21.0], "longitude": [83.0, 83.0, 83.0], "frp": [3.0, 9.0, 4.0],
        "acq_datetime": [ts, ts + pd.Timedelta(hours=12), ts + pd.Timedelta(days=9)],
        "daynight": ["N", "D", "N"], "confidence": ["nominal"] * 3, "instrument": ["VIIRS_SNPP_SP"] * 3,
        "bright_ti4": [330.0] * 3, "bright_ti5": [290.0] * 3, "cell_key": ["1_1", "1_1", "1_1"]})
    u = B.build_units(det).sort_values("window").reset_index(drop=True)
    assert len(u) == 2 and u.n_detections.tolist() == [2, 1] and u.frp_max.tolist() == [9.0, 4.0]
    assert u.window_start.tolist() == [date(2025, 10, 1), date(2025, 10, 8)]
    assert u.night_fraction.iloc[0] == 0.5


def test_rule_text_lists_inputs_for_every_class():
    assert set(R.RULES) == set(R.CLASSES)
    for c in R.CLASSES:
        assert R.RULES[c]["inputs"] and R.forbidden_inputs(c) == R.RULES[c]["inputs"]


def test_held_out_choice_counts_persistent_support_after_the_facility_cap():
    rows = []
    # state A: one plant with 400 windows (caps to 30); state B: 20 plants x 10 windows (200 after the cap)
    for state, plants, per in (("A", 1, 400), ("B", 20, 10), ("C", 20, 10)):
        for f in range(plants):
            for j in range(per):
                rows.append({"state": state, "authoritative_facility_id": f"{state}{f}",
                             "window_start": date(2025, 11, 1) if j % 2 == 0 else date(2026, 5, 1),
                             "is_industrial_persistent": True, "is_agricultural_burning": False, "is_vegetation_fire": False})
    held = B.choose_held_out(pd.DataFrame(rows), min_support=50, max_per_facility=30)
    assert "A" not in held                 # holding out the single big plant would leave ~15 capped test windows
