"""Baseline: window rule, covered days, SP/NRT dedupe, low-confidence drop, monthly profile, z-score."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.thermal import baseline as bl
from src.thermal.sites import SiteIndex

ANCHOR = date(2026, 3, 31)           # window = 2025-03-18 .. 2026-03-17 (365 days, ends 14 days before)
FAC = (21.0, 83.0)                    # persistent flare inside a facility
SEASONAL = (22.0, 84.0)               # only active in December
BARE = (23.0, 85.0)                   # no facility: cell baseline


def _det(lat, lon, day, frp=5.0, instrument="VIIRS_SNPP_SP", hour=1, conf="nominal", dn="N"):
    ts = pd.Timestamp(f"{day.isoformat()} {hour:02d}:30", tz="UTC")
    return {"latitude": lat, "longitude": lon, "acq_datetime": ts, "frp": frp, "bright_ti4": 330.0, "bright_ti5": 290.0,
            "confidence": conf, "daynight": dn, "instrument": instrument}


def _sites() -> SiteIndex:
    return SiteIndex(pd.DataFrame([
        {"ref": "F-FLARE", "name": "Flare", "kind": "gas_flare", "lat": FAC[0], "lon": FAC[1], "radius_m": 800.0},
        {"ref": "F-SEASON", "name": "Kiln", "kind": "brick_kiln", "lat": SEASONAL[0], "lon": SEASONAL[1], "radius_m": 800.0},
    ]))


def _history() -> pd.DataFrame:
    rows = []
    start, end = date(2025, 3, 18), date(2026, 3, 31)
    gap = {date(2025, 6, 1) + timedelta(days=i) for i in range(5)}      # 5 days the archive does not hold
    day = start
    while day <= end:
        if day not in gap:
            # persistent facility source: both satellites every day, 4 MW at night
            rows.append(_det(*FAC, day, 4.0, "VIIRS_SNPP_SP"))
            rows.append(_det(*FAC, day, 6.0, "VIIRS_NOAA20_SP", hour=2))
        if day.month == 12 and day.day <= 10:
            rows.append(_det(*SEASONAL, day, 2.0))
        if date(2025, 8, 1) <= day < date(2025, 8, 11):
            rows.append(_det(*BARE, day, 10.0, dn="D", hour=13))
        day += timedelta(days=1)
    # one physical detection held twice (SP and NRT) and one low-confidence pixel on a bare-cell day
    rows.append(_det(*BARE, date(2025, 8, 1), 10.0, "VIIRS_SNPP_NRT", dn="D", hour=13))
    rows.append(_det(*BARE, date(2025, 8, 2), 99.0, conf="low", dn="D", hour=13))
    # inside the excluded last 14 days: must not enter the baseline
    rows.append(_det(*BARE, ANCHOR, 50.0))
    return pd.DataFrame(rows)


def test_window_ends_14_days_before_newest_detection():
    start, end = bl.window_bounds(_history())
    assert end == ANCHOR - timedelta(days=14) and (end - start).days == 364


def test_physical_drops_low_confidence_and_prefers_sp():
    d = pd.DataFrame([
        _det(*BARE, date(2025, 8, 1), 10.0, "VIIRS_SNPP_SP", dn="D", hour=13),
        _det(*BARE, date(2025, 8, 1), 10.0, "VIIRS_SNPP_NRT", dn="D", hour=13),
        _det(*BARE, date(2025, 8, 2), 99.0, conf="low", dn="D", hour=13),
        _det(*BARE, date(2025, 8, 1), 7.0, "VIIRS_NOAA20_NRT", dn="D", hour=13),    # other satellite: kept
    ])
    out = bl.physical(d)
    assert len(out) == 2 and set(out.instrument) == {"VIIRS_SNPP_SP", "VIIRS_NOAA20_NRT"}


@pytest.fixture(scope="module")
def table() -> pd.DataFrame:
    t = bl.compute_baseline(bl.assign_geo(_history(), _sites()))
    return t.set_index(["scope", "key"])


def test_covered_days_exclude_archive_gaps(table):
    f = table.loc[("facility", "F-FLARE")]
    assert f.covered_days == 360 and f.active_days == 360 and f.active_fraction == 1.0   # gap is not "quiet"


def test_facility_numbers(table):
    f = table.loc[("facility", "F-FLARE")]
    assert f.frp_median == 5.0 and f.n_detections == 720
    assert f.frp_p95 == pytest.approx(6.0)
    assert f.night_share == 1.0
    assert f.daily_frp_mean == pytest.approx(10.0)          # 4 + 6 MW every covered day
    assert f.daily_frp_std == pytest.approx(0.0, abs=1e-9)


def test_monthly_profile_separates_seasonal_source(table):
    k = table.loc[("facility", "F-SEASON")]
    prof = k.monthly_profile
    assert len(prof) == 12 and prof[11] == pytest.approx(10 / 31, abs=1e-4)      # December: 10 of 31 days
    assert all(v == 0 for i, v in enumerate(prof) if i != 11 and v is not None)
    assert k.active_fraction == pytest.approx(10 / 360)


def test_bare_cell_baseline_ignores_duplicates_low_confidence_and_excluded_days(table):
    cell = bl.cell_keys([BARE[0]], [BARE[1]])[0]
    c = table.loc[("cell", cell)]
    assert c.n_detections == 10 and c.active_days == 10        # SP/NRT pair once, low-confidence and recent dropped
    assert c.frp_median == 10.0 and c.night_share == 0.0
    assert c.daily_frp_mean == pytest.approx(10 * 10.0 / 360)


def _sources(lat, lon, **kw):
    base = {"cluster": 0, "lat": lat, "lon": lon, "site_ref": None, "site_within": False}
    return pd.DataFrame([{**base, **kw}])


def test_attach_z_score_for_persistent_and_new_sources():
    index = bl.BaselineIndex.from_detections(_history(), _sites())
    # live window: the flare is at its usual 10 MW/day, a bare cell suddenly burns 30 MW
    live = pd.DataFrame([
        {**_det(*FAC, ANCHOR, 4.0), "cluster": 0}, {**_det(*FAC, ANCHOR, 6.0, "VIIRS_NOAA20_NRT", hour=2), "cluster": 0},
        {**_det(23.5, 85.5, ANCHOR, 30.0), "cluster": 1},
    ])
    src = pd.concat([_sources(*FAC, site_ref="F-FLARE", site_within=True),
                     _sources(23.5, 85.5, cluster=1)], ignore_index=True)
    out = bl.attach(src, live, index, _sites()).set_index("cluster")

    flare = out.loc[0]
    assert flare.baseline_scope == "facility" and flare.baseline_known
    assert flare.baseline_active_fraction == 1.0 and flare.frp_today == pytest.approx(10.0)
    assert flare.frp_z == pytest.approx(0.0, abs=0.01)           # business as usual

    fresh = out.loc[1]
    assert fresh.baseline_scope == "cell" and not fresh.baseline_known
    assert fresh.baseline_active_fraction == 0.0 and fresh.baseline_covered_days == 360
    assert fresh.frp_today == pytest.approx(30.0) and fresh.frp_z == pytest.approx(30.0)   # std floor 1 MW


def test_attach_without_baseline_leaves_fields_empty():
    out = bl.attach(_sources(*FAC), pd.DataFrame(), None, None)
    assert out.baseline_known.tolist() == [False] and out.frp_z.isna().all()


def test_index_roundtrips_through_parquet(tmp_path):
    index = bl.BaselineIndex.from_detections(_history(), _sites())
    index.save(tmp_path)
    again = bl.BaselineIndex.load(tmp_path)
    assert len(again) == len(index) and again.covered_days == 360
    row = again.get("facility", "F-SEASON")
    assert list(row.monthly_profile)[11] == pytest.approx(10 / 31, abs=1e-4)
    assert np.isclose(again.get("facility", "F-FLARE").daily_frp_mean, 10.0)


def test_store_text_helper_never_writes_nan():
    from src.thermal.store import _s
    assert _s(float("nan")) is None and _s(None) is None and _s("watch") == "watch"
