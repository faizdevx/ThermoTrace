"""Deterministic tests for the FIRMS thermal pipeline (no network, no database)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.thermal import classifier, firms, sources
from src.thermal.sites import KINDS, SiteIndex, haversine_m, kind_from_tags

CSV = """latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight
23.8000,86.4000,340.1,0.4,0.4,2026-10-02,0705,N,nominal,2.0NRT,300.0,12.5,N
23.8001,86.4001,341.0,0.4,0.4,2026-10-02,0705,N,nominal,2.0NRT,301.0,12.5,N
13.0,96.8,335.0,0.3,0.5,2026-10-02,627,N,h,2.0NRT,290.0,2,D
"""


def det_rows(points, days, frp=10.0, night=True, instrument="VIIRS_SNPP_NRT"):
    rows = []
    for lat, lon in points:
        for d in days:
            rows.append({"latitude": lat, "longitude": lon, "bright_ti4": 340.0, "bright_ti5": 300.0, "frp": frp,
                         "acq_datetime": pd.Timestamp(f"2026-10-{d:02d} 21:00", tz="UTC") if night
                         else pd.Timestamp(f"2026-10-{d:02d} 08:00", tz="UTC"),
                         "confidence": "nominal", "daynight": "N" if night else "D", "instrument": instrument,
                         "satellite": "N", "version": "2.0NRT"})
    df = pd.DataFrame(rows)
    df["detection_id"] = [f"{i}" for i in range(len(df))]
    return df


# ── FIRMS parsing ─────────────────────────────────────────────────────────────
def test_parse_csv_normalises_columns_and_ids():
    duplicated = CSV + "23.8000,86.4000,340.1,0.4,0.4,2026-10-02,0705,N,nominal,2.0NRT,300.0,12.5,N\n"
    df = firms.parse_csv(duplicated, "VIIRS_SNPP_NRT")
    assert len(df) == 3                                   # three distinct pixels; the exact duplicate row is dropped
    assert df.acq_datetime.dt.tz is not None
    assert df.loc[df.latitude == 13.0, "acq_datetime"].iloc[0].hour == 6    # '627' padded to 0627
    assert set(df.confidence) <= {"low", "nominal", "high"} and "high" in set(df.confidence)
    assert df.detection_id.is_unique and df.detection_id.str.len().eq(20).all()
    again = firms.parse_csv(CSV, "VIIRS_SNPP_NRT")
    assert list(again.detection_id) == list(df.detection_id)              # stable ids -> idempotent loads


def test_parse_rejects_error_pages():
    with pytest.raises(firms.FirmsUnavailable):
        firms.parse_csv("<html>Service unavailable</html>", "VIIRS_SNPP_NRT")


def test_public_urls_use_the_correct_host():
    url = firms.public_url("VIIRS_NOAA20_NRT", firms.FirmsConfig())
    assert url == ("https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/"
                   "J1_VIIRS_C2_South_Asia_7d.csv")


def test_api_key_is_never_logged_in_urls(caplog):
    cfg = firms.FirmsConfig(map_key="SECRET123", products=("VIIRS_SNPP_NRT",))

    class Boom:
        def get(self, *a, **k):
            raise firms.requests.ConnectionError("x")
    with pytest.raises(firms.FirmsUnavailable):
        firms.fetch(cfg, Boom())
    assert "SECRET123" not in caplog.text


# ── clustering / features ─────────────────────────────────────────────────────
def test_close_detections_merge_far_ones_do_not():
    det = det_rows([(23.80, 86.40), (23.804, 86.404), (24.5, 87.0)], [2, 3])
    c = sources.cluster_detections(det)
    assert c.cluster.nunique() == 2
    s = sources.summarise(c, 7)
    big = s.sort_values("n_detections").iloc[-1]
    assert big.n_detections == 4 and big.n_days == 2 and big.extent_m < 1000 and big.night_fraction == 1.0


# ── site kinds ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("tags,expected", [
    ({"man_made": "flare"}, "gas_flare"),
    ({"power": "plant", "plant:source": "coal"}, "power_thermal"),
    ({"power": "plant", "plant:source": "solar"}, None),
    ({"power": "generator", "generator:source": "wind"}, None),
    ({"industrial": "refinery"}, "refinery_petrochem"),
    ({"industrial": "steel"}, "steel_metal"),
    ({"landuse": "quarry"}, "mining_quarry"),
    ({"landuse": "landfill"}, "landfill"),
    ({"man_made": "works"}, "factory_generic"),
    ({"shop": "bakery"}, None),
])
def test_kind_from_tags(tags, expected):
    assert kind_from_tags(tags) == expected


def make_index(kind="steel_metal", lat=23.8, lon=86.4, radius=None):
    return SiteIndex(pd.DataFrame([{"ref": "way/1", "name": "Test Steel", "kind": kind, "lat": lat, "lon": lon,
                                    "radius_m": radius or KINDS[kind].radius_m}]))


def test_site_match_within_radius_and_context_when_outside():
    idx = make_index()
    inside, outside = idx.match(np.array([23.8, 23.9]), np.array([86.4, 86.4]))
    assert inside.within and inside.site_ref == "way/1" and inside.continuous_prior == KINDS["steel_metal"].continuous
    assert not outside.within and outside.continuous_prior == 0.0 and outside.distance_m > 10_000
    assert haversine_m(23.8, 86.4, 23.9, 86.4) == pytest.approx(11_119, rel=0.01)


def analyse_one(points, days, *, frp=10.0, night=True, index=None, month_days=None, **lc):
    det = sources.cluster_detections(det_rows(points, days, frp, night))
    src = sources.summarise(det, 7)
    if index is not None:
        m = index.match(src.lat.to_numpy(), src.lon.to_numpy())
        src["site_ref"], src["site_name"], src["site_kind"] = [x.site_ref for x in m], [x.site_name for x in m], [x.kind for x in m]
        src["site_within"], src["site_continuous"] = [x.within for x in m], [x.continuous_prior for x in m]
    else:
        src["site_ref"] = src["site_name"] = src["site_kind"] = None
        src["site_within"], src["site_continuous"] = False, 0.0
    for k in ("vegetation", "crop", "built", "bare", "water"):
        src[f"lc_{k}"] = lc.get(k, np.nan)
    src["lc_dominant"] = lc.get("dominant", None)
    out = classifier.classify_sources(src, month=lc.get("month", 10))
    return out.iloc[0]


# ── classification behaviour ──────────────────────────────────────────────────
def test_persistent_night_hotspot_on_steel_works_is_industrial_persistent():
    r = analyse_one([(23.8, 86.4)], [2, 3, 4, 5, 6, 7], index=make_index(), built=0.7, dominant="built")
    assert r.thermal_class == "industrial_persistent" and r.class_confidence in ("high", "medium")
    assert any("steel" in x or "Test Steel" in x for x in r.reasons) and any("night" in x for x in r.reasons)
    assert r.alert_level is None


def test_short_lived_high_frp_event_at_a_facility_is_a_fire_candidate_with_alert():
    r = analyse_one([(23.8, 86.4)], [7], frp=120.0, night=False, index=make_index("refinery_petrochem"), built=0.5, dominant="built")
    assert r.thermal_class == "industrial_fire_candidate"
    assert r.alert_level == "critical" and "verify" in r.alert_text


def test_cropland_brief_daytime_small_fire_is_agricultural_burning():
    r = analyse_one([(30.9, 75.2)], [3], frp=4.0, night=False, crop=0.92, vegetation=0.03, dominant="crop", month=10)
    assert r.thermal_class == "agricultural_burning"
    assert any("cropland" in x for x in r.reasons)


def test_forest_fire_is_vegetation_fire():
    r = analyse_one([(27.0, 93.5)], [3, 4], frp=35.0, night=False, vegetation=0.98, dominant="tree", month=3)
    assert r.thermal_class == "vegetation_fire"


def test_persistent_source_without_mapped_facility_is_flagged_for_review():
    r = analyse_one([(21.0, 82.0)], [2, 3, 4, 5, 6], index=None, bare=0.6, built=0.3, dominant="bare")
    assert r.thermal_class == "unmapped_persistent"


def test_no_land_cover_does_not_invent_agriculture_or_forest():
    r = analyse_one([(20.0, 80.0)], [3], frp=3.0, night=False)
    assert r.thermal_class == "other"


def test_crop_fire_near_a_facility_prefers_industrial_over_agriculture():
    r = analyse_one([(23.8, 86.4)], [3], frp=20.0, night=False, index=make_index(), crop=0.8, dominant="crop")
    assert r.thermal_class in ("industrial_fire_candidate", "industrial_persistent")


def test_scores_form_a_distribution_and_reasons_are_present():
    r = analyse_one([(23.8, 86.4)], [2, 3, 4], index=make_index(), built=0.5, dominant="built")
    assert sum(r.class_scores.values()) == pytest.approx(1.0) and set(r.class_scores) == set(classifier.CLASSES)
    assert r.reasons and 0 <= r.class_margin <= 1


# ── real-time behaviour: publish only on new data ─────────────────────────────
def test_refresh_publishes_only_when_firms_has_new_detections(tmp_path, monkeypatch):
    from src.thermal import cli, store

    fresh = det_rows([(23.8, 86.4)], [2, 3, 4])
    fresh["bright_ti4"], fresh["detection_id"] = 340.0, [f"id{i}" for i in range(len(fresh))]
    fresh.attrs["published_at"] = None
    calls = {"analyse": 0}

    def fake_analyse(det, index, **kw):
        calls["analyse"] += 1
        src = sources.summarise(sources.cluster_detections(det), 7)
        for col, val in (("site_ref", None), ("site_name", None), ("site_kind", None), ("site_distance_m", np.nan),
                         ("site_within", False), ("site_continuous", 0.0), ("lc_vegetation", np.nan), ("lc_crop", np.nan),
                         ("lc_built", np.nan), ("lc_bare", np.nan), ("lc_water", np.nan), ("lc_dominant", None)):
            src[col] = val
        from src.thermal import classifier, pipeline
        src = classifier.classify_sources(src, 10)
        src["state"], src["source_id"] = None, [f"ts-{i}" for i in src.cluster]
        det = sources.cluster_detections(det).merge(src[["cluster", "source_id", "thermal_class"]], on="cluster")
        return pipeline.Analysis(det, src, {"sources": len(src)})

    monkeypatch.setattr(cli.firms, "fetch", lambda cfg=None: fresh.copy())
    monkeypatch.setattr(cli.pipeline, "analyse", fake_analyse)
    monkeypatch.setattr(cli, "load_sites", lambda: None)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    first = cli.refresh_once(tmp_path, 14, use_landcover=False, interval_seconds=600)
    assert calls["analyse"] == 1 and first["new_detections"] == len(fresh) and first["last_change_at"]
    change_stamp = first["last_change_at"]
    mtime = (tmp_path / store.SNAPSHOT_FILE).stat().st_mtime

    second = cli.refresh_once(tmp_path, 14, use_landcover=False, interval_seconds=600)   # same FIRMS content
    assert calls["analyse"] == 1                                         # nothing re-classified
    assert second["new_detections"] == 0 and second["last_change_at"] == change_stamp   # data did not change ...
    assert second["last_check_at"] >= first["last_check_at"]                            # ... but it WAS checked
    assert (tmp_path / store.SNAPSHOT_FILE).stat().st_mtime == mtime     # snapshot untouched -> no client wake-up

    extra = det_rows([(23.8, 86.4)], [5])
    extra["detection_id"], extra["bright_ti4"] = "newone", 341.0
    monkeypatch.setattr(cli.firms, "fetch", lambda cfg=None: pd.concat([fresh, extra], ignore_index=True))
    third = cli.refresh_once(tmp_path, 14, use_landcover=False, interval_seconds=600)
    assert calls["analyse"] == 2 and third["new_detections"] == 1 and third["last_change_at"] != change_stamp


def test_firms_failure_keeps_last_good_snapshot_and_counts_failures(tmp_path, monkeypatch):
    from src.thermal import cli, store
    (tmp_path / store.SNAPSHOT_FILE).write_text('{"features": []}')
    monkeypatch.setattr(cli.firms, "fetch", lambda cfg=None: (_ for _ in ()).throw(firms.FirmsUnavailable("down")))
    for n in (1, 2):
        with pytest.raises(firms.FirmsUnavailable):
            cli.refresh_once(tmp_path, 14, use_landcover=False)
        assert store.read_status(tmp_path)["consecutive_failures"] == n
    assert (tmp_path / store.SNAPSHOT_FILE).read_text() == '{"features": []}'
