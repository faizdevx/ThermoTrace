"""Thermal API contract tests against a small controlled snapshot (no database, no network)."""
from __future__ import annotations

import csv
import io
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import thermal_repo

pytestmark = pytest.mark.skipif(os.getenv("DATA_MODE") != "synthetic", reason="snapshot-backed tests")


def feat(sid, cls, lon, lat, *, frp=10.0, alert=None, state="Odisha", name=None, kind=None, last="2026-10-08T20:00:00+00:00"):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"source_id": sid, "thermal_class": cls, "thermal_label": cls, "class_confidence": "high",
                           "class_margin": 0.6, "class_scores": {cls: 0.9}, "reasons": ["r1"], "alert_level": alert,
                           "alert_text": "verify" if alert else None, "state": state, "site_name": name,
                           "site_kind": kind, "n_detections": 5, "n_days": 3, "frp_max": frp, "frp_mean": frp / 2,
                           "frp_total": frp * 3, "last_seen": last, "first_seen": "2026-10-02T20:00:00+00:00"}}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    feats = [
        feat("a", "industrial_persistent", 85.0, 21.0, frp=30, name="=cmd|' /C calc'!A0", kind="steel_metal"),
        feat("b", "industrial_fire_candidate", 86.0, 22.0, frp=120, alert="critical", state="Jharkhand", kind="refinery_petrochem"),
        feat("c", "agricultural_burning", 75.0, 30.0, frp=3, state="Punjab", last="2026-10-05T10:00:00+00:00"),
        feat("d", "vegetation_fire", 93.0, 27.0, frp=20, state="Arunachal Pradesh"),
    ]
    feats[0]["properties"].update({
        "baseline_scope": "facility", "baseline_known": True, "baseline_active_fraction": 0.71,
        "baseline_covered_days": 365, "baseline_frp_median": 3.2, "baseline_frp_p95": 9.8,
        "baseline_night_share": 0.9, "baseline_monthly": [0.5] * 12, "frp_today": 30.0, "frp_z": 3.1,
        "frp_today_date": "2026-10-08", "baseline_window_end": "2026-09-24",
        "model_version": "thermal-gbm-test", "model_class": "industrial_persistent", "model_probability": 0.97,
        "model_probabilities": {"industrial_persistent": 0.97, "agricultural_burning": 0.02, "vegetation_fire": 0.01},
        "model_reasons": ["distance to the nearest mapped facility 120 m supports a persistent industrial source (+0.31)"]})
    meta = {"summary": {"sources": 4, "alerts": 1, "latest_detection": "2026-10-08T20:00:00+00:00"}, "attribution": ["x"]}
    (tmp_path / "thermal_sources.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats, "metadata": meta}))
    (tmp_path / "thermal_detections.csv").write_text(
        "detection_id,instrument,acq_datetime,latitude,longitude,frp,bright_ti4,confidence,daynight,source_id,thermal_class\n"
        "d1,VIIRS_SNPP_NRT,2026-10-08T20:00:00+00:00,22.0,86.0,120,350,high,N,b,industrial_fire_candidate\n")
    (tmp_path / "status.json").write_text(json.dumps({
        "last_success_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "latest_detection_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 3600))}))
    monkeypatch.setattr(thermal_repo, "_FILE", thermal_repo.FileBackend(tmp_path))
    monkeypatch.setenv("THERMAL_BACKEND", "file")
    return TestClient(app)


def test_list_sorted_by_alert_then_power_with_meta(client):
    r = client.get("/api/thermal/sources").json()
    assert r["meta"] == {"total": 4, "returned": 4, "limit": 2000, "offset": 0, "truncated": False, "backend": "snapshot"}
    assert r["features"][0]["properties"]["source_id"] == "b"          # critical alert first


def test_filters_combine(client):
    ids = lambda q: sorted(f["properties"]["source_id"] for f in client.get(f"/api/thermal/sources?{q}").json()["features"])  # noqa: E731
    assert ids("thermal_class=industrial_persistent,vegetation_fire") == ["a", "d"]
    assert ids("state=punjab") == ["c"]
    assert ids("min_frp=25") == ["a", "b"]
    assert ids("alerts_only=true") == ["b"]
    assert ids("site_kind=steel_metal") == ["a"]
    assert ids("bbox=84,20,87,23") == ["a", "b"]
    assert ids("hours=24") == ["a", "b", "d"]                           # 'c' was last active 3 days earlier
    assert ids("thermal_class=industrial_persistent&min_frp=100") == []


@pytest.mark.parametrize("qs", ["thermal_class=bogus", "bbox=nope", "min_frp=-1", "limit=0", "hours=0"])
def test_bad_filters_are_422(client, qs):
    assert client.get(f"/api/thermal/sources?{qs}").status_code == 422


def test_detail_includes_detection_timeline_and_404(client):
    d = client.get("/api/thermal/sources/b").json()
    assert d["properties"]["detections"][0]["frp"] == 120 and d["properties"]["detections"][0]["daynight"] == "N"
    assert client.get("/api/thermal/sources/zzz").status_code == 404


def test_stats_scope_and_counts(client):
    s = client.get("/api/thermal/stats").json()
    assert s["total_sources"] == 4 and s["alerts"]["critical"] == 1 and s["by_class"]["agricultural_burning"] == 1
    scoped = client.get("/api/thermal/stats?state=Odisha").json()
    assert scoped["total_sources"] == 1 and scoped["scope"]["filters"] == {"state": "Odisha"}
    assert sum(s["by_state"].values()) == 4


def test_status_reports_freshness_separately(client):
    s = client.get("/api/thermal/status").json()
    assert s["available"] and s["freshness"]["state"] == "live"
    assert 3000 < s["freshness"]["latest_detection_age_seconds"] < 4500   # data age != refresh age
    assert s["freshness"]["last_refresh_age_seconds"] < 60


def test_classes_metadata_for_legend(client):
    ids = [c["id"] for c in client.get("/api/thermal/classes").json()["classes"]]
    assert ids[0] == "industrial_persistent" and "unmapped_persistent" in ids


def test_csv_export_is_complete_and_formula_safe(client):
    r = client.get("/api/thermal/export?format=csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 4 == int(r.headers["x-total-count"])
    assert all(not (row["site_name"] or "").startswith("=") for row in rows)
    only = client.get("/api/thermal/export?format=geojson&alerts_only=true")
    assert len(json.loads(only.text)["features"]) == 1


def test_missing_snapshot_is_503_with_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(thermal_repo, "_FILE", thermal_repo.FileBackend(tmp_path / "empty"))
    monkeypatch.setenv("THERMAL_BACKEND", "file")
    c = TestClient(app)
    assert c.get("/api/thermal/sources").status_code == 503
    assert c.get("/api/thermal/status").json()["available"] is False


def test_imagery_scene_choice_is_clearest_within_window(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from backend.app.services import imagery

    bands = {b: {"href": f"https://x/{b}.tif"} for b in imagery.TRUE_BANDS + imagery.SWIR_BANDS}
    item = lambda iid, when, cloud, assets=bands: {  # noqa: E731
        "id": iid, "assets": assets, "properties": {"datetime": when, "eo:cloud_cover": cloud}}
    centre = datetime(2026, 10, 8, 20, 0, tzinfo=timezone.utc)
    picks = imagery.pick_scenes([
        item("cloudy", "2026-10-08T05:00:00Z", 45.0),                    # over the cloud limit
        item("old", "2026-09-20T05:00:00Z", 1.0),                        # outside +-5 days
        item("no-swir", "2026-10-08T05:00:00Z", 2.0, {"red": {"href": "x"}}),   # missing bands
        item("ok-near", "2026-10-08T05:00:00Z", 12.0),
        item("ok-clearer", "2026-10-05T05:00:00Z", 3.5),
        item("ok-tie", "2026-10-07T05:00:00Z", 3.5),
    ], centre)
    assert [p["feature"]["id"] for p in picks] == ["ok-tie", "ok-clearer", "ok-near"]   # clearest first, ties by closeness
    assert picks[0]["offset_days"] < 0 and all(p["cloud"] < imagery.MAX_CLOUD for p in picks)

    monkeypatch.setattr(imagery.core, "stac_search", lambda *a, **k: [])
    monkeypatch.setenv("THERMAL_IMAGERY_DIR", str(tmp_path / "imagery"))
    out = imagery.chip_for("t-none", 85.0, 21.0, "2026-10-08T20:00:00+00:00")
    assert out["available"] is False and "No Sentinel-2 scene" in out["reason"] and out["search"]["max_cloud_percent"] == 30.0
    assert out["landsat"]["available"] is False
    # the "nothing found" answer is cached on disk next to the worker's results
    assert list((tmp_path / "imagery").glob("t-none__2026-10-08.json"))


def test_baseline_fields_reach_the_client(client):
    p = client.get("/api/thermal/sources/a").json()["properties"]
    assert p["baseline_scope"] == "facility" and p["baseline_active_fraction"] == 0.71
    assert p["baseline_frp_p95"] == 9.8 and len(p["baseline_monthly"]) == 12 and p["frp_z"] == 3.1


def test_model_fields_and_model_endpoint(client, tmp_path, monkeypatch):
    p = client.get("/api/thermal/sources/a").json()["properties"]
    assert p.get("model_class") == "industrial_persistent" and 0.9 <= p["model_probability"] <= 1.0
    assert p["model_reasons"] and p["model_version"].startswith("thermal-gbm-")
    metrics = tmp_path / "metrics.json"
    metrics.write_text(json.dumps({"model_version": "thermal-gbm-test", "results": {"model": {"macro_f1": 0.9}}}))
    monkeypatch.setenv("THERMAL_MODEL_METRICS", str(metrics))
    d = client.get("/api/thermal/model").json()
    assert d["available"] is True and d["model_version"] == "thermal-gbm-test" and d["metrics"]["results"]["model"]["macro_f1"] == 0.9
    monkeypatch.setenv("THERMAL_MODEL_METRICS", str(tmp_path / "missing.json"))
    assert client.get("/api/thermal/model").json()["available"] in (True, False)


def test_imagery_endpoint_returns_the_full_evidence_shape(client, monkeypatch):
    from backend.app.services import imagery
    seen = {}

    def fake(source_id, lon, lat, last_seen, first_seen=None):
        seen.update(first_seen=first_seen, lon=lon)
        return {"available": True, "reason": None, "window_km": 3.0, "scene": {"id": "S2B_x", "datetime": "2026-10-08T05:00:00+00:00",
                "cloud_cover_percent": 4.0}, "before_scene": {"id": "S2B_y", "datetime": "2026-09-28T05:00:00+00:00"},
                "images": {"after": {"true_colour": "data:image/png;base64,AA", "swir": "data:image/png;base64,AA"}},
                "indices": {"dnbr_p95": 0.4, "severity": "moderate-low severity", "burned_fraction": 0.2},
                "hot_pixels": {"count": 3, "fraction": 0.0001}, "landsat": {"available": False}}
    monkeypatch.setattr(imagery, "chip_for", fake)
    d = client.get("/api/thermal/sources/b/imagery").json()
    assert d["available"] and d["indices"]["dnbr_p95"] == 0.4 and d["hot_pixels"]["count"] == 3 and d["before_scene"]["id"] == "S2B_y"
    assert seen["first_seen"] == "2026-10-02T20:00:00+00:00"          # the route passes the first detection time through
    assert client.get("/api/thermal/sources/nope/imagery").status_code == 404
