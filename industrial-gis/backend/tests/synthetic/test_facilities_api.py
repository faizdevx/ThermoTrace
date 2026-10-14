"""/api/facilities (file backend): filters, licence metadata, detail with footprint + provenance, empty state."""
import json

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

SQUARE = {"type": "Polygon", "coordinates": [[[83, 21], [83.01, 21], [83.01, 21.01], [83, 21.01], [83, 21]]]}


def _write(d):
    def f(i, lon, lat, kind, sources, fp=False):
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"facility_id": f"f{i}", "name": f"F{i}", "kind": kind, "sources": sources,
                               "source_count": len(sources.split(",")), "attribution": "x", "radius_m": 1000,
                               "has_footprint": fp}}
    (d / "facilities.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        f(1, 83.0, 21.0, "power_thermal", "gem,wri_gppd,osm", fp=True), f(2, 83.1, 21.1, "mining_quarry", "osm"),
        f(3, 72.0, 22.0, "gas_flare", "vnf")]}))
    (d / "facility_footprints.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": SQUARE, "properties": {"facility_id": "f1"}}]}))
    (d / "facility_sources.csv").write_text(
        "facility_id,source,source_id,name,distance_m,url,licence,attribution\n"
        "f1,gem,coal_plant:L1,F1,0.0,,CC-BY-4.0,Global Energy Monitor\n"
        "f1,wri_gppd,IND1,F1 TPS,120.5,,CC-BY-4.0,WRI\n"
        "f1,osm,way/9,,80.0,https://www.openstreetmap.org/way/9,ODbL-1.0,(c) OpenStreetMap contributors\n")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    _write(tmp_path)
    monkeypatch.delenv("FACILITIES_GEOJSON", raising=False)
    monkeypatch.setenv("FACILITIES_DIR", str(tmp_path))
    monkeypatch.setenv("FACILITIES_BACKEND", "files")
    return TestClient(app)


def test_all_with_source_licences(client):
    d = client.get("/api/facilities").json()
    assert d["meta"]["total"] == 3 and d["meta"]["available"] is True and d["meta"]["backend"] == "files"
    ids = {s["id"]: s for s in d["meta"]["sources"]}
    assert set(ids) == {"osm", "wri_gppd", "gem", "vnf"} and ids["osm"]["licence"] == "ODbL-1.0"


def test_filters(client):
    assert client.get("/api/facilities?kind=gas_flare").json()["meta"]["total"] == 1
    assert client.get("/api/facilities?kind=gas_flare,mining_quarry").json()["meta"]["total"] == 2
    assert client.get("/api/facilities?source=gem").json()["meta"]["total"] == 1
    assert client.get("/api/facilities?bbox=82,20,84,22").json()["meta"]["total"] == 2
    d = client.get("/api/facilities?limit=1").json()
    assert d["meta"]["returned"] == 1 and d["meta"]["truncated"] is True


def test_bad_input_is_422(client):
    assert client.get("/api/facilities?bbox=1,2,3").status_code == 422
    assert client.get("/api/facilities?source=nope").status_code == 422
    assert client.get("/api/facilities?limit=0").status_code == 422


def test_detail_has_footprint_and_provenance(client):
    d = client.get("/api/facilities/f1").json()
    assert d["footprint"]["type"] == "Polygon"
    assert [r["source"] for r in d["source_records"]] == ["gem", "wri_gppd", "osm"]
    assert d["source_records"][1]["distance_m"] == 120.5 and d["source_records"][0]["url"] is None
    assert client.get("/api/facilities/f2").json()["footprint"] is None
    assert client.get("/api/facilities/nope").status_code == 404


def test_missing_files_are_reported_not_fabricated(tmp_path, monkeypatch):
    monkeypatch.delenv("FACILITIES_GEOJSON", raising=False)
    monkeypatch.setenv("FACILITIES_DIR", str(tmp_path / "empty"))
    monkeypatch.setenv("FACILITIES_BACKEND", "files")
    c = TestClient(app)
    d = c.get("/api/facilities").json()
    assert d["features"] == [] and d["meta"]["available"] is False
    assert c.get("/api/facilities/f1").status_code == 404
