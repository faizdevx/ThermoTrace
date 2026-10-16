"""Alert rules, delivery channels (local fake servers, no network) and the PostGIS alert/decision/label loop."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pandas as pd
import pytest

from src.alerts import notify, rules

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def _src(**over):
    base = {"source_id": "s1", "lat": 21.0, "lon": 83.0, "site_ref": "osm:1", "site_within": True, "site_name": "Test Refinery", "site_kind": "refinery",
            "state": "Gujarat", "last_seen": pd.Timestamp(NOW - timedelta(hours=2)), "first_seen": pd.Timestamp(NOW - timedelta(days=1)),
            "frp_max": 30.0, "frp_z": 0.5, "lc_crop": 0.05, "night_fraction": 0.6, "class_scores": {"industrial_fire_candidate": 0.2}, "reasons": ["r1"]}
    base.update(over)
    return base


def _df(*rows):
    return pd.DataFrame(list(rows))


# ---------------------------------------------------------------- rules
def test_no_alert_below_both_thresholds():
    assert rules.evaluate(_df(_src()), NOW) == []


def test_p_fire_rule_and_the_facility_z_rule_each_trigger_alone():
    a = rules.evaluate(_df(_src(class_scores={"industrial_fire_candidate": 0.8})), NOW)
    b = rules.evaluate(_df(_src(frp_z=3.4)), NOW)
    both = rules.evaluate(_df(_src(frp_z=3.4, class_scores={"industrial_fire_candidate": 0.8})), NOW)
    assert [c.trigger for c in a] == ["p_fire"] and [c.trigger for c in b] == ["frp_z"] and both[0].trigger == "both"


def test_z_rule_needs_a_known_facility():
    assert rules.evaluate(_df(_src(site_ref=None, frp_z=5.0)), NOW) == []
    assert rules.evaluate(_df(_src(site_within=False, frp_z=9.0)), NOW) == []        # nearest facility is 30 km away: not "at" it
    assert len(rules.evaluate(_df(_src(site_ref=None, class_scores={"industrial_fire_candidate": 0.9})), NOW)) == 1


def test_stale_and_faint_sources_never_alert():
    stale = _src(last_seen=pd.Timestamp(NOW - timedelta(hours=40)), frp_z=9.0)
    faint = _src(frp_max=2.0, frp_z=9.0)
    assert rules.evaluate(_df(stale, faint), NOW) == []


def test_severity_follows_power_and_raises_for_hazardous_facilities():
    assert rules.severity_for(10, "steel_plant") == "watch"
    assert rules.severity_for(50, "steel_plant") == "high"
    assert rules.severity_for(150, "steel_plant") == "critical"
    assert rules.severity_for(10, "refinery") == "high"          # hazardous kind: one level up
    assert rules.severity_for(60, "lng_terminal") == "critical"
    assert rules.severity_for(500, "refinery") == "critical"     # capped
    assert rules.severity_for(float("nan"), None) == "watch"


def test_one_candidate_per_facility_the_strongest_source():
    rows = [_src(source_id="a", frp_z=3.5, frp_max=20), _src(source_id="b", frp_z=3.5, frp_max=90),
            _src(source_id="c", site_ref="osm:2", frp_z=3.5, frp_max=12)]
    out = rules.evaluate(_df(*rows), NOW)
    assert [(c.source_id, c.facility_key) for c in out] == [("b", "osm:1"), ("c", "osm:2")]   # sorted by severity then power


def test_summary_names_the_place_power_and_reason():
    c = rules.evaluate(_df(_src(frp_z=4.0, frp_max=45)), NOW)[0]
    assert "Test Refinery" in c.summary and "45 MW" in c.summary and "baseline" in c.summary and c.severity == "critical"


# ---------------------------------------------------------------- delivery channels (fake servers)
class _Recorder(BaseHTTPRequestHandler):
    calls: list = []
    status = 200

    def do_POST(self):                       # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        type(self).calls.append({"path": self.path, "body": body, "headers": dict(self.headers)})
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": type(self).status == 200}).encode())

    def log_message(self, *a):               # noqa: D401
        pass


@pytest.fixture()
def server():
    handler = type("H", (_Recorder,), {"calls": [], "status": 200})
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, handler
    srv.shutdown()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("TELEGRAM_", "ALERT_WEBHOOK", "SMTP_", "ALERT_EMAIL", "ALERT_BASE")):
            monkeypatch.delenv(k)


ALERT = {"alert_id": 7, "summary": "HIGH: possible industrial fire at X", "severity": "high", "site_name": "X", "source_id": "s",
         "first_seen": "2026-10-16T01:00:00+00:00", "last_seen": "2026-10-16T02:00:00+00:00", "lat": 21.0, "lon": 83.0,
         "reasons": ["a reason"], "is_test": False}


def test_nothing_configured_means_no_channels_and_no_error():
    assert notify.configured() == [] and notify.send(ALERT) == {}


def test_telegram_message_goes_to_the_bot_api_with_the_chat_id(monkeypatch, server):
    srv, h = server
    monkeypatch.setenv("TELEGRAM_API_BASE", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRET")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100500")
    monkeypatch.setenv("ALERT_BASE_URL", "https://tt.example/")
    res = notify.send(ALERT)
    assert res["telegram"]["ok"] and h.calls[0]["path"] == "/bot123:SECRET/sendMessage"
    sent = json.loads(h.calls[0]["body"])
    assert sent["chat_id"] == "-100500" and "possible industrial fire at X" in sent["text"] and "https://tt.example/#alert-7" in sent["text"]


def test_telegram_failure_is_reported_without_leaking_the_token(monkeypatch, server):
    srv, h = server
    h.status = 401
    monkeypatch.setenv("TELEGRAM_API_BASE", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRET")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    r = notify.send(ALERT)["telegram"]
    assert r["ok"] is False and "401" in r["error"] and "SECRET" not in r["error"]


def test_webhook_is_signed_when_a_secret_is_set(monkeypatch, server):
    srv, h = server
    monkeypatch.setenv("ALERT_WEBHOOK_URL", f"http://127.0.0.1:{srv.server_port}/hook")
    monkeypatch.setenv("ALERT_WEBHOOK_SECRET", "s3")
    assert notify.send(ALERT)["webhook"]["ok"]
    call = h.calls[0]
    expect = "sha256=" + hmac.new(b"s3", call["body"], hashlib.sha256).hexdigest()
    assert {k.lower(): v for k, v in call["headers"].items()}["x-thermotrace-signature"] == expect and json.loads(call["body"])["alert"]["alert_id"] == 7


def test_email_uses_smtp_with_login(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["conn"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            sent["tls"] = True

        def login(self, u, p):
            sent["login"] = (u, p)

        def send_message(self, msg):
            sent["msg"] = msg

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    for k, v in {"SMTP_HOST": "mail.example", "SMTP_PORT": "2525", "SMTP_USER": "u", "SMTP_PASSWORD": "p",
                 "ALERT_EMAIL_FROM": "tt@example", "ALERT_EMAIL_TO": "ops@example"}.items():
        monkeypatch.setenv(k, v)
    assert notify.send(ALERT)["email"]["ok"]
    assert sent["conn"] == ("mail.example", 2525) and sent["tls"] and sent["login"] == ("u", "p")
    assert sent["msg"]["To"] == "ops@example" and "HIGH alert" in sent["msg"]["Subject"]


def test_one_failing_channel_does_not_stop_the_others(monkeypatch, server):
    srv, h = server
    monkeypatch.setenv("ALERT_WEBHOOK_URL", f"http://127.0.0.1:{srv.server_port}/hook")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_BASE", "http://127.0.0.1:1")          # nothing listens there
    res = notify.send(ALERT)
    assert res["webhook"]["ok"] and res["telegram"]["ok"] is False


# ---------------------------------------------------------------- PostGIS: dedupe, delivery, decisions -> labels
ADMIN_URL = os.getenv("TEST_DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
pg = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture()
def conn():
    import psycopg2
    from pathlib import Path
    admin = psycopg2.connect(ADMIN_URL)
    admin.autocommit = True
    name = f"tt_alerts_{uuid.uuid4().hex[:8]}"
    admin.cursor().execute(f'CREATE DATABASE "{name}"')
    dsn = ADMIN_URL.rsplit("/", 1)[0] + "/" + name
    c = psycopg2.connect(dsn)
    mig = Path(__file__).resolve().parents[2] / "sql" / "migrations"
    with c.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        for f in ("004_thermal.sql", "007_labels.sql", "010_alerts.sql"):
            cur.execute((mig / f).read_text(encoding="utf-8"))
    c.commit()
    yield c
    c.close()
    admin.cursor().execute(f'DROP DATABASE "{name}" WITH (FORCE)')
    admin.close()


def _fire_df(**over):
    return _df(_src(**{"frp_z": 4.0, "frp_max": 60.0, **over}))


@pg
def test_cycle_creates_one_alert_and_dedupes_the_next_cycles_for_six_hours(conn, monkeypatch):
    from src.alerts import store
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    fake = lambda a, ch: (sent.append((a["alert_id"], list(ch))) or {c: {"ok": True, "at": "x", "error": None} for c in ch})  # noqa: E731
    s1 = store.process_cycle(conn, _fire_df(), NOW, send=fake)
    s2 = store.process_cycle(conn, _fire_df(frp_max=90.0), NOW + timedelta(hours=5), send=fake)
    s3 = store.process_cycle(conn, _fire_df(last_seen=pd.Timestamp(NOW + timedelta(hours=7))), NOW + timedelta(hours=7), send=fake)
    assert (s1["created"], s1["delivered"]) == (1, 1)
    assert (s2["created"], s2["deduplicated"], s2["delivered"]) == (0, 1, 0)          # inside the window: refreshed, not re-sent
    assert s3["created"] == 1                                                         # window over: a new alert
    assert len(sent) == 2
    with conn.cursor() as cur:
        cur.execute("SELECT peak_frp, severity FROM alerts ORDER BY alert_id LIMIT 1")
        assert cur.fetchone() == (90.0, "critical")                                   # the refresh kept the strongest values


@pg
def test_failed_delivery_is_retried_next_cycle_then_recorded(conn, monkeypatch):
    from src.alerts import store
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    outcomes = iter([{"telegram": {"ok": False, "at": "x", "error": "boom"}}, {"telegram": {"ok": True, "at": "y", "error": None}}])
    send = lambda a, ch: next(outcomes)  # noqa: E731
    assert store.process_cycle(conn, _fire_df(), NOW, send=send)["failed"] == 1
    assert store.deliver_pending(conn, NOW + timedelta(minutes=10), send=send)["delivered"] == 1
    with conn.cursor() as cur:
        cur.execute("SELECT delivery->'telegram'->>'ok', delivery->'telegram'->>'attempts' FROM alerts")
        assert cur.fetchone() == ("true", "2")


@pg
def test_decision_is_stored_and_mirrored_into_labels(conn):
    from src.alerts import store
    store.process_cycle(conn, _fire_df(), NOW, send=lambda a, ch: {})
    with conn.cursor() as cur:
        cur.execute("SELECT alert_id FROM alerts")
        aid = cur.fetchone()[0]
    out = store.record_decision(conn, aid, "confirmed", "smoke visible in imagery", "analyst-1")
    assert out["label"] == "industrial_fire"
    with conn.cursor() as cur:
        cur.execute("SELECT label, labelled_by, rule_version, split, unit_type, detail->>'note', ST_X(geometry) FROM labels")
        assert cur.fetchall() == [("industrial_fire", "analyst:analyst-1", "analyst-v1", "unused", "incident", "smoke visible in imagery", 83.0)]
        cur.execute("SELECT status FROM alerts")
        assert cur.fetchone()[0] == "confirmed"
    store.record_decision(conn, aid, "rejected", None, "analyst-2")                     # a changed mind: latest wins, history kept
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), max(label) FROM labels")
        assert cur.fetchone() == (1, "not_industrial_fire")
        cur.execute("SELECT count(*) FROM alert_decisions")
        assert cur.fetchone()[0] == 2
    a = None
    with conn.cursor() as cur:
        a = store.get_alert(cur, aid)
    kinds = [e["kind"] for e in a["timeline"]]
    assert kinds.count("decision") == 2 and "first_detection" in kinds and "last_detection" in kinds and "alert" in kinds
    with pytest.raises(ValueError):
        store.record_decision(conn, aid, "maybe", None, None)
    with pytest.raises(LookupError):
        store.record_decision(conn, 99999, "confirmed", None, None)


@pg
def test_test_alert_is_flagged_and_does_not_block_a_real_alert_for_that_facility(conn):
    from src.alerts import store
    a = store.create_test_alert(conn, send=lambda a, ch: {"telegram": {"ok": True, "at": "x", "error": None}}, channels=["telegram"])
    assert a["is_test"] and a["delivery"]["telegram"]["ok"]
    assert store.process_cycle(conn, _fire_df(), NOW, send=lambda a, ch: {})["created"] == 1


@pg
def test_burst_guard_defers_the_excess(conn, monkeypatch):
    from src.alerts import store
    monkeypatch.setenv("ALERT_MAX_PER_CYCLE", "2")
    rows = [_src(source_id=f"s{i}", site_ref=f"osm:{i}", frp_z=4.0, frp_max=50 + i) for i in range(5)]
    s = store.process_cycle(conn, _df(*rows), NOW, send=lambda a, ch: {})
    assert (s["created"], s["deferred"]) == (2, 3)
    assert store.process_cycle(conn, _df(*rows), NOW + timedelta(minutes=10), send=lambda a, ch: {})["created"] == 2   # next cycle continues


def test_a_far_facility_neither_names_the_alert_nor_merges_unrelated_sources():
    far = _src(source_id="x", site_within=False, site_name=float("nan"), class_scores={"industrial_fire_candidate": 0.9})
    near = _src(source_id="y", class_scores={"industrial_fire_candidate": 0.9})
    out = {c.source_id: c for c in rules.evaluate(_df(far, near), NOW)}
    assert out["x"].facility_key == "src:x" and "nan" not in out["x"].summary and "unmapped location" in out["x"].summary
    assert out["y"].facility_key == "osm:1"


def test_uuid_and_id_like_names_are_not_shown_as_the_place():
    c = rules.evaluate(_df(_src(site_name="4777cc0c-6601-548d-a1ed-9603fa34fd5c", site_ref="osm:9", site_kind="gas_flare",
                                class_scores={"industrial_fire_candidate": 0.9})), NOW)[0]
    assert "4777cc0c" not in c.summary and "an unnamed gas flare" in c.summary


def test_field_burning_signature_never_alerts_even_at_a_mapped_facility():
    field = _src(site_kind="brick_kiln", lc_crop=0.97, night_fraction=0.0, frp_z=9.0, class_scores={"industrial_fire_candidate": 0.9})
    night_fire = _src(site_kind="brick_kiln", lc_crop=0.97, night_fraction=0.7, frp_z=9.0)      # cropland but burning at night: not a field fire
    built_up = _src(site_kind="chemical", lc_crop=0.02, night_fraction=0.0, frp_z=9.0)           # a daytime fire in an industrial estate
    skipped: list = []
    out = rules.evaluate(_df(dict(field, source_id="f"), dict(night_fire, source_id="n", site_ref="osm:2"),
                             dict(built_up, source_id="b", site_ref="osm:3")), NOW, vetoed=skipped)
    assert skipped == ["f"] and sorted(c.source_id for c in out) == ["b", "n"]
