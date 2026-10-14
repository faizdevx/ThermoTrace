"""Facility database in PostGIS: migration 005, persist_postgis, /api/facilities, detail, thermal matcher."""
from __future__ import annotations

import os

import pandas as pd
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    os.getenv("DATA_MODE") != "postgis" or not os.getenv("TEST_DATABASE_URL"), reason="needs PostGIS")

if os.getenv("DATA_MODE") == "postgis" and os.getenv("TEST_DATABASE_URL"):
    from src.facilities.merge import merge
    from src.facilities.sources import COLUMNS
    from src.facilities.store import persist_postgis
    from src.sync.db import run_migrations
    from src.thermal.sites import SiteIndex
    from backend.app.main import app

DSN = os.getenv("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
SQUARE = "POLYGON ((83 21, 83.01 21, 83.01 21.01, 83 21.01, 83 21))"


def _rec(source, sid, kind, lat, lon, name=None, cap=None, footprint=None):
    return {"source": source, "source_id": sid, "name": name, "kind": kind, "lat": lat, "lon": lon,
            "radius_m": 1500.0, "capacity_mw": cap, "status": None, "fuel": None, "url": None, "footprint": footprint}


@pytest.fixture(scope="module")
def built():
    run_migrations(DSN)   # 005_facilities is applied by the normal migration runner
    frame = pd.DataFrame([
        _rec("osm", "way/1", "power_thermal", 21.005, 83.005, "Fixture TPS", footprint=SQUARE),
        _rec("wri_gppd", "W1", "power_thermal", 21.006, 83.006, "Fixture Power", 1200.0),
        _rec("osm", "node/2", "gas_flare", 22.0, 72.0),
    ], columns=COLUMNS)
    fac, links = merge([frame])
    assert persist_postgis(DSN, fac, links) == 2
    assert persist_postgis(DSN, fac, links) == 2   # idempotent replace
    return fac


@pytest.fixture(scope="module")
def client(built):
    with TestClient(app) as c:
        yield c


def test_list_served_from_postgis_with_licences(client):
    d = client.get("/api/facilities").json()
    assert d["meta"]["backend"] == "postgis" and d["meta"]["total"] == 2
    assert {s["id"] for s in d["meta"]["sources"]} == {"osm", "wri_gppd"}
    top = d["features"][0]["properties"]
    assert top["source_count"] == 2 and top["has_footprint"] is True and top["capacity_mw"] == 1200.0


def test_filters_in_sql(client):
    assert client.get("/api/facilities?kind=gas_flare").json()["meta"]["total"] == 1
    assert client.get("/api/facilities?source=wri_gppd").json()["meta"]["total"] == 1
    assert client.get("/api/facilities?bbox=82,20,84,22").json()["meta"]["total"] == 1
    assert client.get("/api/facilities?source=nope").status_code == 422


def test_detail_has_footprint_and_every_source_record(client, built):
    fid = built[built.source_count == 2].facility_id.iloc[0]
    d = client.get(f"/api/facilities/{fid}").json()
    assert d["footprint"]["type"] == "Polygon"
    assert {r["source"] for r in d["source_records"]} == {"osm", "wri_gppd"}
    assert all(r["licence"] for r in d["source_records"])
    assert client.get("/api/facilities/does-not-exist").status_code == 404


def test_thermal_matcher_reads_the_facility_table(built):
    idx = SiteIndex.from_postgis(DSN)
    assert len(idx) == 2 and set(idx.sites.kind) == {"power_thermal", "gas_flare"}
