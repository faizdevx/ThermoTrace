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
