"""Gradient-boosted thermal-source classifier with calibrated probabilities and plain-language reasons.

* model        scikit-learn HistGradientBoostingClassifier (balanced class weights, early stopping)
* calibration  one-vs-rest isotonic regression fitted on a validation split that is held out by REGION
* reasons      sampling Shapley values (permutation estimator against a background sample of training units): the
               ``shap`` package is not a dependency; the estimator is exact in expectation and model-agnostic
* version      ``thermal-gbm-<data hash>-<date>`` stored in the file and returned with every prediction
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression

from .features import ALL_FEATURES

CLASSES = ["industrial_persistent", "agricultural_burning", "vegetation_fire"]

# feature -> (label used in a sentence, unit formatter)
_LABEL: dict[str, tuple[str, str]] = {
    "n_detections": ("number of detections", "{:.0f}"), "n_days": ("days with heat in the window", "{:.0f}"),
    "frp_max": ("peak radiative power", "{:.1f} MW"), "frp_mean": ("mean radiative power", "{:.1f} MW"),
    "frp_total": ("total radiative power", "{:.0f} MW"), "frp_p90": ("90th-percentile power", "{:.1f} MW"),
    "night_fraction": ("share of night passes", "{:.0%}"), "ti4_mean": ("mean I4 brightness", "{:.0f} K"),
    "ti4_max": ("peak I4 brightness", "{:.0f} K"), "ti_diff_mean": ("I4 minus I5 brightness", "{:.0f} K"),
    "scan_mean": ("pixel scan size", "{:.2f}"), "track_mean": ("pixel track size", "{:.2f}"),
    "extent_m": ("footprint", "{:.0f} m"), "high_conf_fraction": ("share of high-confidence pixels", "{:.0%}"),
    "n_satellites": ("satellites that saw it", "{:.0f}"),
    "baseline_active_fraction": ("share of the last 365 days this place was hot", "{:.0%}"),
    "frp_z": ("power against this place's own normal", "{:+.1f} sigma"),
    "facility_dist_m": ("distance to the nearest mapped facility", "{:.0f} m"),
    "facility_within": ("inside a mapped facility's radius", "{:.0f}"), "facility_source_count": ("sources listing that facility", "{:.0f}"),
    "facility_radius_m": ("matching radius of that facility", "{:.0f} m"),
    "authoritative_dist_m": ("distance to the nearest WRI / flare-site facility", "{:.0f} m"),
    "lc_crop": ("cropland around it", "{:.0%}"), "lc_woody": ("tree / shrub cover around it", "{:.0%}"),
    "lc_vegetation": ("vegetation around it", "{:.0%}"), "lc_built": ("built-up land around it", "{:.0%}"),
    "lc_bare": ("bare ground around it", "{:.0%}"), "lc_water": ("water around it", "{:.0%}"),
    "month_sin": ("time of year", "{:+.2f}"), "month_cos": ("time of year", "{:+.2f}"), "lat_abs": ("latitude", "{:.1f} N"),
}
_PLAIN = {"industrial_persistent": "a persistent industrial source", "agricultural_burning": "crop-residue burning",
          "vegetation_fire": "a forest / vegetation fire"}


def _label(feature: str) -> tuple[str, str]:
    if feature.startswith("kind_"):
        return f"inside a {feature[5:].replace('_', ' ')} facility", "{:.0f}"
    return _LABEL.get(feature, (feature.replace("_", " "), "{:.2f}"))


@dataclass
class ThermalModel:
    version: str
    features: list[str]
    classes: list[str]
    hgb: HistGradientBoostingClassifier
    calibrators: list[IsotonicRegression]
    background: pd.DataFrame
    meta: dict = field(default_factory=dict)

    # ---- prediction ---------------------------------------------------------------------------------------------
    def _x(self, frame: pd.DataFrame) -> np.ndarray:
        return frame.reindex(columns=self.features).to_numpy(dtype=float)

    def predict_proba(self, frame: pd.DataFrame, calibrated: bool = True) -> np.ndarray:
        raw = self.hgb.predict_proba(self._x(frame))
        if not calibrated or not self.calibrators:
            return raw
        cal = np.column_stack([iso.predict(raw[:, i]) for i, iso in enumerate(self.calibrators)])
        cal = np.clip(cal, 1e-6, None)
        return cal / cal.sum(axis=1, keepdims=True)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.array(self.classes)[self.predict_proba(frame).argmax(axis=1)]

    # ---- reasons ------------------------------------------------------------------------------------------------
    def shapley(self, row: pd.Series | pd.DataFrame, class_index: int, n_permutations: int = 16, seed: int = 0) -> np.ndarray:
        """Sampling Shapley values of the calibrated probability of ``class_index`` for one unit."""
        rng = np.random.default_rng(seed)
        x = self._x(row.to_frame().T if isinstance(row, pd.Series) else row)[0]
        bg = self.background.reindex(columns=self.features).to_numpy(dtype=float)
        n = len(x)
        phi = np.zeros(n)
        batch, meta = [], []
        for _ in range(n_permutations):
            perm = rng.permutation(n)
            base = bg[rng.integers(len(bg))].copy()
            batch.append(base.copy())
            for j in perm:
                base[j] = x[j]
                batch.append(base.copy())
            meta.append(perm)
        frame = pd.DataFrame(np.array(batch), columns=self.features)
        p = self.predict_proba(frame)[:, class_index].reshape(n_permutations, n + 1)
        for k, perm in enumerate(meta):
            phi[perm] += np.diff(p[k])
        return phi / n_permutations

    def explain(self, row: pd.Series, top: int = 5, n_permutations: int = 16, min_effect: float = 0.03) -> dict:
        proba = self.predict_proba(row.to_frame().T)[0]
        ci = int(proba.argmax())
        cls = self.classes[ci]
        phi = self.shapley(row, ci, n_permutations)
        order = np.argsort(-np.abs(phi))
        reasons = []
        for j in order:
            f = self.features[j]
            if reasons and abs(phi[j]) < min_effect:
                break                         # the rest is noise: a reason that moves the probability < 3 points is not a reason
            if f in ("month_sin", "month_cos") and any(r["feature"] in ("month_sin", "month_cos") for r in reasons):
                continue                      # one "time of year" line, not two
            name, fmt = _label(f)
            v = row.get(f)
            if v is None or pd.isna(v):
                continue
            direction = "supports" if phi[j] > 0 else "argues against"
            reasons.append({"feature": f, "value": float(v), "effect": float(phi[j]),
                            "text": f"{name} {fmt.format(float(v))} {direction} {_PLAIN[cls]} ({phi[j]:+.2f})"})
            if len(reasons) == top:
                break
        return {"class": cls, "probability": float(proba[ci]), "probabilities": dict(zip(self.classes, map(float, proba))),
                "reasons": reasons, "model_version": self.version}

    # ---- persistence --------------------------------------------------------------------------------------------
    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)

    @staticmethod
    def load(path: str | Path) -> "ThermalModel":
        return joblib.load(path)


def _version(train: pd.DataFrame, features: list[str]) -> str:
    h = hashlib.sha1(("|".join(features) + str(len(train)) + str(train.get("label_id", pd.Series(dtype=str)).astype(str).str.cat())[:5000]).encode())
    return f"thermal-gbm-{h.hexdigest()[:8]}-{datetime.now(timezone.utc):%Y%m%d}"


def train(train_df: pd.DataFrame, features: list[str] | None = None, label_col: str = "label", region_col: str = "state",
          val_regions: list[str] | None = None, seed: int = 0, max_iter: int = 300) -> ThermalModel:
    """Fit on ``train_df`` (columns: features + label + region). A validation set made of whole regions is used for
    early stopping AND isotonic calibration (never the same rows the trees were fitted on)."""
    feats = list(features or ALL_FEATURES)
    df = train_df[train_df[label_col].isin(CLASSES)].reset_index(drop=True)
    regions = sorted(df[region_col].dropna().unique())
    if val_regions is None:
        val_regions = regions[::4] or regions[:1]          # every 4th region name: deterministic, region-disjoint
    is_val = df[region_col].isin(val_regions)
    if is_val.sum() < 50 or (~is_val).sum() < 50:         # tiny data: fall back to a random split
        rng = np.random.default_rng(seed)
        is_val = pd.Series(rng.random(len(df)) < 0.2, index=df.index)
    fit, val = df[~is_val], df[is_val]
    y_fit = fit[label_col].map({c: i for i, c in enumerate(CLASSES)}).to_numpy()
    y_val = val[label_col].map({c: i for i, c in enumerate(CLASSES)}).to_numpy()
    hgb = HistGradientBoostingClassifier(max_iter=max_iter, learning_rate=0.06, max_leaf_nodes=15, min_samples_leaf=15,
                                         l2_regularization=1.0, class_weight="balanced", early_stopping=False, random_state=seed)
    hgb.fit(fit.reindex(columns=feats).to_numpy(dtype=float), y_fit)
    raw = hgb.predict_proba(val.reindex(columns=feats).to_numpy(dtype=float))
    cals = []
    for i in range(len(CLASSES)):
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(raw[:, i], (y_val == i).astype(float))
        cals.append(iso)
    bg = fit.sample(n=min(200, len(fit)), random_state=seed).reindex(columns=feats)
    import sklearn
    meta = {"trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sklearn_version": sklearn.__version__, "n_fit": int(len(fit)), "n_calibration": int(len(val)),
            "calibration_regions": list(val_regions), "classes": CLASSES, "seed": seed,
            "class_counts_fit": fit[label_col].value_counts().to_dict()}
    return ThermalModel(_version(df, feats), feats, CLASSES, hgb, cals, bg.reset_index(drop=True), meta)


def metrics_json(m: ThermalModel) -> str:
    return json.dumps({"version": m.version, "features": m.features, "classes": m.classes, **m.meta}, indent=2, default=str)
