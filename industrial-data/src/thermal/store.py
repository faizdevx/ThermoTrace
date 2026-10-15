"""Persistence for thermal results: atomic file snapshot (always) and PostGIS (when configured)."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .pipeline import Analysis, to_geojson

logger = logging.getLogger(__name__)
HISTORY_FILE = "detections_history.csv"
SNAPSHOT_FILE = "thermal_sources.geojson"
DETECTIONS_FILE = "thermal_detections.csv"
STATUS_FILE = "status.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)          # readers never see a half-written file
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_history(directory: Path) -> pd.DataFrame:
    path = directory / HISTORY_FILE
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["acq_datetime"] = pd.to_datetime(df["acq_datetime"], utc=True)
    return df


def merge_history(history: pd.DataFrame, fresh: pd.DataFrame, keep_days: int) -> pd.DataFrame:
    merged = pd.concat([history, fresh], ignore_index=True) if len(history) else fresh.copy()
    merged = merged.drop_duplicates("detection_id", keep="last")
    cutoff = merged["acq_datetime"].max() - pd.Timedelta(days=keep_days)
    return merged[merged["acq_datetime"] >= cutoff].reset_index(drop=True)


def save_history(directory: Path, det: pd.DataFrame) -> None:
    cols = ["detection_id", "instrument", "acq_datetime", "latitude", "longitude", "frp", "bright_ti4",
            "bright_ti5", "confidence", "daynight", "satellite", "version"]
    directory.mkdir(parents=True, exist_ok=True)
    det[[c for c in cols if c in det.columns]].to_csv(directory / HISTORY_FILE, index=False)


def write_snapshot(directory: Path, a: Analysis) -> None:
    """Publish a new consistent snapshot (sources first so detections never reference a missing source)."""
    det_cols = ["detection_id", "instrument", "acq_datetime", "latitude", "longitude", "frp", "bright_ti4",
                "confidence", "daynight", "source_id", "thermal_class"]
    det_csv = a.detections[[c for c in det_cols if c in a.detections.columns]].to_csv(index=False)
    _atomic_write(directory / DETECTIONS_FILE, det_csv)
    _atomic_write(directory / SNAPSHOT_FILE, json.dumps(to_geojson(a), default=_json_default))


def _json_default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    return str(obj)


def snapshot_exists(directory: Path) -> bool:
    return (directory / SNAPSHOT_FILE).exists()


def write_status(directory: Path, **fields) -> dict:
    path = directory / STATUS_FILE
    current = json.loads(path.read_text()) if path.exists() else {}
    current.update({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in fields.items()})
    _atomic_write(path, json.dumps(current, default=_json_default, indent=2))
    return current


def read_status(directory: Path) -> dict:
    path = directory / STATUS_FILE
    return json.loads(path.read_text()) if path.exists() else {}


# ── PostGIS ─────────────────────────────────────────────────────────────────

def persist_postgis(dsn: str, a: Analysis) -> int:
    """Replace the *current* thermal picture in PostGIS inside one transaction; returns run id.

    Detections are upserted (history kept); sources are upserted and anything not in this run is
    marked ``is_current = FALSE``. A NOTIFY is queued in the same transaction so API clients are
    told only after the data is visible.
    """
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(dsn, application_name="thermotrace-thermal")
    try:
        with conn, conn.cursor() as cur:
            cur.execute("INSERT INTO thermal_runs (status) VALUES ('running') RETURNING id")
            run_id = cur.fetchone()[0]
            det = a.detections
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO thermal_detections (detection_id, instrument, acq_datetime, geometry, frp,
                       bright_ti4, bright_ti5, confidence, daynight, source_id) VALUES %s
                   ON CONFLICT (detection_id) DO UPDATE SET source_id = EXCLUDED.source_id""",
                [(r.detection_id, r.instrument, r.acq_datetime.to_pydatetime(),
                  f"SRID=4326;POINT({r.longitude} {r.latitude})", float(r.frp), _f(r.bright_ti4),
                  _f(getattr(r, "bright_ti5", None)), r.confidence, r.daynight, r.source_id)
                 for r in det.itertuples()],
                template="(%s,%s,%s,ST_GeomFromEWKT(%s),%s,%s,%s,%s,%s,%s)", page_size=2000)
            cur.execute("UPDATE thermal_sources SET is_current = FALSE WHERE is_current")
            rows = []
            for r in a.sources.to_dict("records"):
                rows.append((
                    r["source_id"], f"SRID=4326;POINT({r['lon']} {r['lat']})", r["thermal_class"], r["thermal_label"],
                    r["class_confidence"], r["class_margin"], json.dumps(r["class_scores"]),
                    # _s: pandas NaN would otherwise be stored as the text 'NaN' (and read back as an alert)
                    json.dumps(r["reasons"]), _s(r["alert_level"]), _s(r["alert_text"]), _s(r.get("state")),
                    _s(r.get("site_ref")), _s(r.get("site_name")), _s(r.get("site_kind")), _f(r.get("site_distance_m")),
                    bool(r.get("site_within")), r["n_detections"], r["n_days"], r["window_days"],
                    r["first_seen"].to_pydatetime(), r["last_seen"].to_pydatetime(), r["frp_mean"], r["frp_max"],
                    r["frp_total"], r["night_fraction"], r["extent_m"], _s(r.get("lc_dominant")),
                    _f(r.get("lc_crop")), _f(r.get("lc_vegetation")), _f(r.get("lc_built")), _f(r.get("lc_bare")),
                    r["instruments"], run_id,
                    _s(r.get("baseline_scope")), _s(r.get("baseline_key")), _f(r.get("baseline_active_fraction")),
                    _i(r.get("baseline_covered_days")), _f(r.get("baseline_frp_median")),
                    _f(r.get("baseline_frp_p95")), _f(r.get("baseline_night_share")),
                    json.dumps(r["baseline_monthly"]) if isinstance(r.get("baseline_monthly"), list) else None,
                    r.get("baseline_window_end"), _f(r.get("frp_today")), r.get("frp_today_date"), _f(r.get("frp_z"))))
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO thermal_sources (source_id, geometry, thermal_class, thermal_label, class_confidence,
                       class_margin, class_scores, reasons, alert_level, alert_text, state, site_ref, site_name,
                       site_kind, site_distance_m, site_within, n_detections, n_days, window_days, first_seen,
                       last_seen, frp_mean, frp_max, frp_total, night_fraction, extent_m, lc_dominant, lc_crop,
                       lc_vegetation, lc_built, lc_bare, instruments, run_id, baseline_scope, baseline_key,
                       baseline_active_fraction, baseline_covered_days, baseline_frp_median, baseline_frp_p95,
                       baseline_night_share, baseline_monthly, baseline_window_end, frp_today, frp_today_date,
                       frp_z) VALUES %s
                   ON CONFLICT (source_id) DO UPDATE SET geometry = EXCLUDED.geometry,
                       thermal_class = EXCLUDED.thermal_class, thermal_label = EXCLUDED.thermal_label,
                       class_confidence = EXCLUDED.class_confidence, class_margin = EXCLUDED.class_margin,
                       class_scores = EXCLUDED.class_scores, reasons = EXCLUDED.reasons,
                       alert_level = EXCLUDED.alert_level, alert_text = EXCLUDED.alert_text, state = EXCLUDED.state,
                       site_ref = EXCLUDED.site_ref, site_name = EXCLUDED.site_name, site_kind = EXCLUDED.site_kind,
                       site_distance_m = EXCLUDED.site_distance_m, site_within = EXCLUDED.site_within,
                       n_detections = EXCLUDED.n_detections, n_days = EXCLUDED.n_days,
                       window_days = EXCLUDED.window_days, first_seen = EXCLUDED.first_seen,
                       last_seen = EXCLUDED.last_seen, frp_mean = EXCLUDED.frp_mean, frp_max = EXCLUDED.frp_max,
                       frp_total = EXCLUDED.frp_total, night_fraction = EXCLUDED.night_fraction,
                       extent_m = EXCLUDED.extent_m, lc_dominant = EXCLUDED.lc_dominant, lc_crop = EXCLUDED.lc_crop,
                       lc_vegetation = EXCLUDED.lc_vegetation, lc_built = EXCLUDED.lc_built, lc_bare = EXCLUDED.lc_bare,
                       instruments = EXCLUDED.instruments, run_id = EXCLUDED.run_id, is_current = TRUE,
                       baseline_scope = EXCLUDED.baseline_scope, baseline_key = EXCLUDED.baseline_key,
                       baseline_active_fraction = EXCLUDED.baseline_active_fraction,
                       baseline_covered_days = EXCLUDED.baseline_covered_days,
                       baseline_frp_median = EXCLUDED.baseline_frp_median, baseline_frp_p95 = EXCLUDED.baseline_frp_p95,
                       baseline_night_share = EXCLUDED.baseline_night_share,
                       baseline_monthly = EXCLUDED.baseline_monthly, baseline_window_end = EXCLUDED.baseline_window_end,
                       frp_today = EXCLUDED.frp_today, frp_today_date = EXCLUDED.frp_today_date,
                       frp_z = EXCLUDED.frp_z, updated_at = NOW()""",
                rows, template="(%s,ST_GeomFromEWKT(%s),%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                               "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                               "%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)", page_size=1000)
            cur.execute(
                """UPDATE thermal_runs SET status='success', finished_at=NOW(), detections=%s, sources=%s,
                       latest_detection=%s, summary=%s::jsonb WHERE id=%s""",
                (len(det), len(a.sources), det.acq_datetime.max().to_pydatetime(),
                 json.dumps(a.summary, default=_json_default), run_id))
            cur.execute("UPDATE data_revision SET revision = revision + 1, changed_at = NOW() WHERE id = 1 RETURNING revision")
            revision = cur.fetchone()[0]
            cur.execute("SELECT pg_notify('thermotrace_changes', %s)", (json.dumps({
                "revision": revision, "source": "nasa_firms", "kind": "thermal", "sources": len(a.sources),
                "alerts": int(a.sources.alert_level.notna().sum()),
                "committed_at": datetime.now(timezone.utc).isoformat()}),))
        return run_id
    finally:
        conn.close()


def _i(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else int(v)


def _f(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)


def _s(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else str(v)
