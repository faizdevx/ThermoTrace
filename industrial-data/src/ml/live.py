"""Score live thermal sources with the trained model (second opinion next to the rule classifier).

The rule classifier stays the primary, always-available class and the fallback: if the model file is missing or a
feature table cannot be built, sources simply carry no model fields.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd

from . import features as F
from .model import ThermalModel

logger = logging.getLogger(__name__)
DEFAULT_MODEL = Path(__file__).resolve().parents[2] / "models" / "thermal_gbm.joblib"
MODEL_COLUMNS = ["model_version", "model_class", "model_probability", "model_probabilities", "model_reasons"]


def model_path() -> Path:
    return Path(os.getenv("THERMAL_MODEL_PATH", str(DEFAULT_MODEL)))


def load_model() -> ThermalModel | None:
    p = model_path()
    if not p.exists():
        logger.warning("no trained model at %s: sources carry the rule classifier only", p)
        return None
    try:
        model = ThermalModel.load(p)
        import sklearn
        trained_with = model.meta.get("sklearn_version")
        if trained_with and trained_with != sklearn.__version__:
            logger.warning("model was trained with scikit-learn %s, running %s: predictions may differ", trained_with, sklearn.__version__)
        return model
    except Exception as exc:  # noqa: BLE001 - never break a cycle over the second opinion
        logger.warning("could not load the model (%s): rules only", exc)
        return None


def facilities_frame(sites_geojson: str | Path | None = None, dsn: str | None = None) -> pd.DataFrame | None:
    """Facility table with source_count / authoritative (the SiteIndex does not carry them)."""
    p = Path(sites_geojson) if sites_geojson else None
    if p and p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data["features"] and "source_count" in data["features"][0]["properties"]:
                return F.load_facilities(p)
        except Exception as exc:  # noqa: BLE001
            logger.warning("facility file unreadable (%s)", exc)
    if dsn:
        try:
            import psycopg2
            with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute("SELECT facility_id, name, kind, ST_Y(geometry), ST_X(geometry), radius_m, source_count, sources FROM facilities")
                rows = cur.fetchall()
            return pd.DataFrame([{"facility_id": a, "name": b, "kind": c, "lat": d, "lon": e, "radius_m": f, "source_count": g,
                                  "authoritative": any(s in ("wri_gppd", "gem", "ggfr", "vnf") for s in str(h).split(","))}
                                 for a, b, c, d, e, f, g, h in rows])
        except Exception as exc:  # noqa: BLE001
            logger.warning("facility table unreadable (%s)", exc)
    return None


def score_sources(src: pd.DataFrame, det: pd.DataFrame, model: ThermalModel | None, facilities: pd.DataFrame | None,
                  explain_top: int = 5, n_permutations: int = 8) -> pd.DataFrame:
    """Add model_* columns to ``src`` (one row per cluster). ``det`` carries the detections with a ``cluster`` column."""
    out = src.copy()
    for c in MODEL_COLUMNS:
        out[c] = None
    if model is None or facilities is None or src.empty or det.empty:
        return out
    try:
        agg = F.aggregate_units(det.assign(unit=det["cluster"]), "unit").rename(columns={"unit": "cluster"})
        month = pd.to_datetime(src["last_seen"]).dt.month
        feat = src.reindex(columns=["cluster", "lat", "lon", "baseline_active_fraction", "frp_z", "lc_crop", "lc_woody",
                                    "lc_vegetation", "lc_built", "lc_bare", "lc_water"]).copy()
        feat["month"] = month.to_numpy()
        merged = agg.drop(columns=["lat", "lon"]).merge(feat, on="cluster", how="inner")
        merged = F.facility_features(merged, facilities)
        merged = F.season_region(merged, pd.Series(merged["month"].to_numpy()))
        for c in ("baseline_active_fraction", "frp_z"):
            merged[c] = pd.to_numeric(merged[c], errors="coerce")
        proba = model.predict_proba(merged)
        cls = np.array(model.classes)[proba.argmax(axis=1)]
        merged["model_class"], merged["model_probability"] = cls, proba.max(axis=1)
        merged["model_probabilities"] = [dict(zip(model.classes, map(float, p))) for p in proba]
        # reasons for every source whose model class is not overwhelming, and for all industrial-context ones
        reasons: list[list[str] | None] = []
        for i, row in merged.iterrows():
            if explain_top and (row.model_probability < 0.97 or row.model_class == "industrial_persistent"):
                reasons.append([r["text"] for r in model.explain(row, top=explain_top, n_permutations=n_permutations)["reasons"]])
            else:
                reasons.append(None)
        merged["model_reasons"] = reasons
        merged["model_version"] = model.version
        keep = merged.set_index("cluster")[MODEL_COLUMNS]
        for c in MODEL_COLUMNS:
            out[c] = out["cluster"].map(keep[c]).astype(object)
        out["model_version"] = out["model_version"].where(out["model_class"].notna(), None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("model scoring skipped: %s", exc)
    return out
