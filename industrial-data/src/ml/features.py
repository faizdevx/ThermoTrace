"""One feature definition for every place a thermal "source" is scored: labelled cell-windows (training and
evaluation), verified incidents (evaluation) and live sources (inference).

A unit is a group of VIIRS detections (a 1 km cell in a 7-day window, an incident circle, or a live cluster).
Feature groups are named so the evaluation can ablate them: ``BLIND`` is the thermal behaviour alone; the other groups
are exactly the inputs the weak-label rules read (see ``src.labels.rules``).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.thermal.sites import KINDS

KIND_NAMES = sorted(KINDS)

# ---- thermal behaviour (no place knowledge) --------------------------------------------------------------------
THERMAL = ["n_detections", "n_days", "frp_max", "frp_mean", "frp_total", "frp_p90", "night_fraction", "ti4_mean", "ti4_max",
           "ti_diff_mean", "scan_mean", "track_mean", "extent_m", "high_conf_fraction", "n_satellites"]
# ---- history of the place (365-day baseline) -------------------------------------------------------------------
BASELINE = ["baseline_active_fraction", "frp_z"]
# ---- what is at the place ----------------------------------------------------------------------------------------
FACILITY = ["facility_dist_m", "facility_within", "facility_source_count", "facility_radius_m", "authoritative_dist_m"] + \
           [f"kind_{k}" for k in KIND_NAMES]
LANDCOVER = ["lc_crop", "lc_woody", "lc_vegetation", "lc_built", "lc_bare", "lc_water"]
SEASON_REGION = ["month_sin", "month_cos", "lat_abs"]

GROUPS = {"thermal": THERMAL, "baseline": BASELINE, "facility": FACILITY, "landcover": LANDCOVER, "season_region": SEASON_REGION}
ALL_FEATURES = THERMAL + BASELINE + FACILITY + LANDCOVER + SEASON_REGION
BLIND = THERMAL                                  # no facility, land cover, season, region or baseline: the honest model
THERMAL_BASELINE = THERMAL + BASELINE


def _extent_m(lat: np.ndarray, lon: np.ndarray) -> float:
    if len(lat) < 2:
        return 0.0
    dy = (lat.max() - lat.min()) * 110_540.0
    dx = (lon.max() - lon.min()) * 111_320.0 * math.cos(math.radians(float(np.mean(lat))))
    return float(math.hypot(dx, dy))


def aggregate_units(det: pd.DataFrame, unit_col: str) -> pd.DataFrame:
    """Per-unit thermal features from detections (columns latitude, longitude, acq_datetime, frp, daynight, instrument
    and optionally bright_ti4, bright_ti5, scan, track, confidence)."""
    d = det.copy()
    for c in ("bright_ti4", "bright_ti5", "scan", "track"):
        if c not in d:
            d[c] = np.nan
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["frp"] = pd.to_numeric(d["frp"], errors="coerce").fillna(0.0)
    d["night"] = (d["daynight"] == "N").astype(float)
    d["day"] = d["acq_datetime"].dt.tz_convert("UTC").dt.tz_localize(None).dt.normalize()
    d["ti_diff"] = d["bright_ti4"] - d["bright_ti5"]
    d["high"] = (d.get("confidence", "nominal") == "high").astype(float)
    d["sat"] = d["instrument"].astype(str).str.split("_").str[1]
    rows = []
    for uid, g in d.groupby(unit_col, sort=False):
        rows.append({
            unit_col: uid, "lat": float(np.average(g.latitude, weights=g.frp.clip(lower=0.1))),
            "lon": float(np.average(g.longitude, weights=g.frp.clip(lower=0.1))),
            "n_detections": len(g), "n_days": int(g.day.nunique()), "frp_max": float(g.frp.max()), "frp_mean": float(g.frp.mean()),
            "frp_total": float(g.frp.sum()), "frp_p90": float(np.percentile(g.frp, 90)), "night_fraction": float(g.night.mean()),
            "ti4_mean": float(g.bright_ti4.mean()), "ti4_max": float(g.bright_ti4.max()), "ti_diff_mean": float(g.ti_diff.mean()),
            "scan_mean": float(g.scan.mean()), "track_mean": float(g.track.mean()),
            "extent_m": _extent_m(g.latitude.to_numpy(), g.longitude.to_numpy()), "high_conf_fraction": float(g.high.mean()),
            "n_satellites": int(g.sat.nunique()), "first_day": g.day.min(), "last_day": g.day.max(),
        })
    return pd.DataFrame(rows)


def season_region(units: pd.DataFrame, month: pd.Series | None = None) -> pd.DataFrame:
    m = month if month is not None else pd.to_datetime(units["last_day"]).dt.month
    out = units.copy()
    out["month"] = m.to_numpy()
    out["month_sin"] = np.sin(2 * np.pi * (m.to_numpy() - 1) / 12)
    out["month_cos"] = np.cos(2 * np.pi * (m.to_numpy() - 1) / 12)
    out["lat_abs"] = out["lat"].abs()
    return out


def facility_features(units: pd.DataFrame, facilities: pd.DataFrame) -> pd.DataFrame:
    """``facilities``: lat, lon, kind, radius_m, source_count, authoritative (bool). Nearest facility of any source
    (+ nearest authoritative one) for every unit; kind is one-hot."""
    from sklearn.neighbors import BallTree
    earth = 6_371_000.0
    pts = np.radians(units[["lat", "lon"]].to_numpy(dtype=float))
    tree = BallTree(np.radians(facilities[["lat", "lon"]].to_numpy(dtype=float)), metric="haversine")
    dist, idx = tree.query(pts, k=1)
    near = facilities.iloc[idx[:, 0]].reset_index(drop=True)
    out = units.copy().reset_index(drop=True)
    out["facility_dist_m"] = dist[:, 0] * earth
    out["facility_radius_m"] = near.radius_m.to_numpy()
    out["facility_within"] = (out.facility_dist_m <= out.facility_radius_m + 500.0).astype(float)
    out["facility_source_count"] = near.source_count.to_numpy()
    out["facility_id"] = near.facility_id.to_numpy() if "facility_id" in near else None
    out["facility_name"] = near.name.to_numpy() if "name" in near else None
    out["facility_kind"] = near.kind.to_numpy()
    auth = facilities[facilities.authoritative].reset_index(drop=True)
    if len(auth):
        d2, _ = BallTree(np.radians(auth[["lat", "lon"]].to_numpy(dtype=float)), metric="haversine").query(pts, k=1)
        out["authoritative_dist_m"] = d2[:, 0] * earth
    else:
        out["authoritative_dist_m"] = 1e6
    for k in KIND_NAMES:
        # a kind only counts when the unit is actually inside the facility's radius (otherwise it is "no facility")
        out[f"kind_{k}"] = ((out.facility_kind == k) & (out.facility_within > 0)).astype(float)
    return out


def landcover_features(units: pd.DataFrame, cache_path=None) -> pd.DataFrame:
    from src.thermal import landcover
    lc = landcover.sample_many(units[["lat", "lon"]].reset_index(drop=True), cache_path)
    out = units.reset_index(drop=True).copy()
    for c in LANDCOVER:
        out[c] = lc[c].to_numpy() if c in lc else np.nan
    return out


def load_facilities(geojson_path) -> pd.DataFrame:
    import json
    data = json.loads(open(geojson_path, encoding="utf-8").read())
    rows = []
    for f in data["features"]:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        srcs = str(p.get("sources", "")).split(",")
        rows.append({"facility_id": p["facility_id"], "name": p.get("name"), "kind": p.get("kind"), "lat": lat, "lon": lon,
                     "radius_m": p.get("radius_m") or KINDS[p["kind"]].radius_m, "source_count": p.get("source_count") or 1,
                     "authoritative": any(s in ("wri_gppd", "gem", "ggfr", "vnf") for s in srcs)})
    return pd.DataFrame(rows)
