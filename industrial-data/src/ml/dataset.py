"""Feature tables for the labelled units (labels-v1) and for the verified incidents."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.labels import rules as R
from src.thermal import baseline as bl
from src.thermal import classifier
from src.thermal.sites import KINDS, SiteIndex

from . import features as F

logger = logging.getLogger(__name__)


def _context(units: pd.DataFrame, facilities: pd.DataFrame, lc_cache, month: pd.Series) -> pd.DataFrame:
    u = F.facility_features(units, facilities)
    u = F.landcover_features(u, lc_cache)
    return F.season_region(u, month.reset_index(drop=True))


def _baseline(units: pd.DataFrame, det: pd.DataFrame, unit_col: str, index: bl.BaselineIndex, sites: SiteIndex) -> pd.DataFrame:
    """baseline_active_fraction + frp_z through the same code the live pipeline uses (baseline.attach)."""
    src = units[[unit_col, "lat", "lon", "facility_id", "facility_within"]].rename(columns={unit_col: "cluster"}).copy()
    src["site_ref"] = src.facility_id
    src["site_within"] = src.facility_within > 0
    d = det.rename(columns={unit_col: "cluster"})
    out = bl.attach(src.reset_index(drop=True), d, index, sites)
    res = units.reset_index(drop=True).copy()
    res["baseline_active_fraction"] = out["baseline_active_fraction"].to_numpy(dtype=float)
    res["frp_z"] = pd.to_numeric(out["frp_z"], errors="coerce").to_numpy()
    return res


def labelled_features(labels_parquet: Path, archive_dirs: list[Path], facilities_geojson: Path, lc_cache: Path,
                      index: bl.BaselineIndex | None = None) -> pd.DataFrame:
    lab = pd.read_parquet(labels_parquet)
    lab = lab[lab.unit_type == "cell_window"].copy()
    lab["window"] = ((pd.to_datetime(lab.window_start) - pd.Timestamp(R.FIRST_WINDOW)).dt.days // R.WINDOW_DAYS).astype(int)

    det = bl.physical(bl.load_archives(archive_dirs).pipe(lambda d: d[d.confidence != "low"]))
    det["cell_key"] = bl.cell_keys(det.latitude, det.longitude)
    day = det.acq_datetime.dt.tz_convert("UTC").dt.tz_localize(None).dt.normalize()
    det["window"] = ((day - pd.Timestamp(R.FIRST_WINDOW)).dt.days // R.WINDOW_DAYS).astype(int)
    keyed = det.merge(lab[["label_id", "cell_key", "window"]], on=["cell_key", "window"], how="inner")
    logger.info("labelled units %d, detections %d", len(lab), len(keyed))

    units = F.aggregate_units(keyed, "label_id")
    fac = F.load_facilities(facilities_geojson)
    month = (pd.to_datetime(lab.set_index("label_id").loc[units.label_id, "window_start"]) + pd.Timedelta(days=3)).dt.month
    units = _context(units, fac, lc_cache, month.reset_index(drop=True))
    sites = SiteIndex.from_geojson(facilities_geojson)
    if index is None:
        index = bl.BaselineIndex.from_detections(bl.physical(bl.load_archives(archive_dirs)), sites)
    units = _baseline(units, keyed, "label_id", index, sites)
    meta = lab.set_index("label_id")[["label", "split", "state", "window_start", "window_end", "region_group", "cell_key"]]
    return units.join(meta, on="label_id")


# --------------------------------------------------------------------------- incidents
def incident_features(incidents_csv: Path, units_dir: Path, facilities_geojson: Path, lc_cache: Path) -> pd.DataFrame:
    """One unit per verified incident: every detection inside the circle during [event-1d, event+2d]; the baseline of
    that circle is its own 365 days ending 14 days before the event. Incidents with no detection get a row with
    ``detected = False`` (the satellite cannot classify what it did not see)."""
    inc = pd.read_csv(incidents_csv)
    inc = inc[inc.verified.astype(str).str.lower() == "true"]
    fac = F.load_facilities(facilities_geojson)
    rows, dets = [], []
    for r in inc.itertuples():
        p = units_dir / f"{r.incident_id}.parquet"
        if not p.exists():
            continue
        det = pd.read_parquet(p)
        det["acq_datetime"] = pd.to_datetime(det.acq_datetime, utc=True)
        det = det[det.confidence.fillna("nominal") != "low"] if "confidence" in det else det
        d0 = pd.Timestamp(r.event_date).date()
        lat0, lon0, rad = float(r.lat), float(r.lon), float(r.radius_km) + 0.5
        if len(det):
            dlat = (det.latitude - lat0) * 110.54
            dlon = (det.longitude - lon0) * 111.32 * np.cos(np.radians(lat0))
            det = det[np.hypot(dlat, dlon) <= rad]
        det = bl.physical(det.assign(confidence=det.get("confidence", "nominal"))) if len(det) else det
        day = det.acq_datetime.dt.tz_convert("UTC").dt.tz_localize(None).dt.date if len(det) else pd.Series([], dtype=object)
        ev = det[(day >= d0 - timedelta(days=R.INCIDENT_DAYS_BEFORE)) & (day <= d0 + timedelta(days=R.INCIDENT_DAYS_AFTER))] if len(det) else det
        base = det[(day >= d0 - timedelta(days=14 + 364)) & (day <= d0 - timedelta(days=14))] if len(det) else det
        rec = {"incident_id": r.incident_id, "event_date": d0, "state": r.state, "location_basis": r.location_basis,
               "radius_km": rad, "lat": lat0, "lon": lon0, "detected": bool(len(ev)), "n_baseline_days": 365}
        if len(ev):
            ev = ev.assign(unit="incident")
            agg = F.aggregate_units(ev, "unit").iloc[0].to_dict()
            rec.update(agg)
            bday = base.acq_datetime.dt.tz_convert("UTC").dt.tz_localize(None).dt.date
            daily = base.groupby(bday).frp.sum()
            rec["baseline_active_fraction"] = len(daily) / 365.0
            mean = float(daily.sum() / 365.0)
            std = float(np.sqrt(max((daily ** 2).sum() / 365.0 - mean ** 2, 0.0)))
            ev_day = ev.groupby(ev.acq_datetime.dt.tz_convert("UTC").dt.date).frp.sum()
            rec["frp_z"] = float((ev_day.max() - mean) / max(std, bl.MIN_STD_MW))
            rec["last_day"] = pd.Timestamp(rec["last_day"])
        rows.append(rec)
        dets.append(ev.assign(incident_id=r.incident_id) if len(ev) else None)
    out = pd.DataFrame(rows)
    ok = out[out.detected].copy()
    if len(ok):
        ok = F.facility_features(ok, fac)
        ok = F.landcover_features(ok, lc_cache)
        ok = F.season_region(ok, pd.Series([pd.Timestamp(d).month for d in ok.event_date]))
        out = out.set_index("incident_id").combine_first(ok.set_index("incident_id")).reset_index()
    return out


# --------------------------------------------------------------------------- rule-classifier twin
def rules_predict(feat: pd.DataFrame, window_days: int = 7) -> pd.Series:
    """Run the existing rule classifier on a feature table (same inputs the live pipeline gives it).

    Returns the rule class mapped to the label vocabulary; rule classes without a label counterpart
    (``industrial_fire_candidate``, ``unmapped_persistent``, ``other``) are returned as is.
    """
    out = []
    for r in feat.to_dict("records"):
        kind = r.get("facility_kind") if r.get("facility_within", 0) > 0 else None
        row = {
            "n_days": r["n_days"], "window_days": window_days, "extent_m": r["extent_m"], "night_fraction": r["night_fraction"],
            "frp_max": r["frp_max"], "frp_mean": r["frp_mean"], "frp_recent_max": r["frp_max"], "hours_since_last": 12.0,
            "site_within": bool(r.get("facility_within", 0) > 0), "site_kind": kind, "site_name": r.get("facility_name"),
            "site_ref": r.get("facility_id"), "site_continuous": KINDS[kind].continuous if kind in KINDS else 0.0,
            "lc_crop": r.get("lc_crop"), "lc_vegetation": r.get("lc_vegetation"), "lc_bare": r.get("lc_bare"),
            "lc_built": r.get("lc_built"), "lc_dominant": "x" if pd.notna(r.get("lc_crop")) else None,
        }
        out.append(classifier.classify_row(row, int(r["month"])).cls)
    return pd.Series(out, index=feat.index)
