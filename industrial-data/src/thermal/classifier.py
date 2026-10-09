"""Explainable classification of thermal sources.

NASA FIRMS reports *where heat was seen*, not *why*. This module scores each thermal source
(a spatial cluster of detections over the analysis window) against six hypotheses from physically
meaningful evidence, and returns the class, per-class scores and the reasons that drove the decision.

The weights below are expert-set priors, NOT trained on labelled ground truth. Scores are relative
(a softmax of evidence), not calibrated probabilities. See ``model.py`` for the optional weakly
supervised second opinion and README for validation status.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

CLASSES = ["industrial_persistent", "industrial_fire_candidate", "agricultural_burning",
           "vegetation_fire", "unmapped_persistent", "other"]
LABELS = {
    "industrial_persistent": "Persistent industrial thermal source",
    "industrial_fire_candidate": "Possible industrial fire / abnormal event",
    "agricultural_burning": "Agricultural (crop residue) burning",
    "vegetation_fire": "Forest / vegetation fire",
    "unmapped_persistent": "Persistent source, no mapped facility",
    "other": "Other / unclassified",
}
AGRI_MONTHS = {10, 11, 4, 5}      # kharif residue burning (Oct-Nov) and rabi/wheat residue (Apr-May)

# (feature name, weight) per class; features are 0..1 values computed in ``features_for``.
WEIGHTS: dict[str, list[tuple[str, float]]] = {
    "industrial_persistent": [("near_site", 2.2), ("persistent", 1.4), ("very_persistent", 0.6),
                              ("site_continuous", 1.0), ("point_like", 0.5), ("night_present", 0.5),
                              ("far_from_site", -3.0), ("brief", -1.0)],
    "industrial_fire_candidate": [("near_site", 2.2), ("brief", 1.2), ("frp_high", 0.9), ("recent", 0.5),
                                  ("site_episodic", 0.6), ("persistent", -1.4), ("far_from_site", -3.0)],
    "agricultural_burning": [("lc_crop", 2.2), ("agri_season_crop", 1.0), ("crop_brief", 0.6), ("crop_frp_small", 0.5),
                             ("persistent", -0.8), ("near_site", -2.5), ("night_present", -0.8)],
    "vegetation_fire": [("lc_vegetation", 2.2), ("veg_frp_mid", 0.5), ("veg_big_extent", 0.4),
                        ("near_site", -2.5), ("persistent", -0.6), ("night_present", -0.5)],
    "unmapped_persistent": [("unmapped_persistent", 1.8), ("unmapped_very_persistent", 0.6),
                            ("unmapped_point_like", 0.8), ("unmapped_night", 0.4), ("unmapped_bare_built", 0.8)],
    "other": [("bias", 0.6), ("lc_unknown", 0.5)],
}
FEATURE_TEXT = {
    "near_site": "lies on a mapped {kind} ({site})",
    "persistent": "detected on {n_days} of the last {window_days} days",
    "very_persistent": "detected on {n_days} separate days (very persistent)",
    "site_continuous": "this facility type commonly emits a continuous thermal signature",
    "site_episodic": "this facility type is usually not thermally active, so a detection is notable",
    "point_like": "compact hotspot ({extent_m:.0f} m across), typical of a point source",
    "night_present": "{night_pct:.0f}% of detections at night (not solar/crop-field typical)",
    "brief": "short-lived: only {n_days} day(s) of detections",
    "crop_brief": "short-lived: only {n_days} day(s) of detections, typical of field burning",
    "crop_frp_small": "low radiative power (max {frp_max:.0f} MW), typical of small field fires",
    "veg_frp_mid": "moderate/high radiative power (max {frp_max:.0f} MW)",
    "veg_big_extent": "spread over {extent_m:.0f} m (area fire)",
    "frp_high": "high radiative power (max {frp_max:.0f} MW)",
    "frp_small": "low radiative power (max {frp_max:.0f} MW), typical of small field fires",
    "frp_mid": "moderate/high radiative power (max {frp_max:.0f} MW)",
    "recent": "active within the last 24 h",
    "lc_crop": "{crop_pct:.0f}% cropland around the source (ESA WorldCover)",
    "agri_season_crop": "within the residue-burning season",
    "lc_vegetation": "{veg_pct:.0f}% natural vegetation around the source (ESA WorldCover)",
    "big_extent": "spread over {extent_m:.0f} m (area fire)",
    "unmapped_persistent": "persistent for {n_days} days but no mapped industrial facility within range",
    "unmapped_very_persistent": "very persistent without any mapped facility",
    "unmapped_point_like": "compact point-source footprint with no mapped facility",
    "unmapped_night": "night-time activity without a mapped facility",
    "unmapped_bare_built": "bare/built-up surroundings (possible unmapped industry, kiln or landfill)",
    "far_from_site": "no mapped industrial facility within range",
    "lc_unknown": "land cover unavailable",
    "bias": "no strong evidence for any specific cause",
}


@dataclass
class Result:
    cls: str
    label: str
    scores: dict[str, float]
    confidence: str
    margin: float
    reasons: list[str]


def features_for(r: dict, month: int) -> dict[str, float]:
    nd = r["n_days"]
    near = 1.0 if r.get("site_within") else 0.0
    far = 1.0 - near
    cont = float(r.get("site_continuous") or 0.0) * near
    persistent = 1.0 if nd >= 3 else 0.0
    vp = 1.0 if nd >= 5 else 0.0
    point_like = 1.0 if r["extent_m"] <= 1500 else 0.0
    night = 1.0 if r["night_fraction"] >= 0.25 else 0.0
    crop = _num(r.get("lc_crop"))
    veg = _num(r.get("lc_vegetation"))
    bare_built = min(_num(r.get("lc_bare")) + _num(r.get("lc_built")), 1.0)
    return {
        "near_site": near, "far_from_site": far,
        "persistent": persistent, "very_persistent": vp, "brief": 1.0 if nd <= 2 else 0.0,
        "site_continuous": cont, "site_episodic": near * (1.0 - float(r.get("site_continuous") or 0.0)),
        "point_like": point_like, "night_present": night, "big_extent": 1.0 if r["extent_m"] > 3000 else 0.0,
        "frp_high": 1.0 if r["frp_max"] >= 40 else 0.0,
        "frp_mid": 1.0 if r["frp_max"] >= 15 else 0.0,
        "frp_small": 1.0 if r["frp_max"] < 10 else 0.0,
        "recent": 1.0 if r["hours_since_last"] <= 24 else 0.0,
        "lc_crop": crop, "lc_vegetation": veg,
        "crop_brief": crop * (1.0 if nd <= 2 else 0.0), "crop_frp_small": crop * (1.0 if r["frp_max"] < 10 else 0.0),
        "veg_frp_mid": veg * (1.0 if r["frp_max"] >= 15 else 0.0), "veg_big_extent": veg * (1.0 if r["extent_m"] > 3000 else 0.0),
        "agri_season_crop": (1.0 if month in AGRI_MONTHS else 0.0) * crop,
        "lc_unknown": 1.0 if r.get("lc_dominant") in (None, np.nan) or pd.isna(r.get("lc_dominant")) else 0.0,
        "unmapped_persistent": far * persistent, "unmapped_very_persistent": far * vp,
        "unmapped_point_like": far * persistent * point_like, "unmapped_night": far * persistent * night,
        "unmapped_bare_built": far * persistent * bare_built,
        "bias": 1.0,
    }


def _num(v) -> float:
    return 0.0 if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def classify_row(r: dict, month: int) -> Result:
    f = features_for(r, month)
    logits = {c: sum(w * f[name] for name, w in terms) for c, terms in WEIGHTS.items()}
    mx = max(logits.values())
    exp = {c: math.exp(v - mx) for c, v in logits.items()}
    total = sum(exp.values())
    scores = {c: exp[c] / total for c in CLASSES}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top, second = ranked[0], ranked[1]
    margin = top[1] - second[1]
    confidence = "high" if margin >= 0.45 else "medium" if margin >= 0.2 else "low"
    ctx = {
        "kind": (r.get("site_kind") or "facility").replace("_", " "), "site": r.get("site_name") or r.get("site_ref"),
        "n_days": r["n_days"], "window_days": r["window_days"], "extent_m": r["extent_m"],
        "night_pct": 100 * r["night_fraction"], "frp_max": r["frp_max"],
        "crop_pct": 100 * _num(r.get("lc_crop")), "veg_pct": 100 * _num(r.get("lc_vegetation")),
    }
    contribs = sorted(((name, w * f[name]) for name, w in WEIGHTS[top[0]] if w * f[name] > 0.25),
                      key=lambda kv: kv[1], reverse=True)
    reasons = [FEATURE_TEXT[name].format(**ctx) for name, _ in contribs]
    if second[1] >= 0.2:
        reasons.append(f"second most likely: {LABELS[second[0]].lower()} ({second[1]:.0%} of evidence)")
    return Result(top[0], LABELS[top[0]], scores, confidence, float(margin), reasons)


def alert_for(cls: str, r: dict) -> tuple[str | None, str | None]:
    """Operational flag: (level, text). Only for industrial classes."""
    if cls == "industrial_fire_candidate" and r["hours_since_last"] <= 24 and r["frp_max"] >= 8:
        level = "critical" if r["frp_max"] >= 100 else "high" if r["frp_max"] >= 40 else "watch"
        return level, "new/short-lived thermal anomaly at an industrial facility - verify with imagery"
    if cls == "industrial_persistent" and r["n_days"] >= 4 and r["frp_recent_max"] >= 3 * max(r["frp_mean"], 1.0) \
            and r["frp_recent_max"] >= 30:
        return "high", "intensity well above this source's own recent baseline - possible incident or flaring event"
    return None, None


def classify_sources(sources: pd.DataFrame, month: int) -> pd.DataFrame:
    """Append class, scores, confidence, reasons and alert columns."""
    out = []
    for r in sources.to_dict("records"):
        res = classify_row(r, month)
        level, text = alert_for(res.cls, r)
        out.append({"thermal_class": res.cls, "thermal_label": res.label, "class_confidence": res.confidence,
                    "class_margin": res.margin, "class_scores": res.scores, "reasons": res.reasons,
                    "alert_level": level, "alert_text": text})
    return pd.concat([sources.reset_index(drop=True), pd.DataFrame(out)], axis=1)
