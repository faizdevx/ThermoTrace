"""API contract + update-lifecycle tests against a real PostGIS test database (controlled fixture)."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os

import psycopg2
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    os.getenv("DATA_MODE") != "postgis" or not os.getenv("TEST_DATABASE_URL"), reason="needs PostGIS")

if os.getenv("DATA_MODE") == "postgis" and os.getenv("TEST_DATABASE_URL"):
    from src.sync.db import run_migrations
    from src.sync.master import apply_osm_sync
    from backend.app.main import app
    from backend.app.services import events, postgis_repo

DSN = os.getenv("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")

RAW_DDL = """
CREATE TABLE IF NOT EXISTS osm_industrial_raw (
    osm_type char(1) NOT NULL, osm_id bigint NOT NULL, version int4, changeset int8,
    osm_timestamp timestamptz, tags jsonb, geom geometry(Geometry, 4326) NOT NULL, PRIMARY KEY (osm_type, osm_id));
"""

SEED = [  # (type, id, tags, wkt)
    ("W", 1, {"industrial": "factory", "name": "Alpha Works", "operator": "Alpha Ltd", "website": "https://a.example"},
     "POLYGON((1 1,1 2,2 2,2 1,1 1))"),
    ("W", 2, {"industrial": "chemical", "name": "Beta Chemicals"}, "POLYGON((6 6,6 7,7 7,7 6,6 6))"),
    ("N", 3, {"man_made": "works"}, "POINT(2 2)"),
    ("W", 4, {"landuse": "industrial"}, "POLYGON((3 3,3 4,4 4,4 3,3 3))"),
    ("N", 5, {"power": "plant", "name": "Gamma Power", "operator": "Gamma", "ref": "G1"}, "POINT(8 8)"),
    ("N", 6, {"industrial": "factory", "name": "=HYPERLINK(\"http://evil\")"}, "POINT(2.5 2.5)"),
    ("N", 7, {"industrial": "warehouse", "name": "100% Pure_Stuff"}, "POINT(9 1)"),
    ("N", 8, {"industrial": "factory", "name": "Outside Everything"}, "POINT(50 50)"),   # no state
]


def sync(db, *, delete: set[int] = frozenset(), add: list | None = None):
    with db.cursor() as cur:
        for osm_type, osm_id, tags, wkt in (add or []):
            cur.execute("""INSERT INTO osm_industrial_raw (osm_type, osm_id, version, osm_timestamp, tags, geom)
                VALUES (%s,%s,1,NOW(),%s::jsonb,ST_GeomFromText(%s,4326))
                ON CONFLICT (osm_type, osm_id) DO UPDATE SET tags=EXCLUDED.tags, geom=EXCLUDED.geom""",
                        (osm_type, osm_id, json.dumps(tags), wkt))
        for osm_id in delete:
            cur.execute("DELETE FROM osm_industrial_raw WHERE osm_id = %s", (osm_id,))
        result = apply_osm_sync(cur, allow_mass_deactivation=True)
        cur.execute("""UPDATE sync_state SET cursor = '{"sequence": 1}'::jsonb WHERE source='openstreetmap'""")
    db.commit()
    return result


@pytest.fixture(scope="module")
def db():
    run_migrations(DSN)
    conn = psycopg2.connect(DSN)
    with conn.cursor() as cur:
        cur.execute(RAW_DDL)
        cur.execute("""INSERT INTO sync_state (source, kind, status, cursor, update_cadence, poll_interval_seconds)
                       VALUES ('openstreetmap','osm_replication','idle','{"sequence":1}','daily',300) ON CONFLICT DO NOTHING""")
        cur.execute("""INSERT INTO states (name, geometry, source, source_id)
            VALUES ('Testland', ST_Multi(ST_GeomFromText('POLYGON((0 0,0 10,10 10,10 0,0 0))',4326)), 'fixture','s1'),
                   ('Otherland', ST_Multi(ST_GeomFromText('POLYGON((20 20,20 30,30 30,30 20,20 20))',4326)), 'fixture','s2')""")
        cur.execute("""INSERT INTO districts (name, geometry, source, source_id)
            VALUES ('North Test', ST_Multi(ST_GeomFromText('POLYGON((0 5,0 10,10 10,10 5,0 5))',4326)), 'fixture','d1'),
                   ('South Test', ST_Multi(ST_GeomFromText('POLYGON((0 0,0 5,10 5,10 0,0 0))',4326)), 'fixture','d2')""")
    conn.commit()
    sync(conn, add=SEED)
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def client(db):
    with TestClient(app) as c:
        yield c


def features(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    return r.json()


# ── health ─────────────────────────────────────────────────────────────────
def test_health_reports_database_and_dataset_separately(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["data_mode"] == "postgis" and body["synthetic"] is False
    assert body["database"]["status"] == "ok" and body["ready"] is True
    assert body["dataset"]["active_sites"] == 8 and body["dataset"]["boundary_states"] == 2


# ── list / pagination / filters ────────────────────────────────────────────
def test_list_contract_and_pagination_are_consistent(client):
    page1 = features(client, "/api/industries?limit=3&offset=0")
    page2 = features(client, "/api/industries?limit=3&offset=3")
    page3 = features(client, "/api/industries?limit=3&offset=6")
    assert page1["meta"]["total"] == 8 and page1["meta"]["truncated"] is True
    assert page3["meta"]["truncated"] is False and len(page3["features"]) == 2
    ids = [f["properties"]["site_id"] for p in (page1, page2, page3) for f in p["features"]]
    assert len(ids) == len(set(ids)) == 8 and ids == sorted(ids)          # stable order, no dup/skip
    f = page1["features"][0]
    assert f["geometry"]["type"] == "Point"                              # list rows are representative points
    for key in ("site_id", "name", "industry_type", "state", "district", "status", "evidence_level",
                "source_url", "primary_source", "osm_ids", "latitude", "longitude"):
        assert key in f["properties"]


def test_filters_combine(client):
    assert features(client, "/api/industries?state=testland")["meta"]["total"] == 7            # case-insensitive
    assert features(client, "/api/industries?state=Testland&district=North%20Test")["meta"]["total"] == 2
    assert features(client, "/api/industries?industry_type=Manufacturing")["meta"]["total"] == 4
    assert features(client, "/api/industries?confidence=high")["meta"]["total"] == 2
    assert features(client, "/api/industries?status=mapped")["meta"]["total"] == 8
    assert features(client, "/api/industries?status=disused")["meta"]["total"] == 0
    assert features(client, "/api/industries?state=Testland&industry_type=Chemicals%20%26%20Petrochemicals")["meta"]["total"] == 1


def test_bbox_filter_uses_current_geometry(client):
    inside = features(client, "/api/industries?bbox=0.5,0.5,2.2,2.2")
    names = sorted(f["properties"]["name"] for f in inside["features"])
    assert inside["meta"]["total"] == 2 and "Alpha Works" in names                  # polygon + point inside
    assert features(client, "/api/industries?bbox=40,40,60,60")["meta"]["total"] == 1


@pytest.mark.parametrize("bad", ["abc", "1,2,3", "5,5,1,1", "-200,0,10,10", "0,0,10,95", "1,2,3,x"])
def test_malformed_bbox_is_422_not_500(client, bad):
    r = client.get(f"/api/industries?bbox={bad}")
    assert r.status_code == 422 and "bbox" in r.json()["detail"]


@pytest.mark.parametrize("qs", ["limit=0", "limit=999999", "offset=-1", "geometry=weird"])
def test_invalid_paging_parameters_are_422(client, qs):
    assert client.get(f"/api/industries?{qs}").status_code == 422


def test_search_matches_text_and_treats_wildcards_literally(client):
    assert features(client, "/api/industries/search?q=alpha")["meta"]["total"] == 1
    assert features(client, "/api/industries/search?q=100%25")["meta"]["total"] == 1      # '%' is literal
    assert features(client, "/api/industries/search?q=%25")["meta"]["total"] == 1         # not "match everything"
    assert features(client, "/api/industries/search?q=zzzz-none")["meta"]["total"] == 0
    assert features(client, "/api/industries/search?q=chemicals&state=Testland")["meta"]["total"] == 1
    assert client.get("/api/industries/search").status_code == 422


def test_sql_injection_attempt_is_inert(client):
    r = client.get("/api/industries?state=x'%3B%20DROP%20TABLE%20industrial_sites%3B--")
    assert r.status_code == 200 and r.json()["meta"]["total"] == 0
    assert features(client, "/api/industries")["meta"]["total"] == 8


# ── detail ──────────────────────────────────────────────────────────────────
def test_detail_has_full_geometry_tags_and_provenance(client):
    listing = features(client, "/api/industries/search?q=alpha")["features"][0]["properties"]
    d = client.get(f"/api/industries/{listing['site_id']}").json()
    assert d["geometry"]["type"] == "Polygon"
    p = d["properties"]
    assert p["tags"]["operator"] == "Alpha Ltd" and p["area_m2"] > 0
    assert p["source_url"] == "https://www.openstreetmap.org/way/1" and p["state"] == "Testland"
    assert client.get("/api/industries/does-not-exist").status_code == 404


def test_unassigned_state_is_reported_honestly(client):
    p = features(client, "/api/industries/search?q=outside")["features"][0]["properties"]
    assert p["state"] == "" and p["district"] == ""


# ── statistics / options ──────────────────────────────────────────────────────
def test_statistics_have_defined_scope_and_add_up(client):
    s = client.get("/api/statistics").json()
    assert s["total_sites"] == 8 and sum(s["by_industry_type"].values()) == 8
    assert sum(s["by_state"].values()) == 8 and s["by_state"]["Unassigned"] == 1
    assert sum(s["confidence_distribution"].values()) == 8
    assert s["government_records"] == 0 and s["matched_records"] == 0
    scoped = client.get("/api/statistics?state=Testland&confidence=high").json()
    assert scoped["total_sites"] == 2 and scoped["scope"]["filters"] == {"state": "Testland", "confidence": "high"}
    assert client.get("/api/statistics?bbox=oops").status_code == 422


def test_filter_options_cascade(client):
    all_opts = client.get("/api/filters/options").json()
    assert all_opts["states"] == ["Otherland", "Testland"] or "Testland" in all_opts["states"]
    assert set(all_opts["districts"]) == {"North Test", "South Test"}
    assert client.get("/api/filters/options?state=Otherland").json()["districts"] == []
    assert "high" in all_opts["confidence_levels"]


# ── boundaries ───────────────────────────────────────────────────────────────
def test_boundaries_endpoint(client):
    states = features(client, "/api/boundaries/states")
    assert {f["properties"]["name"] for f in states["features"]} == {"Testland", "Otherland"}
    assert states["features"][0]["geometry"]["type"] in ("Polygon", "MultiPolygon")
    d = features(client, "/api/boundaries/districts?state=Testland")
    assert {f["properties"]["name"] for f in d["features"]} == {"North Test", "South Test"}
    assert features(client, "/api/boundaries/districts?state=Otherland")["features"] == []
    assert client.get("/api/boundaries/planets").status_code == 404
    assert client.get("/api/boundaries/states?bbox=bad").status_code == 422


# ── exports ──────────────────────────────────────────────────────────────────
def test_csv_export_is_complete_unpaginated_and_formula_safe(client):
    r = client.get("/api/industries/export?format=csv")
    assert r.status_code == 200 and r.headers["x-total-count"] == "8"
    assert "attachment" in r.headers["content-disposition"] and r.headers["x-data-revision"].isdigit()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 8 and rows[0]["primary_source"] == "openstreetmap"
    assert all(not row["name"].startswith("=") for row in rows)                 # spreadsheet injection neutralised
    filtered = client.get("/api/industries/export?format=csv&state=Testland")
    assert len(list(csv.DictReader(io.StringIO(filtered.text)))) == 7 == int(filtered.headers["x-total-count"])


def test_geojson_export_is_valid_and_has_real_geometry(client):
    r = client.get("/api/industries/export?format=geojson&industry_type=Manufacturing")
    data = json.loads(r.text)
    assert data["type"] == "FeatureCollection" and len(data["features"]) == int(r.headers["x-total-count"]) == 4
    assert {f["geometry"]["type"] for f in data["features"]} <= {"Point", "Polygon", "MultiPolygon"}
    assert client.get("/api/industries/export?format=xml").status_code == 422


# ── update lifecycle through the API ───────────────────────────────────────────────
def test_committed_changes_are_visible_through_every_endpoint(client, db):
    before = client.get("/api/statistics").json()
    rev0 = features(client, "/api/industries?limit=1")["meta"]["revision"]
    result = sync(db, add=[("W", 900, {"industrial": "steel", "name": "Live Steel Works", "operator": "Steel Co"},
                           "POLYGON((4 6,4 7,5 7,5 6,4 6))")])
    assert result.created == 1 and result.revision == rev0 + 1

    found = features(client, "/api/industries/search?q=live%20steel")
    assert found["meta"]["total"] == 1 and found["meta"]["revision"] == rev0 + 1        # cache invalidated by revision
    site_id = found["features"][0]["properties"]["site_id"]
    assert found["features"][0]["properties"]["district"] == "North Test"
    after = client.get("/api/statistics").json()
    assert after["total_sites"] == before["total_sites"] + 1 and after["revision"] == rev0 + 1
    assert after["by_industry_type"]["Metals & Steel"] == 1

    sync(db, add=[("W", 900, {"industrial": "steel", "name": "Live Steel Works Renamed"},
                   "POLYGON((4 6,4 7,5 7,5 6,4 6))")])
    assert client.get(f"/api/industries/{site_id}").json()["properties"]["name"] == "Live Steel Works Renamed"

    result = sync(db, delete={900})
    assert result.deleted == 1
    assert client.get(f"/api/industries/{site_id}").status_code == 404                 # gone for readers
    assert client.get("/api/statistics").json()["total_sites"] == before["total_sites"]
    assert features(client, "/api/industries/search?q=live%20steel")["meta"]["total"] == 0
    assert "Live Steel" not in client.get("/api/industries/export?format=csv").text


def test_unchanged_sync_does_not_bump_revision(client, db):
    rev = features(client, "/api/industries?limit=1")["meta"]["revision"]
    result = sync(db)
    assert result.changed is False
    assert features(client, "/api/industries?limit=1")["meta"]["revision"] == rev


# ── sync endpoints ──────────────────────────────────────────────────────────────
def test_sync_status_and_history_endpoints(client, db):
    with db.cursor() as cur:
        cur.execute("""UPDATE sync_state SET last_check_at = NOW(), last_source_data_at = NOW() - interval '1 hour',
                       source_head_sequence = 1, source_head_timestamp = NOW() - interval '1 hour',
                       freshness_target_seconds = 129600, status = 'idle' WHERE source = 'openstreetmap'""")
        cur.execute("INSERT INTO sync_runs (source, trigger, status, finished_at) VALUES ('openstreetmap','schedule','success', NOW())")
    db.commit()
    s = client.get("/api/sync/status").json()
    assert s["database"]["status"] == "ok" and s["api"]["alive"] is True and s["revision"] >= 1
    osm = next(x for x in s["sources"] if x["source"] == "openstreetmap")
    assert osm["state"] == "live" and osm["update_cadence"] == "daily"
    assert osm["last_check_at"] and osm["last_source_data_at"]                   # reported separately
    h = client.get("/api/sync/history?limit=5").json()["runs"]
    assert h and h[0]["source"] == "openstreetmap"


def test_admin_trigger_is_protected(client, db, monkeypatch):
    from backend.app.config import get_settings
    assert client.post("/api/admin/sync/trigger", json={}).status_code == 503        # disabled without token
    get_settings().admin_token = "s3cret"
    try:
        assert client.post("/api/admin/sync/trigger", json={}).status_code == 401
        assert client.post("/api/admin/sync/trigger", json={}, headers={"X-Admin-Token": "wrong"}).status_code == 401
        ok = client.post("/api/admin/sync/trigger", json={"source": "openstreetmap"}, headers={"X-Admin-Token": "s3cret"})
        assert ok.status_code == 200 and ok.json()["requested"] is True
        with db.cursor() as cur:
            cur.execute("SELECT manual_requested_at IS NOT NULL FROM sync_state WHERE source='openstreetmap'")
            assert cur.fetchone()[0] is True
        db.commit()
        assert client.post("/api/admin/sync/trigger", json={"source": "nope"},
                           headers={"X-Admin-Token": "s3cret"}).status_code == 404
    finally:
        get_settings().admin_token = ""


# ── SSE ──────────────────────────────────────────────────────────────────────────
def test_sse_hub_delivers_revision_events_after_commit(db):
    async def scenario():
        hub = events.EventHub()
        hub.start(asyncio.get_running_loop())
        queue = hub.subscribe()
        for _ in range(100):                        # wait until LISTEN is established
            if hub.connected:
                break
            await asyncio.sleep(0.05)
        assert hub.connected
        await asyncio.to_thread(
            sync, db, add=[("N", 777, {"industrial": "factory", "name": "SSE Probe"}, "POINT(1 9)")])
        message = await asyncio.wait_for(queue.get(), timeout=10)
        hub.stop()
        return message

    message = asyncio.run(scenario())
    assert message["event"] == "revision" and message["data"]["created"] == 1
    wire = events.format_sse(message["event"], message["data"], message["data"]["revision"])
    assert wire.startswith(f"id: {message['data']['revision']}\nevent: revision\ndata: {{")


def test_sse_endpoint_streams_hello_first(client):
    with client.stream("GET", "/api/events") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        first = ""
        for chunk in r.iter_text():
            first += chunk
            if "event: hello" in first and "data:" in first:
                break
    assert '"revision"' in first


# ── database failure ─────────────────────────────────────────────────────────────
def test_database_outage_is_reported_without_leaking_details(client, monkeypatch):
    from sqlalchemy import create_engine
    import backend.app.db as dbmod
    dead = create_engine("postgresql+psycopg2://nobody:pw@127.0.0.1:1/none", connect_args={"connect_timeout": 1})
    monkeypatch.setattr(dbmod, "_engine", dead)
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and health["database"]["status"] == "unavailable" and health["ready"] is False
    r = client.get("/api/industries")
    assert r.status_code == 503 and r.json() == {"detail": "Database temporarily unavailable."}
    assert "nobody" not in r.text and "127.0.0.1" not in r.text
    sync = client.get("/api/sync/status").json()
    assert sync["overall"]["state"] == "offline"
