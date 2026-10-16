"""Accounts, roles, sessions, CSRF header, rate limits, proxy identity and the audit chain, against a real PostGIS test database."""
from __future__ import annotations

import os

import psycopg2
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    os.getenv("DATA_MODE") != "postgis" or not os.getenv("TEST_DATABASE_URL"), reason="needs PostGIS")

if os.getenv("DATA_MODE") == "postgis" and os.getenv("TEST_DATABASE_URL"):
    from src.sync.db import run_migrations
    from backend.app import config
    from backend.app.main import app
    from backend.app.security import accounts, audit, gate
    from backend.app.security.ratelimit import RateLimiter

DSN = os.getenv("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
PW = "Correct-Horse-Battery-9"
H = {"X-Requested-With": "ThermoTrace"}


@pytest.fixture(scope="module")
def db():
    run_migrations(DSN)
    conn = psycopg2.connect(DSN)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE sessions, users CASCADE")
        cur.execute("""INSERT INTO alerts (facility_key, source_id, geometry, site_name, site_kind, state, trigger, severity, p_fire, frp_max,
            first_seen, last_seen, peak_frp, summary, reasons) VALUES ('osm:9', 's9', ST_SetSRID(ST_MakePoint(5, 5), 4326), 'Plant', 'steel_plant', 'Testland',
            'both', 'high', 0.8, 60, now() - interval '3 hours', now() - interval '1 hour', 60, 'HIGH: test', '["r"]'::jsonb)""")
    conn.commit()
    for name, role in (("admin1", "admin"), ("asha", "analyst"), ("vik", "viewer")):
        accounts.create_user(conn, name, PW, role)
    yield conn
    conn.close()


@pytest.fixture()
def api(db, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("RATE_LOGIN_PER_MIN", "50")
    monkeypatch.setattr(config, "_settings", None)
    monkeypatch.setattr(gate, "limiter", RateLimiter())
    with TestClient(app) as c:
        yield c
    config._settings = None


def login(api, user, password=PW):
    r = api.post("/api/auth/login", json={"username": user, "password": password}, headers=H)
    return r


def as_user(api, user):
    api.cookies.clear()
    assert login(api, user).status_code == 200
    return api


def audit_actions(db, like="%"):
    with db.cursor() as cur:
        cur.execute("SELECT actor, role, action, target, detail FROM audit_log WHERE action LIKE %s ORDER BY audit_id", (like,))
        return cur.fetchall()


def test_everything_but_health_and_login_needs_a_session(api):
    assert api.get("/api/health").status_code == 200
    me = api.get("/api/auth/me").json()
    assert me["auth_mode"] == "local" and me["user"] is None
    for path in ("/api/thermal/stats", "/api/alerts", "/api/dashboard", "/api/facilities", "/api/events"):
        assert api.get(path).status_code == 401, path
    assert api.get("/api/health").headers["X-Request-ID"]


def test_login_sets_an_httponly_strict_cookie_and_me_returns_the_user(api, db):
    r = login(api, "vik")
    assert r.status_code == 200 and r.json()["user"]["role"] == "viewer"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert api.get("/api/auth/me").json()["user"]["username"] == "vik"
    assert api.get("/api/thermal/stats").status_code in (200, 404, 503)          # authenticated: past the gate
    with db.cursor() as cur:                                                    # the cookie value itself is never stored
        cur.execute("SELECT count(*) FROM sessions WHERE token_hash = %s", (api.cookies.get("tt_session"),))
        assert cur.fetchone()[0] == 0


def test_wrong_password_is_401_five_failures_lock_the_account_and_are_audited(api, db):
    accounts.create_user(db, "locky", PW, "viewer")
    for _ in range(4):
        assert login(api, "locky", "wrong-wrong-wrong-1").status_code == 401
    assert login(api, "locky", "wrong-wrong-wrong-1").status_code == 423
    assert login(api, "locky").status_code == 423                               # even the right password: locked for 15 minutes
    assert login(api, "nobody-here", PW).status_code == 401
    failed = [a for a in audit_actions(db, "login.failed") if a[0] in ("locky", "nobody-here")]
    assert len(failed) >= 6 and failed[0][4]["reason"] == "bad_password" and failed[-1][4]["reason"] == "unknown"


def test_state_changing_requests_need_the_csrf_header(api):
    as_user(api, "asha")
    r = api.post("/api/alerts/1/decision", json={"decision": "unsure"})
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]


def test_viewer_reads_but_cannot_decide_or_export_and_the_denial_is_audited(api, db):
    as_user(api, "vik")
    with db.cursor() as cur:
        cur.execute("SELECT min(alert_id) FROM alerts WHERE source_id = 's9'")
        aid = cur.fetchone()[0]
    assert api.get("/api/alerts").status_code == 200 and api.get(f"/api/alerts/{aid}").status_code == 200
    assert api.post(f"/api/alerts/{aid}/decision", json={"decision": "confirmed"}, headers=H).status_code == 403
    assert api.get("/api/thermal/export").status_code == 403
    assert api.get(f"/api/alerts/{aid}/report.pdf").status_code == 403
    assert api.get("/api/admin/audit").status_code == 403
    denied = [a for a in audit_actions(db, "access.denied") if a[0] == "vik"]
    assert len(denied) >= 4 and denied[0][4]["needs"] == "analyst"


def test_analyst_decision_uses_the_signed_in_name_and_is_audited_with_the_label(api, db):
    as_user(api, "asha")
    with db.cursor() as cur:
        cur.execute("SELECT min(alert_id) FROM alerts WHERE source_id = 's9'")
        aid = cur.fetchone()[0]
    r = api.post(f"/api/alerts/{aid}/decision", json={"decision": "confirmed", "note": "smoke", "analyst": "someone-else"}, headers=H)
    assert r.status_code == 200 and r.json()["label"] == "industrial_fire"
    with db.cursor() as cur:
        cur.execute("SELECT labelled_by FROM labels WHERE label_id = %s", (f"analyst-alert-{aid}",))
        assert cur.fetchone()[0] == "analyst:asha"                              # the request body cannot name another analyst
        cur.execute("SELECT analyst FROM alert_decisions WHERE alert_id = %s ORDER BY id DESC LIMIT 1", (aid,))
        assert cur.fetchone()[0] == "asha"
    rows = [a for a in audit_actions(db, "alert.decision") if a[3] == str(aid)]
    assert rows[-1][0] == "asha" and rows[-1][1] == "analyst" and rows[-1][4]["decision"] == "confirmed" and rows[-1][4]["label_id"] == f"analyst-alert-{aid}"


def test_exports_and_reports_are_audited_before_the_download_starts(api, db, monkeypatch):
    from backend.app.services import imagery, report
    monkeypatch.setattr(imagery, "chip_for", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(report, "_http", lambda url: (_ for _ in ()).throw(OSError("offline")))
    from backend.app.api import routes

    class NoSnapshot:                                   # CI has no thermal snapshot; the export only has to be audited
        name = "stub"

        def list_sources(self, flt, limit, offset):
            return [], 0
    monkeypatch.setattr(routes, "_thermal_backend", lambda: NoSnapshot())
    as_user(api, "asha")
    before = len(audit_actions(db, "export.%"))
    assert api.get("/api/thermal/export?format=geojson&min_frp=5").status_code == 200
    with db.cursor() as cur:
        cur.execute("SELECT min(alert_id) FROM alerts WHERE source_id = 's9'")
        aid = cur.fetchone()[0]
    r = api.get(f"/api/alerts/{aid}/report.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    rows = audit_actions(db, "export.%")[before:]
    assert [x[2] for x in rows] == ["export.thermal", "export.report"] and rows[0][4]["format"] == "geojson" and rows[0][4]["filters"] == {"min_frp": 5.0}
    assert rows[1][4]["alert_id"] == aid and rows[0][0] == "asha"


def test_admin_manages_users_and_cannot_remove_the_last_admin(api, db):
    as_user(api, "admin1")
    assert any(u["username"] == "vik" for u in api.get("/api/admin/users").json()["users"])
    r = api.post("/api/admin/users", json={"username": "newbie", "password": "short", "role": "viewer"}, headers=H)
    assert r.status_code == 422 and "12 characters" in r.json()["detail"]
    r = api.post("/api/admin/users", json={"username": "newbie", "password": PW, "role": "analyst"}, headers=H)
    assert r.status_code == 201
    uid = r.json()["user"]["user_id"]
    assert api.patch(f"/api/admin/users/{uid}", json={"role": "viewer"}, headers=H).json()["user"]["role"] == "viewer"
    admin_id = next(u["user_id"] for u in api.get("/api/admin/users").json()["users"] if u["username"] == "admin1")
    r = api.patch(f"/api/admin/users/{admin_id}", json={"active": False}, headers=H)
    assert r.status_code == 422 and "last active admin" in r.json()["detail"]
    created = [a for a in audit_actions(db, "user.%") if a[3] == "newbie"]
    assert [c[2] for c in created] == ["user.created", "user.updated"] and created[1][4] == {"role": "viewer"}


def test_disabling_a_user_or_logging_out_ends_the_session(api, db):
    other = TestClient(app)
    other.cookies.clear()
    assert other.post("/api/auth/login", json={"username": "vik", "password": PW}, headers=H).status_code == 200
    assert other.get("/api/alerts").status_code == 200
    as_user(api, "admin1")
    vid = next(u["user_id"] for u in api.get("/api/admin/users").json()["users"] if u["username"] == "vik")
    api.patch(f"/api/admin/users/{vid}", json={"active": False}, headers=H)
    assert other.get("/api/alerts").status_code == 401
    api.patch(f"/api/admin/users/{vid}", json={"active": True}, headers=H)
    assert other.post("/api/auth/login", json={"username": "vik", "password": PW}, headers=H).status_code == 200
    assert other.post("/api/auth/logout", headers=H).status_code == 200
    other.cookies.set("tt_session", "stale")
    assert other.get("/api/alerts").status_code == 401


def test_idle_sessions_expire(api, db):
    as_user(api, "vik")
    with db.cursor() as cur:
        cur.execute("UPDATE sessions SET last_seen = now() - interval '3 hours'")
    db.commit()
    assert api.get("/api/alerts").status_code == 401


def test_audit_chain_verifies_and_detects_tampering(api, db):
    as_user(api, "admin1")
    v = api.get("/api/admin/audit/verify").json()
    assert v["ok"] and v["rows"] > 10 and len(v["head"]) == 64
    entries = api.get("/api/admin/audit?limit=5&action=login").json()["entries"]
    assert entries and all(e["action"].startswith("login") for e in entries)
    with db.cursor() as cur:                                                    # append-only: the database itself refuses edits
        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute("UPDATE audit_log SET actor = 'mallory' WHERE audit_id = (SELECT min(audit_id) FROM audit_log)")
    db.rollback()
    with db.cursor() as cur:                                                    # a database owner who bypasses the trigger is caught by the chain
        cur.execute("SELECT audit_id, actor FROM audit_log ORDER BY audit_id OFFSET 3 LIMIT 1")
        row_id, original = cur.fetchone()
        cur.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update")
        cur.execute("UPDATE audit_log SET actor = 'mallory' WHERE audit_id = %s", (row_id,))
    db.commit()
    bad = audit.verify(db)
    with db.cursor() as cur:
        cur.execute("UPDATE audit_log SET actor = %s WHERE audit_id = %s", (original, row_id))
        cur.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update")
    db.commit()
    assert bad["ok"] is False and bad["first_bad_id"] == row_id
    assert audit.verify(db)["ok"] is True                                       # put back as it was: the chain is whole again


def test_login_is_rate_limited_per_address(api, monkeypatch):
    monkeypatch.setenv("RATE_LOGIN_PER_MIN", "3")
    monkeypatch.setattr(config, "_settings", None)
    codes = [login(api, "vik", "wrong-wrong-wrong-1").status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
    assert api.post("/api/auth/login", json={}, headers=H).headers["Retry-After"]


def test_proxy_mode_trusts_identity_headers_only_with_the_shared_secret(db, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "proxy")
    monkeypatch.setenv("AUTH_PROXY_SECRET", "s3cret-shared-with-the-proxy")
    monkeypatch.setattr(config, "_settings", None)
    monkeypatch.setattr(gate, "limiter", RateLimiter())
    secret = {"X-Auth-Proxy-Secret": "s3cret-shared-with-the-proxy"}
    with TestClient(app) as c:
        assert c.get("/api/alerts", headers={"X-Auth-Request-User": "mallory"}).status_code == 401                 # no secret: header forged
        assert c.get("/api/alerts", headers={"X-Auth-Request-User": "mallory", "X-Auth-Proxy-Secret": "wrong"}).status_code == 401
        assert c.get("/api/alerts", headers={**secret, "X-Auth-Request-User": "lena"}).status_code == 200            # viewer by default
        me = c.get("/api/auth/me", headers={**secret, "X-Auth-Request-User": "lena", "X-Auth-Request-Groups": "staff,thermotrace-analyst"}).json()
        assert me["user"]["role"] == "analyst" and me["user"]["via"] == "proxy"
        assert c.get("/api/admin/audit", headers={**secret, "X-Auth-Request-User": "lena", "X-Auth-Request-Groups": "thermotrace-analyst"}).status_code == 403
        assert c.get("/api/admin/audit", headers={**secret, "X-Auth-Request-User": "root", "X-Auth-Request-Groups": "thermotrace-admin"}).status_code == 200
    config._settings = None


def test_auth_off_keeps_the_legacy_behaviour_and_hides_account_routes(db, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "off")
    monkeypatch.setattr(config, "_settings", None)
    with TestClient(app) as c:
        assert c.get("/api/alerts").status_code == 200
        assert c.get("/api/admin/users").status_code == 404 and c.get("/api/admin/audit").status_code == 404
        assert c.post("/api/auth/login", json={"username": "x", "password": "y"}).status_code == 404
    config._settings = None
