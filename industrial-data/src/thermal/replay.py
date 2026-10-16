"""Replay archived FIRMS detections through the *live* pipeline on a virtual clock.

    python -m src.thermal.cli replay --scenario vatva-gidc --speed 60x
    python -m src.thermal.cli replay --from 2025-11-01 --to 2025-11-03 --bbox 73.8,29.5,77.0,32.6 --speed 60x

The only thing that differs from the live worker is where the detections come from: each detection becomes visible
``latency_hours`` after its acquisition time (FIRMS lags the overpass by about 3 h) and a tick of ``step_minutes`` of
virtual time is one worker cycle - cluster, facility match, land cover, rule + model classification, baseline z-score,
PostGIS commit with the NOTIFY the API turns into an SSE event, and the alert rules with their 6 h deduplication.
The baseline is built *only* from detections before the replay start, so nothing the replay shows was known in advance.

Output goes to a separate database (default ``<dbname>_replay``) and a separate snapshot directory, never to the live
ones; ``--init-db`` creates and migrates that database. Alerts are not sent to Telegram / webhook / email unless
``--deliver`` is given (delivery is exercised by the alert tests; a replay of 2019 must not message anyone).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import baseline as baseline_mod
from . import pipeline, store

logger = logging.getLogger("thermotrace.replay")

REPO = Path(__file__).resolve().parents[3]
DATA_ROOT = Path(os.getenv("THERMAL_REPLAY_DATA", REPO / "data"))
SCENARIO_FILE = Path(os.getenv("THERMAL_REPLAY_SCENARIOS", REPO / "industrial-data" / "replay" / "scenarios.json"))
INCIDENTS_CSV = Path(os.getenv("THERMAL_REPLAY_INCIDENTS", REPO / "industrial-data" / "data_labels" / "incidents_verified.csv"))
INDUSTRIAL_CLASSES = ("industrial_fire_candidate", "industrial_persistent")


def parse_speed(text: str | float | None) -> float:
    """'60x' -> 60.0 virtual seconds per real second; 'max' / '0' -> no waiting."""
    if text is None:
        return 60.0
    t = str(text).strip().lower()
    if t in ("max", "inf", "0", ""):
        return float("inf")
    return float(t[:-1] if t.endswith("x") else t)


@dataclass
class Scenario:
    id: str
    title: str
    start: str                      # first virtual day (UTC, inclusive)
    end: str                        # last virtual day (UTC, inclusive)
    source: str = "archive"         # "archive" (data/archive) or "incident" (data/incidents/units/<id>.parquet)
    incident_id: str | None = None
    bbox: tuple[float, float, float, float] | None = None   # lon_min, lat_min, lon_max, lat_max
    focus: tuple[float, float, float] | None = None        # lat, lon, radius_km: where the expected source is
    expect: dict = field(default_factory=dict)             # {"classes": [...], "alert": true/false, "note": "..."}
    step_minutes: int | None = None                        # per-scenario cycle length (large areas: fewer, longer ticks)
    note: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        d = dict(d)
        for k in ("bbox", "focus"):
            if d.get(k) is not None:
                d[k] = tuple(float(x) for x in d[k])
        return cls(**d)


def load_scenarios(path: Path = SCENARIO_FILE) -> dict[str, Scenario]:
    items = json.loads(Path(path).read_text(encoding="utf-8"))["scenarios"]
    out = {s["id"]: Scenario.from_dict(s) for s in items}
    for sc in out.values():                        # an incident scenario looks where the incident was located
        if sc.source == "incident" and sc.focus is None:
            inc = pd.read_csv(INCIDENTS_CSV).set_index("incident_id").loc[sc.incident_id]
            sc.focus = (float(inc.lat), float(inc.lon), float(inc.radius_km) + 0.5)
    return out


def incident_scenario(incident_id: str, days_before: int = 2, days_after: int = 2) -> Scenario:
    """A scenario for any verified incident: its location, event date -2 d .. +2 d, expected = industrial class + alert."""
    inc = pd.read_csv(INCIDENTS_CSV).set_index("incident_id").loc[incident_id]
    d = date.fromisoformat(str(inc.event_date))
    return Scenario(
        id=incident_id.lower(), title=f"{incident_id} {inc.facility} ({inc.event_date})", source="incident", incident_id=incident_id,
        start=(d - timedelta(days=days_before)).isoformat(), end=(d + timedelta(days=days_after)).isoformat(),
        focus=(float(inc.lat), float(inc.lon), float(inc.radius_km) + 0.5),
        expect={"classes": list(INDUSTRIAL_CLASSES), "alert": True})


# --------------------------------------------------------------------------------------------------------- data
def _with_ids(det: pd.DataFrame) -> pd.DataFrame:
    det = det.copy()
    det["acq_datetime"] = pd.to_datetime(det["acq_datetime"], utc=True)
    det["confidence"] = det["confidence"].fillna("nominal")
    det["detection_id"] = [baseline_mod.detection_id(i, la, lo, ts)
                           for i, la, lo, ts in zip(det.instrument, det.latitude, det.longitude, det.acq_datetime)]
    det["satellite"] = det["instrument"].str.split("_").str[1]
    return det.drop_duplicates("detection_id").sort_values("acq_datetime").reset_index(drop=True)


def _in_bbox(det: pd.DataFrame, bbox) -> pd.DataFrame:
    if bbox is None or det.empty:
        return det
    x0, y0, x1, y1 = bbox
    return det[(det.longitude >= x0) & (det.longitude <= x1) & (det.latitude >= y0) & (det.latitude <= y1)]


def load_detections(s: Scenario, data_root: Path = DATA_ROOT) -> pd.DataFrame:
    """Every detection the scenario can use (baseline history before the start included)."""
    if s.source == "incident":
        det = pd.read_parquet(data_root / "incidents" / "units" / f"{s.incident_id}.parquet")
    else:
        det = baseline_mod.load_archives([data_root / "archive" / "snpp", data_root / "archive" / "noaa20"])
        if det.empty:
            raise FileNotFoundError(f"no FIRMS archive under {data_root / 'archive'}")
        det = _in_bbox(det, s.bbox)
    if det.empty:
        return det
    return _with_ids(det)


def clock_range(s: Scenario) -> tuple[pd.Timestamp, pd.Timestamp]:
    return (pd.Timestamp(s.start, tz="UTC"), pd.Timestamp(s.end, tz="UTC") + pd.Timedelta(days=1))


# --------------------------------------------------------------------------------------------------------- database
def replay_dsn(live_dsn: str | None = None) -> str:
    env = os.getenv("REPLAY_DATABASE_URL")
    if env:
        return env.replace("postgresql+psycopg2://", "postgresql://")
    dsn = (live_dsn or os.environ["DATABASE_URL"]).replace("postgresql+psycopg2://", "postgresql://")
    head, _, name = dsn.rpartition("/")
    name, _, query = name.partition("?")
    return f"{head}/{name}_replay" + (f"?{query}" if query else "")


def init_db(dsn: str) -> None:
    """Create the replay database next to the live one (if missing) and bring it to the current schema."""
    import psycopg2
    from src.sync.db import run_migrations
    head, _, name = dsn.rpartition("/")
    name = name.partition("?")[0]
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError("replay database name must be letters, digits and underscores")
    admin = psycopg2.connect(f"{head}/postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
        if cur.fetchone() is None:
            cur.execute(f'CREATE DATABASE "{name}"')
            logger.info("created database %s", name)
    admin.close()
    logger.info("migrations applied to %s: %s", name, run_migrations(dsn))


def reset_db(dsn: str) -> None:
    """Empty the replay tables. Refuses a database that is not named *replay*: the live data must never be wiped."""
    import psycopg2
    name = dsn.rpartition("/")[2].partition("?")[0]
    if "replay" not in name:
        raise RuntimeError(f"refusing to reset {name!r}: only databases with 'replay' in the name are replay databases")
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE alerts CASCADE")
        cur.execute("TRUNCATE thermal_detections, thermal_sources CASCADE")
        cur.execute("DELETE FROM labels WHERE rule_version = 'analyst-v1'")
        cur.execute("UPDATE data_revision SET revision = revision + 1, changed_at = NOW() WHERE id = 1")


# --------------------------------------------------------------------------------------------------------- run
@dataclass
class Tick:
    clock: pd.Timestamp
    sources: int
    new_detections: int
    alerts_created: int
    alerts_dedup: int


def _near(src: pd.DataFrame, focus) -> pd.DataFrame:
    if focus is None or src.empty:
        return src.iloc[0:0]
    lat, lon, km = focus
    dy = (src.lat - lat) * 111.32
    dx = (src.lon - lon) * 111.32 * np.cos(np.radians(lat))
    return src[np.hypot(dx, dy) <= km]


def run(s: Scenario, *, speed: float = float("inf"), step_minutes: int = 60, latency_hours: float = 3.0, window_days: int = 14,
        dsn: str | None = None, out_dir: Path | None = None, sites=None, model=None, facilities=None, use_landcover: bool = True,
        deliver: bool = False, imagery: bool = False, data_root: Path = DATA_ROOT, cache_dir: Path | None = None,
        det: pd.DataFrame | None = None, progress=print, sleep=time.sleep) -> dict:
    """Run one scenario; returns the report (per-tick log, alerts, class of the expected source)."""
    from src.alerts import notify, store as alert_store
    t_start, t_end = clock_range(s)
    det = load_detections(s, data_root) if det is None else det
    out_dir = Path(out_dir or DATA_ROOT / "thermal" / "replay")
    cache_dir = Path(cache_dir or DATA_ROOT / "thermal")
    report: dict = {"scenario": s.id, "title": s.title, "start": s.start, "end": s.end, "expect": s.expect, "ticks": [], "alerts": []}
    if det.empty:
        report["error"] = "no detections for this scenario"
        return report
    history = det[det.acq_datetime < t_start]
    index = baseline_mod.BaselineIndex.from_detections(history, sites, anchor=t_start.date()) if len(history) else None
    report["baseline"] = {"history_detections": int(len(history)), "entities": 0 if index is None else len(index),
                          "covered_days": 0 if index is None else index.covered_days}
    det = det[det.acq_datetime >= t_start - pd.Timedelta(days=window_days)].copy()
    det["available_at"] = det.acq_datetime + pd.Timedelta(hours=latency_hours)

    conn = None
    if dsn:
        import psycopg2
        conn = psycopg2.connect(dsn, application_name="thermotrace-replay")
    send = notify.send if deliver else (lambda alert, channels=None: {})
    seen = 0
    simulated: dict[str, pd.Timestamp] = {}
    wall0 = time.monotonic()
    clock = t_start
    step = pd.Timedelta(minutes=step_minutes)
    final = None
    try:
        while clock <= t_end:
            avail = det[(det.available_at <= clock) & (det.acq_datetime >= clock - pd.Timedelta(days=window_days))]
            if len(avail) and len(det[det.available_at <= clock]) != seen:
                new = int(len(det[det.available_at <= clock]) - seen)
                seen = len(det[det.available_at <= clock])
                result = pipeline.analyse(avail.drop(columns=["available_at"]), sites, window_days=window_days, use_landcover=use_landcover,
                                          cache_dir=cache_dir, clip=False, baseline=index, model=model, facilities=facilities)
                if imagery:
                    _imagery(result, clock, out_dir)
                created = dedup = 0
                if conn is None:                      # no database: apply the same rules and 6 h deduplication in memory
                    created, dedup = _simulate_alerts(result.sources, clock, report, simulated)
                if conn is not None:
                    store.write_snapshot(out_dir, result)
                    # status first: the SSE event that persist_postgis queues must find the replay clock already in place
                    store.write_status(out_dir, state="idle", last_success_at=datetime.now(timezone.utc), last_check_at=datetime.now(timezone.utc),
                                       latest_detection_at=avail.acq_datetime.max(), summary=result.summary,
                                       replay={"active": True, "scenario": s.id, "title": s.title, "virtual_time": clock.isoformat(),
                                               "from": t_start.isoformat(), "to": t_end.isoformat(), "speed": None if speed == float("inf") else speed,
                                               "latency_hours": latency_hours})
                    run_id = store.persist_postgis(dsn, result)
                    stats = alert_store.process_cycle(conn, result.sources, now=clock.to_pydatetime(), send=send)
                    created, dedup = stats["created"], stats["deduplicated"]
                tick = Tick(clock, len(result.sources), new, created, dedup)
                report["ticks"].append({"clock": clock.isoformat(), "sources": tick.sources, "new_detections": new,
                                        "alerts_created": created, "alerts_deduplicated": dedup})
                final = result
                focus_src = _near(result.sources, s.focus)
                top = None if focus_src.empty else focus_src.sort_values("frp_max", ascending=False).iloc[0]
                progress(f"{clock:%Y-%m-%d %H:%M}Z  +{new:>3} detections  {tick.sources:>3} sources  alerts +{created}"
                         + ("" if top is None else f"  | focus: {top.thermal_class} {top.frp_max:.1f} MW"
                            + (f" z={top.frp_z:+.1f}" if pd.notna(top.get('frp_z')) else "")))
                if top is not None:
                    report.setdefault("focus_log", []).append({"clock": clock.isoformat(), "class": top.thermal_class, "model_class": top.get("model_class"),
                                                               "frp_max": float(top.frp_max), "frp_z": None if pd.isna(top.get("frp_z")) else float(top.frp_z),
                                                               "n_detections": int(top.n_detections), "source_id": top.source_id})
            clock += step
            if speed != float("inf"):
                target = (clock - t_start).total_seconds() / speed
                wait = target - (time.monotonic() - wall0)
                if wait > 0:
                    sleep(wait)
        if conn is not None:
            store.write_status(out_dir, replay={"active": False, "scenario": s.id, "title": s.title, "virtual_time": t_end.isoformat(),
                                                "from": t_start.isoformat(), "to": t_end.isoformat(), "finished": True})
            _notify(conn)                                     # the dashboard re-reads the status: "finished"
    finally:
        if conn is not None:
            report["alerts"] = _alerts(conn)
            conn.close()
        else:
            report["alerts"] = report.get("simulated_alerts", [])
    report["final"] = _final(final, s)
    return report


def _notify(conn) -> None:
    """Tell API clients something changed (same channel and payload shape persist_postgis uses)."""
    with conn.cursor() as cur:
        cur.execute("UPDATE data_revision SET revision = revision + 1, changed_at = NOW() WHERE id = 1 RETURNING revision")
        rev = cur.fetchone()[0]
        cur.execute("SELECT pg_notify('thermotrace_changes', %s)", (json.dumps({"revision": rev, "source": "nasa_firms", "kind": "thermal",
                                                                              "committed_at": datetime.now(timezone.utc).isoformat()}),))
    conn.commit()


def _simulate_alerts(sources: pd.DataFrame, clock: pd.Timestamp, report: dict, last: dict) -> tuple[int, int]:
    from src.alerts import rules as alert_rules
    created = dedup = 0
    window = pd.Timedelta(hours=alert_rules.settings()["dedupe_hours"])
    for c in alert_rules.evaluate(sources, now=clock.to_pydatetime()):
        if c.facility_key in last and clock - last[c.facility_key] <= window:
            dedup += 1
            continue
        last[c.facility_key] = clock
        created += 1
        report.setdefault("simulated_alerts", []).append({
            "severity": c.severity, "trigger": c.trigger, "site_name": alert_rules.display_name(c.row), "site_kind": c.row.get("site_kind"),
            "peak_frp": c.frp_max, "p_fire": c.p_fire, "frp_z": c.frp_z, "at": clock.isoformat(), "summary": c.summary,
            "lat": float(c.row["lat"]), "lon": float(c.row["lon"])})
    return created, dedup


def _imagery(result, clock, out_dir: Path) -> None:
    try:
        from src.imagery import enrich
        from src.alerts import rules as alert_rules
        priority = [a.source_id for a in alert_rules.evaluate(result.sources, now=clock.to_pydatetime())]
        result.sources, _ = enrich.enrich_sources(result.sources, Path(os.getenv("THERMAL_IMAGERY_DIR", out_dir.parent / "imagery")),
                                                  per_cycle=int(os.getenv("THERMAL_IMAGERY_PER_CYCLE", "4")), priority_ids=priority)
    except Exception as exc:  # noqa: BLE001 - imagery is evidence, never a reason to stop a replay
        logger.warning("imagery skipped: %s", exc)


def _alerts(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT alert_id, severity, trigger, site_name, site_kind, peak_frp, p_fire, frp_z, created_at, summary, "
                    "ST_Y(geometry), ST_X(geometry) FROM alerts WHERE NOT is_test ORDER BY created_at")
        return [{"alert_id": a, "severity": sv, "trigger": tr, "site_name": nm, "site_kind": k, "peak_frp": pk, "p_fire": pf, "frp_z": z,
                 "at": at.isoformat(), "summary": sm, "lat": la, "lon": lo}
                for a, sv, tr, nm, k, pk, pf, z, at, sm, la, lo in cur.fetchall()]


def _final(result, s: Scenario) -> dict:
    if result is None:
        return {}
    src = result.sources
    out = {"sources": int(len(src)), "by_class": src.thermal_class.value_counts().to_dict(),
           "by_model_class": src.model_class.value_counts().to_dict() if "model_class" in src else {}}
    near = _near(src, s.focus)
    if len(near):
        t = near.sort_values("frp_max", ascending=False).iloc[0]
        out["focus"] = {"source_id": t.source_id, "class": t.thermal_class, "model_class": t.get("model_class"), "frp_max": float(t.frp_max),
                        "site_kind": t.get("site_kind"), "site_within": bool(t.get("site_within")), "n_detections": int(t.n_detections)}
    return out


def verdict(s: Scenario, report: dict) -> dict:
    """Did the replay produce the expected class and alert? Whole-area scenarios judge the class mix instead."""
    exp = s.expect or {}
    res: dict = {}
    log = report.get("focus_log", [])
    focus_classes = {x["class"] for x in log}
    if exp.get("classes") and s.focus is not None:
        # the class the analyst would have seen: at the first alert if there was one, else at the end of the replay
        at_alert = [a for a in report.get("alerts", []) if _alert_near(a, report, s)]
        if at_alert and log:
            first = min(a["at"] for a in at_alert)
            seen = [x for x in log if x["clock"] <= first]
            shown = seen[-1]["class"] if seen else log[0]["class"]
        else:
            shown = log[-1]["class"] if log else None
        res["class_shown"] = shown
        res["class_ok"] = shown in exp["classes"]
        res["classes_seen"] = sorted(focus_classes)
    if exp.get("dominant_class"):
        by = (report.get("final") or {}).get("by_class", {})
        top = max(by, key=by.get) if by else None
        res["dominant_class"] = top
        res["class_ok"] = top == exp["dominant_class"]
    focus_alert = [a for a in report.get("alerts", []) if _alert_near(a, report, s)] if s.focus else report.get("alerts", [])
    res["alerts_total"] = len(report.get("alerts", []))
    res["alerts_at_focus"] = len(focus_alert)
    if "alert" in exp:
        res["alert_ok"] = bool(focus_alert) if exp["alert"] else res["alerts_total"] == 0
    if "max_alerts" in exp:
        res["alert_ok"] = res["alerts_total"] <= exp["max_alerts"]
    res["pass"] = all(v for k, v in res.items() if k in ("class_ok", "alert_ok"))
    return res


def _alert_near(alert: dict, report: dict, s: Scenario) -> bool:
    lat, lon, km = s.focus
    dy = (alert["lat"] - lat) * 111.32
    dx = (alert["lon"] - lon) * 111.32 * np.cos(np.radians(lat))
    return float(np.hypot(dx, dy)) <= km


# --------------------------------------------------------------------------------------------------------- CLI
def add_arguments(ap) -> None:
    g = ap.add_argument_group("replay")
    g.add_argument("--scenario", help="id from replay/scenarios.json")
    g.add_argument("--incident", help="verified incident id (IN-024): replays event date -2 d .. +2 d at its location")
    g.add_argument("--from", dest="date_from", help="first virtual day (YYYY-MM-DD, UTC)")
    g.add_argument("--to", dest="date_to", help="last virtual day (YYYY-MM-DD, UTC)")
    g.add_argument("--bbox", help="lon_min,lat_min,lon_max,lat_max (archive replays)")
    g.add_argument("--speed", default="60x", help="virtual seconds per real second ('60x'); 'max' = as fast as possible")
    g.add_argument("--step-minutes", type=int, default=None, help="virtual minutes per worker cycle (default: scenario value, else 60)")
    g.add_argument("--latency-hours", type=float, default=3.0, help="delay between acquisition and availability")
    g.add_argument("--deliver", action="store_true", help="send alerts to the configured channels (default: off)")
    g.add_argument("--imagery", action="store_true", help="attach Sentinel-2 evidence to alert sources (network, slow)")
    g.add_argument("--init-db", action="store_true", help="create + migrate the replay database and exit")
    g.add_argument("--no-reset", action="store_true", help="keep what an earlier replay left in the replay database")
    g.add_argument("--no-db", action="store_true", help="classify and evaluate only; no database, snapshot or alerts")
    g.add_argument("--report", help="write the JSON report here")
    g.add_argument("--list", action="store_true", help="list the scenarios")


def resolve(args) -> Scenario:
    if args.scenario:
        scenarios = load_scenarios()
        if args.scenario not in scenarios:
            raise SystemExit(f"unknown scenario {args.scenario!r}; known: {', '.join(scenarios)}")
        s = scenarios[args.scenario]
    elif args.incident:
        s = incident_scenario(args.incident.upper())
    elif args.date_from and args.date_to:
        s = Scenario(id=f"archive-{args.date_from}-{args.date_to}", title=f"FIRMS archive {args.date_from} .. {args.date_to}",
                     start=args.date_from, end=args.date_to)
    else:
        raise SystemExit("give --scenario, --incident, or --from and --to")
    if args.date_from:
        s.start = args.date_from
    if args.date_to:
        s.end = args.date_to
    if args.bbox:
        s.bbox = tuple(float(x) for x in args.bbox.split(","))
    return s


def command(args) -> int:
    from . import cli
    if args.list:
        for sc in load_scenarios().values():
            print(f"{sc.id:<22} {sc.start}..{sc.end}  {sc.title}")
        return 0
    live = os.getenv("DATABASE_URL")
    dsn = None if args.no_db else replay_dsn(live)
    if args.init_db:
        init_db(replay_dsn(live))
        return 0
    s = resolve(args)
    if dsn:
        if not args.no_reset:
            reset_db(dsn)
    sites = cli.load_sites()
    model, facilities = cli.load_model_assets()
    out_dir = Path(args.out) if args.out and "replay" in str(args.out) else DATA_ROOT / "thermal" / "replay"
    report = run(s, speed=parse_speed(args.speed), step_minutes=args.step_minutes if args.step_minutes else (s.step_minutes or 60), latency_hours=args.latency_hours,
                 window_days=args.window_days, dsn=dsn, out_dir=out_dir, sites=sites, model=model, facilities=facilities,
                 use_landcover=not args.no_landcover, deliver=args.deliver, imagery=args.imagery)
    report["verdict"] = verdict(s, report)
    print(json.dumps({"scenario": s.id, "verdict": report["verdict"], "final": report.get("final"), "alerts": len(report["alerts"])}, indent=2, default=str))
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return 0 if report["verdict"].get("pass", True) else 3
