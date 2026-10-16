"""Alert inbox API against a real PostGIS test database: list, detail with timeline, token-protected decisions -> labels."""
from __future__ import annotations

import os

import psycopg2
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    os.getenv("DATA_MODE") != "postgis" or not os.getenv("TEST_DATABASE_URL"), reason="needs PostGIS")

if os.getenv("DATA_MODE") == "postgis" and os.getenv("TEST_DATABASE_URL"):
    from src.sync.db import run_migrations
    from backend.app.main import app
    from backend.app import config

DSN = os.getenv("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture(scope="module")
def db():
    run_migrations(DSN)
    conn = psycopg2.connect(DSN)
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO alerts (facility_key, source_id, geometry, site_ref, site_name, site_kind, state, trigger, severity,
            p_fire, frp_max, frp_z, first_seen, last_seen, peak_frp, peak_at, summary, reasons, imagery, delivery)
            VALUES ('osm:1', 's1', ST_SetSRID(ST_MakePoint(5, 5), 4326), 'osm:1', 'Alpha Works', 'steel_plant', 'Testland', 'both', 'high',
            0.8, 60, 4.1, NOW() - INTERVAL '3 hours', NOW() - INTERVAL '1 hour', 60, NOW() - INTERVAL '2 hours', 'HIGH: test',
            '["r1"]'::jsonb, '{"imagery_available": true, "imagery_scene_id": "S2B_x", "imagery_scene_time": "2026-10-15T05:00:00+00:00",
            "imagery_cloud_percent": 4.0, "imagery_dnbr_p95": 0.31}'::jsonb, '{"telegram": {"ok": true, "at": "2026-10-16T01:00:00+00:00", "error": null}}'::jsonb)""")
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setenv("ANALYST_TOKEN", "letmein")
    monkeypatch.setattr(config, "_settings", None)            # settings are cached; rebuild with the token above
    with TestClient(app) as c:
        yield c
    config._settings = None


def _first(client) -> dict:
    return client.get("/api/alerts").json()["alerts"][0]


def test_list_and_detail_with_timeline(client):
    body = client.get("/api/alerts").json()
    assert body["available"] and body["decisions_enabled"] and len(body["alerts"]) == 1
    a = client.get(f"/api/alerts/{_first(client)['alert_id']}").json()
    kinds = [e["kind"] for e in a["timeline"]]
    assert {"first_detection", "peak", "last_detection", "alert", "imagery", "delivery"} <= set(kinds)
    assert any("S2B_x" in e["text"] and "dNBR p95 +0.31" in e["text"] for e in a["timeline"])      # imagery in the timeline
    assert client.get("/api/alerts/999999").status_code == 404
    assert client.get("/api/alerts?status=bogus").status_code == 422


def test_decision_needs_the_token_and_a_valid_choice(client):
    aid = _first(client)["alert_id"]
    assert client.post(f"/api/alerts/{aid}/decision", json={"decision": "confirmed"}).status_code == 401
    assert client.post(f"/api/alerts/{aid}/decision", json={"decision": "confirmed"}, headers={"X-Analyst-Token": "wrong"}).status_code == 401
    assert client.post(f"/api/alerts/{aid}/decision", json={"decision": "maybe"}, headers={"X-Analyst-Token": "letmein"}).status_code == 422
    assert client.post("/api/alerts/999999/decision", json={"decision": "confirmed"}, headers={"X-Analyst-Token": "letmein"}).status_code == 404


def test_confirm_writes_a_label_and_changes_status(client, db):
    aid = _first(client)["alert_id"]
    r = client.post(f"/api/alerts/{aid}/decision", json={"decision": "confirmed", "note": "plume in SWIR", "analyst": "asha"},
                    headers={"X-Analyst-Token": "letmein"})
    assert r.status_code == 200 and r.json()["label"] == "industrial_fire"
    with db.cursor() as cur:
        cur.execute("SELECT label, labelled_by, rule_version, split, source_id, state, detail->>'note' FROM labels WHERE label_id = %s",
                    (f"analyst-alert-{aid}",))
        assert cur.fetchone() == ("industrial_fire", "analyst:asha", "analyst-v1", "unused", "s1", "Testland", "plume in SWIR")
    d = client.get(f"/api/alerts/{aid}").json()
    assert d["status"] == "confirmed" and d["decisions"][0]["analyst"] == "asha"
    assert any(e["kind"] == "decision" for e in d["timeline"])
    assert client.get("/api/alerts?status=open").json()["alerts"] == []


def test_decisions_are_disabled_without_a_server_token(client, monkeypatch):
    monkeypatch.setenv("ANALYST_TOKEN", "")
    config._settings = None
    aid = _first(client)["alert_id"]
    assert client.post(f"/api/alerts/{aid}/decision", json={"decision": "unsure"}, headers={"X-Analyst-Token": "x"}).status_code == 503
