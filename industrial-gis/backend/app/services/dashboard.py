"""One-screen summary for the dashboard tab: alerts today, class mix over time, top states, model card.

PostGIS mode reads ``alerts``, ``thermal_sources`` and ``thermal_runs`` (one run per published FIRMS batch, each holding its
class counts); without a database the same card is built from the current snapshot and the time series is empty - nothing
is invented.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

INDUSTRIAL = ("industrial_persistent", "industrial_fire_candidate", "unmapped_persistent")
MAX_SERIES = 48


def model_card(metrics: dict[str, Any] | None) -> dict[str, Any] | None:
    """The numbers a reader needs from models/metrics.json, none of the detail."""
    if not metrics:
        return None
    r = metrics.get("results") or {}
    ci = (r.get("bootstrap") or {}).get("ci") or {}
    inc = metrics.get("incidents") or {}
    op = next((o for o in inc.get("operating_points", []) if o.get("false_alarms_per_day") == 0.25), None)
    return {
        "model_version": metrics.get("model_version"),
        "macro_f1": {"model": (r.get("model") or {}).get("macro_f1"), "rules": (r.get("rules") or {}).get("macro_f1"),
                     "blind": (r.get("blind") or {}).get("macro_f1")},
        "macro_f1_ci": {k: ci.get(k) for k in ("model", "rules", "blind")},
        "test_units": (metrics.get("split") or {}).get("test"),
        "incidents": {"verified": inc.get("n_incidents"), "with_detection": inc.get("n_with_detection"),
                      "model_recall_of_detected_at_0.25_false_alarms_per_day": None if op is None else op.get("recall_of_detected"),
                      "rules_recall_of_detected": (inc.get("rules") or {}).get("recall_of_detected")},
        "caveat": metrics.get("leakage_note") or "Labels are rule-derived: the macro-F1 is agreement with the labelling rules.",
    }


def _downsample(rows: list[dict], n: int = MAX_SERIES) -> list[dict]:
    if len(rows) <= n:
        return rows
    idx = sorted({round(i * (len(rows) - 1) / (n - 1)) for i in range(n)})
    return [rows[i] for i in idx]


def from_postgis(conn, metrics: dict[str, Any] | None, days: int = 14) -> dict[str, Any]:
    out: dict[str, Any] = {"backend": "postgis", "generated_at": datetime.now(timezone.utc).isoformat()}
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), COALESCE(sum(n_detections), 0), max(last_seen) FROM thermal_sources WHERE is_current")
        n, det, last = cur.fetchone()
        out["totals"] = {"sources": n, "detections": int(det), "newest_detection": last.isoformat() if last else None}
        cur.execute("SELECT thermal_class, count(*) FROM thermal_sources WHERE is_current GROUP BY 1 ORDER BY 2 DESC")
        out["by_class"] = {k: v for k, v in cur.fetchall()}
        cur.execute("SELECT COALESCE(state, 'Unknown') AS st, count(*) AS n, "
                    "count(*) FILTER (WHERE thermal_class = ANY(%s)) AS ind, count(*) FILTER (WHERE alert_level IS NOT NULL) AS al "
                    "FROM thermal_sources WHERE is_current GROUP BY 1 ORDER BY n DESC LIMIT 10", (list(INDUSTRIAL),))
        out["top_states"] = [{"state": s, "sources": a, "industrial": b, "flagged": c} for s, a, b, c in cur.fetchall()]
        cur.execute("SELECT severity, count(*) FROM alerts WHERE NOT is_test AND created_at >= date_trunc('day', now() AT TIME ZONE 'utc') "
                    "AT TIME ZONE 'utc' GROUP BY 1")
        today = {k: v for k, v in cur.fetchall()}
        cur.execute("SELECT status, count(*) FROM alerts WHERE NOT is_test GROUP BY 1")
        by_status = {k: v for k, v in cur.fetchall()}
        cur.execute("SELECT (created_at AT TIME ZONE 'utc')::date AS d, count(*) FROM alerts WHERE NOT is_test AND created_at >= now() - interval '7 days' "
                    "GROUP BY 1 ORDER BY 1")
        out["alerts"] = {"today": sum(today.values()), "today_by_severity": today, "open": by_status.get("open", 0), "by_status": by_status,
                         "last_7_days": [{"date": d.isoformat(), "count": c} for d, c in cur.fetchall()]}
        cur.execute("SELECT finished_at, summary -> 'by_class' FROM thermal_runs WHERE status = 'success' AND summary ? 'by_class' "
                    "AND finished_at >= now() - make_interval(days => %s) ORDER BY finished_at", (days,))
        rows = [{"at": at.isoformat(), "by_class": bc} for at, bc in cur.fetchall() if isinstance(bc, dict)]
        out["class_mix"] = _downsample(rows)
    out["model"] = model_card(metrics)
    return out


def from_features(features: list[dict[str, Any]], metrics: dict[str, Any] | None) -> dict[str, Any]:
    by_class: dict[str, int] = {}
    states: dict[str, dict[str, int]] = {}
    for f in features:
        p = f["properties"]
        by_class[p["thermal_class"]] = by_class.get(p["thermal_class"], 0) + 1
        s = states.setdefault(p.get("state") or "Unknown", {"sources": 0, "industrial": 0, "flagged": 0})
        s["sources"] += 1
        s["industrial"] += p["thermal_class"] in INDUSTRIAL
        s["flagged"] += bool(p.get("alert_level"))
    top = sorted(({"state": k, **v} for k, v in states.items()), key=lambda x: -x["sources"])[:10]
    return {"backend": "snapshot", "generated_at": datetime.now(timezone.utc).isoformat(),
            "totals": {"sources": len(features), "detections": sum(int(f["properties"].get("n_detections", 0)) for f in features),
                       "newest_detection": max((f["properties"].get("last_seen") for f in features), default=None)},
            "by_class": by_class, "top_states": top,
            "alerts": {"today": 0, "today_by_severity": {}, "open": 0, "by_status": {}, "last_7_days": [], "note": "Alerts need the PostGIS stack."},
            "class_mix": [], "model": model_card(metrics)}
