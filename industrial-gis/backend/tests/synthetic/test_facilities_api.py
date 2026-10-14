"""/api/facilities: bbox, kind and source filters, licence metadata, empty state."""
import json

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


def _fc(tmp_path):
    def f(i, lon, lat, kind, sources):
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"facility_id": f"f{i}", "name": f"F{i}", "kind": kind, "sources": sources,
                               "source_count": len(sources.split(",")), "attribution": "x", "radius_m": 1000}}
    p = tmp_path / "facilities.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": [
        f(1, 83.0, 21.0, "power_thermal", "gem,wri_gppd,osm"), f(2, 83.1, 21.1, "mining_quarry", "osm"),
        f(3, 72.0, 22.0, "gas_flare", "vnf")]}))
    return p


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FACILITIES_GEOJSON", str(_fc(tmp_path)))
    return TestClient(app)


def test_all_with_source_licences(client):
    d = client.get("/api/facilities").json()
    assert d["meta"]["total"] == 3 and d["meta"]["available"] is True
    ids = {s["id"]: s for s in d["meta"]["sources"]}
    assert set(ids) == {"osm", "wri_gppd", "gem", "vnf"} and ids["osm"]["licence"] == "ODbL-1.0"


def test_filters(client):
    assert client.get("/api/facilities?kind=gas_flare").json()["meta"]["total"] == 1
    assert client.get("/api/facilities?source=gem").json()["meta"]["total"] == 1
    assert client.get("/api/facilities?bbox=82,20,84,22").json()["meta"]["total"] == 2
    d = client.get("/api/facilities?limit=1").json()
    assert d["meta"]["returned"] == 1 and d["meta"]["truncated"] is True


def test_bad_bbox_is_422(client):
    assert client.get("/api/facilities?bbox=1,2,3").status_code == 422


def test_missing_file_is_reported_not_fabricated(tmp_path, monkeypatch):
    monkeypatch.setenv("FACILITIES_GEOJSON", str(tmp_path / "nope.geojson"))
    d = TestClient(app).get("/api/facilities").json()
    assert d["features"] == [] and d["meta"]["available"] is False
