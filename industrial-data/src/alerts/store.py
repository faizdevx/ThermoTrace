"""Alert persistence on PostGIS (DB-API cursors, shared by the worker and the API).

Worker: ``process_cycle`` - evaluate rules on the cycle's sources, deduplicate per facility, store, deliver.
API: ``list_alerts`` / ``get_alert`` / ``record_decision`` - the analyst inbox. Every decision is kept in
``alert_decisions`` and the latest one is mirrored into ``labels`` (the retraining loop).
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Callable

import pandas as pd

from . import notify, rules

logger = logging.getLogger(__name__)

DECISIONS = {"confirmed": "industrial_fire", "rejected": "not_industrial_fire", "unsure": "unsure"}
ANALYST_RULE_VERSION = "analyst-v1"
COLS = ("alert_id", "facility_key", "source_id", "site_ref", "site_name", "site_kind", "state", "trigger", "severity", "p_fire",
        "frp_max", "frp_z", "first_seen", "last_seen", "peak_frp", "peak_at", "summary", "reasons", "imagery", "status",
        "delivery", "is_test", "created_at", "updated_at")
_SELECT = ", ".join(COLS) + ", ST_Y(geometry) AS lat, ST_X(geometry) AS lon"
_TIME_KEYS = ("first_seen", "last_seen", "peak_at", "created_at", "updated_at")


def _clean(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        return pd.Timestamp(v).to_pydatetime()
    return v


def _row(cur, r) -> dict:
    d = dict(zip([c[0] for c in cur.description], r))
    for k in _TIME_KEYS:
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


def _imagery(row: dict) -> dict | None:
    avail = row.get("imagery_available")
    if avail is None or (isinstance(avail, float) and math.isnan(avail)):
        return None
    keys = ["imagery_available", "imagery_scene_id", "imagery_scene_time", "imagery_cloud_percent", "imagery_dnbr_p95",
            "imagery_swir_hot_pixels", "imagery_landsat_excess_k"]
    out = {}
    for k in keys:
        v = _clean(row.get(k))
        out[k] = str(v) if v is not None and k.endswith("_time") else (bool(v) if k == "imagery_available" else v)
    return out


def _json(v) -> str:
    return json.dumps(v, default=str)


# ------------------------------------------------------------------ worker
def _peak(cur, source_id: str, fallback: float | None):
    cur.execute("SELECT frp, acq_datetime FROM thermal_detections WHERE source_id = %s AND frp IS NOT NULL "
                "ORDER BY frp DESC LIMIT 1", (source_id,))
    r = cur.fetchone()
    return (float(r[0]), r[1]) if r else (fallback, None)


_INSERT = (
    "INSERT INTO alerts (facility_key, source_id, geometry, site_ref, site_name, site_kind, state, trigger, severity, "
    "p_fire, frp_max, frp_z, first_seen, last_seen, peak_frp, peak_at, summary, reasons, imagery, created_at) "
    "VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
    "%s::jsonb, %s::jsonb, %s) RETURNING alert_id")


def process_cycle(conn, sources: pd.DataFrame, now: datetime | None = None, send: Callable = notify.send,
                  cfg: dict | None = None) -> dict:
    """Create / refresh alerts for this cycle's sources and deliver the new ones. Returns counters."""
    cfg = cfg or rules.settings()
    now = now or datetime.now(timezone.utc)
    stats = {"candidates": 0, "created": 0, "deduplicated": 0, "delivered": 0, "failed": 0, "deferred": 0,
             "channels": notify.configured()}
    cands = rules.evaluate(sources, now, cfg)
    stats["candidates"] = len(cands)
    max_new = int(os.getenv("ALERT_MAX_PER_CYCLE", "10"))
    window = timedelta(hours=cfg["dedupe_hours"])
    with conn.cursor() as cur:
        for c in cands:
            r = c.row
            img = _imagery(r)
            peak_frp, peak_at = _peak(cur, c.source_id, c.frp_max)
            cur.execute("SELECT alert_id, severity FROM alerts WHERE facility_key = %s AND NOT is_test AND created_at > %s "
                        "ORDER BY created_at DESC LIMIT 1", (c.facility_key, now - window))
            existing = cur.fetchone()
            if existing:                       # same facility inside the window: refresh the row, do not notify again
                level = max(existing[1], c.severity, key=rules.LEVELS.index)
                cur.execute("UPDATE alerts SET last_seen = %s, peak_frp = GREATEST(COALESCE(peak_frp, 0), %s), severity = %s, "
                            "p_fire = %s, frp_z = %s, imagery = COALESCE(%s::jsonb, imagery), updated_at = NOW() "
                            "WHERE alert_id = %s",
                            (_clean(r.get("last_seen")), peak_frp, level, c.p_fire, c.frp_z,
                             _json(img) if img else None, existing[0]))
                stats["deduplicated"] += 1
                continue
            if stats["created"] >= max_new:    # burst guard: the rest are still candidates next cycle
                stats["deferred"] += 1
                continue
            cur.execute(_INSERT, (
                c.facility_key, c.source_id, float(r["lon"]), float(r["lat"]), rules._text(r.get("site_ref")), rules.display_name(r),
                rules._text(r.get("site_kind")), rules._text(r.get("state")), c.trigger, c.severity, c.p_fire, c.frp_max, c.frp_z,
                _clean(r.get("first_seen")), _clean(r.get("last_seen")), peak_frp, peak_at, c.summary,
                _json(list(r.get("reasons") or [])[:8]), _json(img) if img else None, now))
            stats["created"] += 1
    conn.commit()
    d = deliver_pending(conn, now, send)
    stats.update({"delivered": d["delivered"], "failed": d["failed"]})
    return stats


def deliver_pending(conn, now: datetime | None = None, send: Callable = notify.send, max_age_hours: float = 24.0) -> dict:
    """Send every recent alert to each configured channel that has not acknowledged it yet (also retries failures)."""
    now = now or datetime.now(timezone.utc)
    channels = notify.configured()
    out = {"delivered": 0, "failed": 0}
    if not channels:
        return out
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_SELECT} FROM alerts WHERE created_at > %s ORDER BY created_at",
                    (now - timedelta(hours=max_age_hours),))
        alerts = [_row(cur, r) for r in cur.fetchall()]
        for a in alerts:
            todo = [ch for ch in channels if not (a["delivery"].get(ch) or {}).get("ok")
                    and int((a["delivery"].get(ch) or {}).get("attempts", 0)) < 5]
            if not todo:
                continue
            res = send(a, todo)
            delivery = dict(a["delivery"])
            for ch, st in res.items():
                st["attempts"] = int((delivery.get(ch) or {}).get("attempts", 0)) + 1
                delivery[ch] = st
                out["delivered" if st["ok"] else "failed"] += 1
                if not st["ok"]:
                    logger.warning("alert %s via %s failed: %s", a["alert_id"], ch, st["error"])
            cur.execute("UPDATE alerts SET delivery = %s::jsonb, updated_at = NOW() WHERE alert_id = %s",
                        (_json(delivery), a["alert_id"]))
    conn.commit()
    return out


def create_test_alert(conn, send: Callable = notify.send, channels: list[str] | None = None) -> dict:
    """A clearly labelled synthetic alert, delivered right away to every configured channel."""
    summary = "TEST ALERT from ThermoTrace: delivery check, no real fire"
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (facility_key, source_id, geometry, site_name, site_kind, state, trigger, severity, p_fire, "
            "frp_max, first_seen, last_seen, peak_frp, summary, reasons, is_test) "
            "VALUES ('test', 'test', ST_SetSRID(ST_MakePoint(77.209, 28.6139), 4326), 'Delivery test', 'refinery', 'Delhi', "
            "'test', 'high', 0.99, 55, NOW(), NOW(), 55, %s, %s::jsonb, TRUE) RETURNING alert_id",
            (summary, _json(["synthetic alert created by `python -m src.alerts test`"])))
        alert_id = cur.fetchone()[0]
        cur.execute(f"SELECT {_SELECT} FROM alerts WHERE alert_id = %s", (alert_id,))
        a = _row(cur, cur.fetchone())
        res = send(a, channels)
        for st in res.values():
            st["attempts"] = 1
        cur.execute("UPDATE alerts SET delivery = %s::jsonb WHERE alert_id = %s", (_json(res), alert_id))
    conn.commit()
    a["delivery"] = res
    return a


# ------------------------------------------------------------------ API side
def list_alerts(cur, status: str | None = None, limit: int = 100, include_test: bool = True) -> list[dict]:
    where, params = [], []
    if status:
        where.append("status = %s")
        params.append(status)
    if not include_test:
        where.append("NOT is_test")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    cur.execute(f"SELECT {_SELECT} FROM alerts {clause} ORDER BY (status = 'open') DESC, (severity = 'critical') DESC, (severity = 'high') DESC, created_at DESC LIMIT %s",
                (*params, limit))
    return [_row(cur, r) for r in cur.fetchall()]


def get_alert(cur, alert_id: int) -> dict | None:
    cur.execute(f"SELECT {_SELECT} FROM alerts WHERE alert_id = %s", (alert_id,))
    r = cur.fetchone()
    if not r:
        return None
    a = _row(cur, r)
    cur.execute("SELECT id, decision, note, analyst, decided_at, label_id FROM alert_decisions "
                "WHERE alert_id = %s ORDER BY decided_at, id", (alert_id,))
    a["decisions"] = [{**_row(cur, x), "decided_at": x[4].isoformat()} for x in cur.fetchall()]
    a["timeline"] = timeline(a)
    return a


def timeline(a: dict) -> list[dict]:
    """First detection, peak, last detection, imagery, delivery, analyst decisions - in time order."""
    ev: list[dict] = []
    if a.get("first_seen"):
        ev.append({"at": a["first_seen"], "kind": "first_detection", "text": "First detection of this source"})
    if a.get("peak_at"):
        ev.append({"at": a["peak_at"], "kind": "peak", "text": f"Peak power {a['peak_frp']:.1f} MW"})
    if a.get("last_seen"):
        ev.append({"at": a["last_seen"], "kind": "last_detection", "text": "Latest detection"})
    ev.append({"at": a["created_at"], "kind": "alert", "text": f"Alert raised ({a['severity']}, trigger {a['trigger']})"})
    img = a.get("imagery") or {}
    if img.get("imagery_available") and img.get("imagery_scene_time"):
        dn = img.get("imagery_dnbr_p95")
        ev.append({"at": str(img["imagery_scene_time"]), "kind": "imagery",
                   "text": f"Sentinel-2 {img.get('imagery_scene_id')}, {(img.get('imagery_cloud_percent') or 0):.0f}% cloud"
                           + (f", dNBR p95 {dn:+.2f}" if dn is not None else "")})
    for ch, st in (a.get("delivery") or {}).items():
        ev.append({"at": st.get("at") or a["created_at"], "kind": "delivery",
                   "text": f"{ch}: " + ("delivered" if st.get("ok") else f"failed ({st.get('error')})")})
    for d in a.get("decisions") or []:
        ev.append({"at": d["decided_at"], "kind": "decision",
                   "text": f"{d['analyst'] or 'analyst'}: {d['decision']}" + (f" - {d['note']}" if d.get("note") else "")})
    return sorted(ev, key=lambda e: str(e["at"]))


_LABEL_UPSERT = (
    "INSERT INTO labels (label_id, unit_type, source_id, geometry, state, window_start, window_end, label, label_source, "
    "labelled_by, rule_version, split, frp_max, detail) "
    "SELECT %s, 'incident', source_id, geometry, state, %s, %s, %s, %s, %s, %s, 'unused', frp_max, %s::jsonb "
    "FROM alerts WHERE alert_id = %s "
    "ON CONFLICT (label_id) DO UPDATE SET label = EXCLUDED.label, label_source = EXCLUDED.label_source, "
    "labelled_by = EXCLUDED.labelled_by, window_start = EXCLUDED.window_start, window_end = EXCLUDED.window_end, "
    "detail = EXCLUDED.detail, created_at = NOW()")


def record_decision(conn, alert_id: int, decision: str, note: str | None, analyst: str | None) -> dict:
    """Store the decision and mirror it into ``labels`` (one row per alert, latest decision wins)."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
    note = (note or "").strip()[:2000] or None
    analyst = (analyst or "").strip()[:120] or None
    with conn.cursor() as cur:
        cur.execute("SELECT is_test, first_seen, last_seen, created_at, trigger, severity, p_fire, frp_z, site_ref "
                    "FROM alerts WHERE alert_id = %s FOR UPDATE", (alert_id,))
        a = cur.fetchone()
        if not a:
            raise LookupError("alert not found")
        label_id = f"analyst-alert-{alert_id}"
        cur.execute("INSERT INTO alert_decisions (alert_id, decision, note, analyst, label_id) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id", (alert_id, decision, note, analyst, label_id))
        decision_id = cur.fetchone()[0]
        cur.execute("UPDATE alerts SET status = %s, updated_at = NOW() WHERE alert_id = %s", (decision, alert_id))
        first, last = (a[1] or a[3]).date(), (a[2] or a[3]).date()
        detail = {"alert_id": alert_id, "decision_id": decision_id, "note": note, "trigger": a[4], "severity": a[5],
                  "p_fire": a[6], "frp_z": a[7], "site_ref": a[8], "is_test": a[0]}
        cur.execute(_LABEL_UPSERT, (label_id, first, last, DECISIONS[decision], f"analyst:alert-{alert_id}",
                                    f"analyst:{analyst or 'unknown'}", ANALYST_RULE_VERSION, _json(detail), alert_id))
    conn.commit()
    return {"alert_id": alert_id, "decision": decision, "label_id": label_id, "label": DECISIONS[decision]}
