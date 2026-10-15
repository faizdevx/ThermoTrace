"""The SQL view `thermal_baseline` and src/thermal/baseline.py must produce the same numbers.

Loads one synthetic archive (persistent flare, December-only kiln, a bare cell, SP/NRT duplicates, a
low-confidence pixel, an archive gap, detections inside the excluded last 14 days) through the real loader,
then compares every entity of the materialised view with the pandas computation.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
import pytest

from src.thermal import baseline as bl
from src.thermal.sites import SiteIndex

from .conftest import ADMIN_URL

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set (needs PostGIS)")

ANCHOR = date(2026, 3, 31)
FLARE, KILN, BARE = (21.0, 83.0), (22.0, 84.0), (23.0, 85.0)
FACILITIES = [("F-FLARE", "Flare", "gas_flare", FLARE, 800.0), ("F-KILN", "Kiln", "brick_kiln", KILN, 800.0)]


def _row(lat, lon, day, frp, instrument="VIIRS_SNPP_SP", hour=1, conf="nominal", dn="N"):
    return {"latitude": lat, "longitude": lon, "frp": frp, "bright_ti4": 330.0, "bright_ti5": 290.0,
            "acq_datetime": pd.Timestamp(f"{day.isoformat()} {hour:02d}:30", tz="UTC"),
            "confidence": conf, "daynight": dn, "instrument": instrument}


def _archive() -> pd.DataFrame:
    rows, gap = [], {date(2025, 6, 1) + timedelta(days=i) for i in range(5)}
    day = date(2025, 3, 18)
    while day <= ANCHOR:
        if day not in gap:
            rows.append(_row(*FLARE, day, 4.0))
            rows.append(_row(*FLARE, day, 6.0, "VIIRS_NOAA20_SP", hour=2))
        if day.month == 12 and day.day <= 10:
            rows.append(_row(*KILN, day, 2.0))
        if date(2025, 8, 1) <= day < date(2025, 8, 11):
            rows.append(_row(*BARE, day, 10.0, dn="D", hour=13))
        day += timedelta(days=1)
    rows.append(_row(*BARE, date(2025, 8, 1), 10.0, "VIIRS_SNPP_NRT", dn="D", hour=13))     # duplicate of an SP row
    rows.append(_row(BARE[0] + 0.002, BARE[1], date(2025, 8, 2), 99.0, conf="low", dn="D", hour=13))
    rows.append(_row(*BARE, ANCHOR, 50.0))                                                  # excluded recent day
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def loaded(dsn):
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        for fid, name, kind, (lat, lon), radius in FACILITIES:
            cur.execute("""INSERT INTO facilities (facility_id, name, kind, geometry, radius_m, primary_source,
                               source_count, sources, attribution)
                           VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, 'osm', 1, 'osm', 'x')""",
                        (fid, name, kind, lon, lat, radius))
    det = _archive()
    report = bl.load_to_postgis(dsn, det)
    return det, report


def test_loader_is_idempotent_and_assigns_every_detection(dsn, loaded):
    det, report = loaded
    assert report["inserted"] == len(det) and report["geo_assigned"] == len(det)
    again = bl.load_to_postgis(dsn, det)
    assert again["inserted"] == 0 and again["geo_assigned"] == 0


def test_generated_columns_split_the_instrument(dsn, loaded):
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT satellite, product, count(*) FROM thermal_detections GROUP BY 1, 2 ORDER BY 1, 2")
        got = {(s, p): n for s, p, n in cur.fetchall()}
    assert set(got) == {("SNPP", "SP"), ("SNPP", "NRT"), ("NOAA20", "SP")}


def test_sql_view_matches_pandas_for_every_entity(dsn, loaded):
    det, _ = loaded
    sites = SiteIndex(pd.DataFrame([{"ref": f, "name": n, "kind": k, "lat": ll[0], "lon": ll[1], "radius_m": r}
                                    for f, n, k, ll, r in FACILITIES]))
    py = bl.BaselineIndex.from_detections(det, sites).frame.set_index(["scope", "key"]).sort_index()
    sql = bl.BaselineIndex.from_postgis(dsn).frame.set_index(["scope", "key"]).sort_index()

    assert set(py.index) == set(sql.index) and len(py) >= 3
    for col in ("active_days", "covered_days", "n_detections"):
        assert (py[col].astype(int) == sql[col].astype(int)).all(), col
    for col in ("active_fraction", "frp_median", "frp_p95", "night_share", "daily_frp_mean", "daily_frp_std"):
        assert np.allclose(py[col].astype(float), sql[col].astype(float), atol=1e-6, equal_nan=True), col
    for key in py.index:
        a = [np.nan if v is None else v for v in py.loc[key, "monthly_profile"]]
        b = [np.nan if v is None else v for v in sql.loc[key, "monthly_profile"]]
        assert np.allclose(a, b, atol=1e-4, equal_nan=True), key
    assert (py.window_end == sql.window_end).all() and (py.window_start == sql.window_start).all()
    assert sql.loc[("facility", "F-FLARE"), "covered_days"] == 360


def test_persisted_source_carries_baseline_columns(dsn, loaded):
    """thermal_sources has the columns the worker writes (migration 006) and they accept a full row."""
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""SELECT column_name FROM information_schema.columns WHERE table_name = 'thermal_sources'
                       AND column_name LIKE 'baseline_%' OR column_name IN ('frp_today', 'frp_today_date', 'frp_z')""")
        cols = {r[0] for r in cur.fetchall()}
    assert {"baseline_scope", "baseline_active_fraction", "baseline_monthly", "frp_today", "frp_z"} <= cols
