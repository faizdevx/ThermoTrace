"""Thermal sources API data layer.

Two interchangeable backends with identical semantics:

* ``FileBackend``    - reads the atomic snapshot published by ``python -m src.thermal.cli`` (works with no database).
* ``PostgisBackend`` - reads ``thermal_sources`` / ``thermal_detections`` (used when the sync stack is running).

``auto`` picks PostGIS when it is the configured data mode and holds a current run, otherwise the snapshot.
All counts cover *current* thermal sources matching the filters; nothing is hard-coded.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from backend.app.services.postgis_repo import InvalidFilter, _csv_safe, parse_bbox

logger = logging.getLogger(__name__)

CLASS_INFO: dict[str, dict[str, str]] = {
    "industrial_persistent": {"label": "Persistent industrial thermal source", "color": "#dc2626",
        "description": "Repeated detections (often at night) on a mapped industrial facility: flares, furnaces, kilns, thermal plants, burning coal mines."},
    "industrial_fire_candidate": {"label": "Possible industrial fire / abnormal event", "color": "#f97316",
        "description": "Short-lived or new thermal anomaly on a mapped industrial facility. Verify with imagery before acting."},
    "agricultural_burning": {"label": "Agricultural (crop residue) burning", "color": "#eab308",
        "description": "Brief, low-power detections over cropland, typical of field burning."},
    "vegetation_fire": {"label": "Forest / vegetation fire", "color": "#16a34a",
        "description": "Detections over natural vegetation away from mapped facilities."},
    "unmapped_persistent": {"label": "Persistent source, no mapped facility", "color": "#7c3aed",
        "description": "Persistent heat with no known facility in OpenStreetMap: candidate unmapped industry, kiln or landfill for review."},
    "other": {"label": "Other / unclassified", "color": "#64748b",
        "description": "Not enough evidence for a specific cause."},
}
ALERT_RANK = {"watch": 1, "high": 2, "critical": 3}
MAX_LIMIT = 5000


def default_dir() -> Path:
    env = os.getenv("THERMAL_DATA_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[4] / "data" / "thermal" / "live"


class ThermalFilters:
    def __init__(self, *, classes: str | None, bbox: str | None, state: str | None, min_frp: float | None,
                 alerts_only: bool, hours: int | None, kind: str | None = None):
        self.classes = [c.strip() for c in classes.split(",") if c.strip()] if classes else []
        unknown = [c for c in self.classes if c not in CLASS_INFO]
        if unknown:
            raise InvalidFilter(f"unknown thermal_class: {', '.join(unknown)}")
        self.bbox = parse_bbox(bbox)
        self.state, self.min_frp, self.alerts_only, self.hours, self.kind = state, min_frp, alerts_only, hours, kind

    def describe(self) -> dict[str, Any]:
        return {k: v for k, v in {"thermal_class": ",".join(self.classes) or None, "state": self.state,
                                  "min_frp": self.min_frp, "alerts_only": self.alerts_only or None,
                                  "hours": self.hours, "site_kind": self.kind,
                                  "bbox": ",".join(map(str, self.bbox)) if self.bbox else None}.items() if v}


# ── File backend ──────────────────────────────────────────────────────────────────

class FileBackend:
    name = "snapshot"

    def __init__(self, directory: Path | None = None):
        self.dir = directory or default_dir()
        self._lock = threading.Lock()
        self._cache: tuple[float, list[dict], dict] | None = None
        self._det: tuple[float, pd.DataFrame] | None = None

    @property
    def snapshot_path(self) -> Path:
        return self.dir / "thermal_sources.geojson"

    def mtime(self) -> float | None:
        try:
            return self.snapshot_path.stat().st_mtime
        except OSError:
            return None

    def available(self) -> bool:
        return self.mtime() is not None

    def _features(self) -> tuple[list[dict], dict]:
        m = self.mtime()
        if m is None:
            return [], {}
        with self._lock:
            if self._cache and self._cache[0] == m:
                return self._cache[1], self._cache[2]
            data = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            self._cache = (m, data["features"], data.get("metadata", {}))
            return self._cache[1], self._cache[2]

    def _detections(self) -> pd.DataFrame:
        path = self.dir / "thermal_detections.csv"
        try:
            m = path.stat().st_mtime
        except OSError:
            return pd.DataFrame()
        with self._lock:
            if self._det and self._det[0] == m:
                return self._det[1]
            df = pd.read_csv(path)
            df["acq_datetime"] = pd.to_datetime(df["acq_datetime"], utc=True)
            self._det = (m, df)
            return df

    def _match(self, f: ThermalFilters) -> list[dict]:
        feats, meta = self._features()
        latest = None
        if f.hours:
            latest = pd.Timestamp(meta.get("summary", {}).get("latest_detection") or datetime.now(timezone.utc))
            latest = latest.tz_convert("UTC") if latest.tzinfo else latest.tz_localize("UTC")
        out = []
        for feat in feats:
            p = feat["properties"]
            lon, lat = feat["geometry"]["coordinates"]
            if f.classes and p["thermal_class"] not in f.classes:
                continue
            if f.state and (p.get("state") or "").lower() != f.state.lower():
                continue
            if f.min_frp is not None and p["frp_max"] < f.min_frp:
                continue
            if f.alerts_only and not p.get("alert_level"):
                continue
            if f.kind and p.get("site_kind") != f.kind:
                continue
            if f.bbox and not (f.bbox[0] <= lon <= f.bbox[2] and f.bbox[1] <= lat <= f.bbox[3]):
                continue
            if f.hours and (latest - pd.Timestamp(p["last_seen"])).total_seconds() > f.hours * 3600:
                continue
            out.append(feat)
        out.sort(key=lambda x: (ALERT_RANK.get(x["properties"].get("alert_level"), 0), x["properties"]["frp_total"]),
                 reverse=True)
        return out

    def list_sources(self, f: ThermalFilters, limit: int, offset: int) -> tuple[list[dict], int]:
        matched = self._match(f)
        return matched[offset: offset + limit], len(matched)

    def get_source(self, source_id: str) -> dict | None:
        feats, _ = self._features()
        feat = next((x for x in feats if x["properties"]["source_id"] == source_id), None)
        if feat is None:
            return None
        det = self._detections()
        rows = []
        if not det.empty:
            sub = det[det["source_id"] == source_id].sort_values("acq_datetime")
            rows = [{"detection_id": r.detection_id, "time": r.acq_datetime.isoformat(), "instrument": r.instrument,
                     "frp": float(r.frp), "confidence": r.confidence, "daynight": r.daynight,
                     "lat": float(r.latitude), "lon": float(r.longitude)} for r in sub.itertuples()]
        out = json.loads(json.dumps(feat))
        out["properties"]["detections"] = rows
        return out

    def stats(self, f: ThermalFilters) -> dict[str, Any]:
        m = self._match(f)
        by_class: dict[str, int] = {}
        by_state: dict[str, int] = {}
        alerts = {"watch": 0, "high": 0, "critical": 0}
        for feat in m:
            p = feat["properties"]
            by_class[p["thermal_class"]] = by_class.get(p["thermal_class"], 0) + 1
            by_state[p.get("state") or "Unassigned"] = by_state.get(p.get("state") or "Unassigned", 0) + 1
            if p.get("alert_level"):
                alerts[p["alert_level"]] += 1
        return {"total_sources": len(m), "by_class": by_class,
                "by_state": dict(sorted(by_state.items(), key=lambda kv: -kv[1])[:30]), "alerts": alerts,
                "total_frp_mw": round(sum(x["properties"]["frp_total"] for x in m), 1),
                "detections": sum(x["properties"]["n_detections"] for x in m)}

    def run_info(self) -> dict[str, Any]:
        _, meta = self._features()
        status = {}
        p = self.dir / "status.json"
        if p.exists():
            status = json.loads(p.read_text())
        return {"backend": self.name, "summary": meta.get("summary", {}), "status": status,
                "attribution": meta.get("attribution", []), "snapshot_mtime": self.mtime()}


# ── PostGIS backend ──────────────────────────────────────────────────────────────────

class PostgisBackend:
    name = "postgis"

    def available(self) -> bool:
        from sqlalchemy import text
        from backend.app.db import get_engine
        try:
            with get_engine().connect() as c:
                return bool(c.execute(text(
                    "SELECT to_regclass('public.thermal_sources') IS NOT NULL AND "
                    "EXISTS (SELECT 1 FROM thermal_sources WHERE is_current)")).scalar())
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _where(f: ThermalFilters) -> tuple[str, dict]:
        cond, p = ["is_current"], {}
        if f.classes:
            cond.append("thermal_class = ANY(:classes)"); p["classes"] = f.classes
        if f.state:
            cond.append("lower(state) = lower(:state)"); p["state"] = f.state
        if f.min_frp is not None:
            cond.append("frp_max >= :min_frp"); p["min_frp"] = f.min_frp
        if f.alerts_only:
            cond.append("alert_level IS NOT NULL")
        if f.kind:
            cond.append("site_kind = :kind"); p["kind"] = f.kind
        if f.hours:
            cond.append("last_seen >= (SELECT max(last_seen) FROM thermal_sources WHERE is_current) - make_interval(hours => :hours)")
            p["hours"] = f.hours
        if f.bbox:
            cond.append("ST_Intersects(geometry, ST_MakeEnvelope(:bw,:bs,:be,:bn,4326))")
            p.update(bw=f.bbox[0], bs=f.bbox[1], be=f.bbox[2], bn=f.bbox[3])
        return " AND ".join(cond), p

    _COLS = ("source_id, thermal_class, thermal_label, class_confidence, class_margin, class_scores, reasons, "
             "alert_level, alert_text, state, site_ref, site_name, site_kind, site_distance_m, site_within, "
             "n_detections, n_days, window_days, first_seen, last_seen, frp_mean, frp_max, frp_total, "
             "night_fraction, extent_m, lc_dominant, lc_crop, lc_vegetation, lc_built, lc_bare, instruments, "
             "baseline_scope, baseline_key, baseline_active_fraction, baseline_covered_days, baseline_frp_median, "
             "baseline_frp_p95, baseline_night_share, baseline_monthly, baseline_window_end, frp_today, "
             "frp_today_date, frp_z, model_version, model_class, model_probability, model_probabilities, model_reasons, "
             "imagery_available, imagery_scene_id, imagery_scene_time, imagery_cloud_percent, imagery_before_time, "
             "imagery_dnbr_p95, imagery_burned_fraction, imagery_swir_hot_pixels, imagery_landsat_excess_k, imagery_checked_at")

    def _feature(self, row) -> dict:
        d = dict(row)
        lon, lat = d.pop("lon"), d.pop("lat")
        for k in ("first_seen", "last_seen", "baseline_window_end", "frp_today_date", "imagery_scene_time", "imagery_before_time",
                  "imagery_checked_at"):
            d[k] = d[k].isoformat() if d.get(k) else None
        d["baseline_known"] = d.get("baseline_frp_median") is not None
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": d}

    def list_sources(self, f: ThermalFilters, limit: int, offset: int):
        from sqlalchemy import text
        from backend.app.db import get_engine
        where, p = self._where(f)
        order = ("ORDER BY CASE alert_level WHEN 'critical' THEN 3 WHEN 'high' THEN 2 WHEN 'watch' THEN 1 ELSE 0 END DESC, "
                 "frp_total DESC, source_id")
        with get_engine().connect() as c:
            total = int(c.execute(text(f"SELECT COUNT(*) FROM thermal_sources WHERE {where}"), p).scalar_one())
            rows = c.execute(text(f"SELECT {self._COLS}, ST_X(geometry) lon, ST_Y(geometry) lat FROM thermal_sources "
                                  f"WHERE {where} {order} LIMIT :limit OFFSET :offset"),
                             {**p, "limit": limit, "offset": offset}).mappings().all()
        return [self._feature(r) for r in rows], total

    def get_source(self, source_id: str):
        from sqlalchemy import text
        from backend.app.db import get_engine
        with get_engine().connect() as c:
            row = c.execute(text(f"SELECT {self._COLS}, ST_X(geometry) lon, ST_Y(geometry) lat FROM thermal_sources "
                                 "WHERE source_id = :id AND is_current"), {"id": source_id}).mappings().first()
            if row is None:
                return None
            dets = c.execute(text("SELECT detection_id, acq_datetime, instrument, frp, confidence, daynight, "
                                  "ST_Y(geometry) lat, ST_X(geometry) lon FROM thermal_detections "
                                  "WHERE source_id = :id ORDER BY acq_datetime"), {"id": source_id}).mappings().all()
        feat = self._feature(row)
        feat["properties"]["detections"] = [
            {"detection_id": d["detection_id"], "time": d["acq_datetime"].isoformat(), "instrument": d["instrument"],
             "frp": d["frp"], "confidence": d["confidence"], "daynight": d["daynight"], "lat": d["lat"], "lon": d["lon"]}
            for d in dets]
        return feat

    def stats(self, f: ThermalFilters):
        from sqlalchemy import text
        from backend.app.db import get_engine
        where, p = self._where(f)
        with get_engine().connect() as c:
            agg = c.execute(text(f"SELECT COUNT(*) n, COALESCE(SUM(frp_total),0) frp, COALESCE(SUM(n_detections),0) det "
                                 f"FROM thermal_sources WHERE {where}"), p).mappings().one()
            by_class = {r[0]: r[1] for r in c.execute(text(f"SELECT thermal_class, COUNT(*) FROM thermal_sources WHERE {where} GROUP BY 1"), p)}
            by_state = {r[0]: r[1] for r in c.execute(text(
                f"SELECT COALESCE(state,'Unassigned'), COUNT(*) FROM thermal_sources WHERE {where} GROUP BY 1 ORDER BY 2 DESC LIMIT 30"), p)}
            al = {r[0]: r[1] for r in c.execute(text(f"SELECT alert_level, COUNT(*) FROM thermal_sources WHERE {where} AND alert_level IS NOT NULL GROUP BY 1"), p)}
        return {"total_sources": int(agg["n"]), "by_class": by_class, "by_state": by_state,
                "alerts": {"watch": al.get("watch", 0), "high": al.get("high", 0), "critical": al.get("critical", 0)},
                "total_frp_mw": round(float(agg["frp"]), 1), "detections": int(agg["det"])}

    def run_info(self):
        from sqlalchemy import text
        from backend.app.db import get_engine
        with get_engine().connect() as c:
            r = c.execute(text("SELECT id, finished_at, detections, sources, latest_detection, summary FROM thermal_runs "
                               "WHERE status='success' ORDER BY id DESC LIMIT 1")).mappings().first()
        return {"backend": self.name, "summary": (r or {}).get("summary") or {},
                "status": {"last_success_at": r["finished_at"].isoformat() if r else None,
                           "latest_detection_at": r["latest_detection"].isoformat() if r and r["latest_detection"] else None},
                "attribution": ["NASA FIRMS (LANCE)", "OpenStreetMap contributors (ODbL)", "ESA WorldCover 2021 v200 (CC BY 4.0)"]}


def get_backend():
    mode = os.getenv("THERMAL_BACKEND", "auto")
    file_backend = _file_singleton()
    if mode == "file":
        return file_backend
    pg = PostgisBackend()
    if mode == "postgis" or (mode == "auto" and os.getenv("DATA_MODE", "postgis") == "postgis" and pg.available()):
        return pg
    return file_backend


_FILE: FileBackend | None = None


def _file_singleton() -> FileBackend:
    global _FILE
    if _FILE is None:
        _FILE = FileBackend()
    return _FILE


CSV_COLUMNS = ["source_id", "thermal_class", "thermal_label", "class_confidence", "alert_level", "state", "latitude",
               "longitude", "site_name", "site_kind", "site_distance_m", "n_detections", "n_days", "first_seen",
               "last_seen", "frp_max", "frp_mean", "frp_total", "night_fraction", "lc_dominant", "reasons"]


def export_rows(features: list[dict], fmt: str) -> Iterator[str]:
    if fmt == "geojson":
        yield json.dumps({"type": "FeatureCollection", "features": features})
        return
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    yield buf.getvalue()
    for feat in features:
        p, (lon, lat) = feat["properties"], feat["geometry"]["coordinates"]
        row = {**p, "latitude": lat, "longitude": lon, "reasons": " | ".join(p.get("reasons") or [])}
        buf.seek(0); buf.truncate(0)
        w.writerow([_csv_safe(row.get(c)) if row.get(c) is not None else "" for c in CSV_COLUMNS])
        yield buf.getvalue()
