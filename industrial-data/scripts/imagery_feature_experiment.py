"""Does Sentinel-2 evidence (dNBR, SWIR hot pixels) add anything to the Phase 4 model?

Samples labelled units (stratified by class), runs the imagery analysis for each unit's window, then compares
cross-validated macro-F1 of the same model with and without the imagery columns, on the units that have imagery.
Output: models/imagery_experiment.json (+ per-unit cache in data/imagery_experiment/).
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.imagery import core  # noqa: E402

OUT = ROOT / "models" / "imagery_experiment.json"
CACHE = ROOT / "data" / "imagery_experiment"
CLASSES = ["agricultural_burning", "vegetation_fire", "industrial_persistent"]


def sample(per_class: int, seed: int = 7) -> pd.DataFrame:
    d = pd.read_parquet(ROOT / "data_labels" / "labels_v1" / "labels_v1.parquet")
    d = d[d.label.isin(CLASSES)]
    return pd.concat([g.sample(min(per_class, len(g)), random_state=seed) for _, g in d.groupby("label")]).reset_index(drop=True)


def one(row) -> dict:
    f = CACHE / f"{row.label_id}.json"
    if f.exists():
        return json.loads(f.read_text())
    end = pd.Timestamp(row.window_end, tz="UTC").to_pydatetime().replace(hour=12)
    start = pd.Timestamp(row.window_start, tz="UTC").to_pydatetime()
    try:
        res = core.analyse_source(float(row.lon), float(row.lat), end, with_landsat=False, with_images=False, first_seen=start)
        feats = core.imagery_features(res)
        out = {"label_id": row.label_id, "ok": True, **feats}
    except Exception as exc:  # noqa: BLE001
        out = {"label_id": row.label_id, "ok": False, "error": str(exc)[:200]}
    f.write_text(json.dumps(out))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=60)
    ap.add_argument("--threads", type=int, default=8)
    a = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    s = sample(a.per_class)
    with ThreadPoolExecutor(a.threads) as ex:
        res = list(ex.map(one, s.itertuples()))
    r = pd.DataFrame(res)
    r.to_csv(CACHE / "_results.csv", index=False)
    print("units", len(s), "ok", int(r.ok.sum()), "with scene", int(r.get("s2_available", pd.Series(dtype=float)).fillna(0).sum()))


if __name__ == "__main__" and "--compare" not in sys.argv:
    main()


IMAGERY = ["s2_dnbr_p95", "s2_dnbr_mean", "s2_burned_fraction", "s2_hot_pixels"]


def compare(seed: int = 0) -> dict:
    """Grouped (by state) 5-fold CV macro-F1 with and without the imagery columns, full and blind feature sets."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import f1_score
    from sklearn.model_selection import GroupKFold

    from src.ml import features as F
    lab = pd.read_parquet(ROOT.parent / "data" / "ml" / "features_labels.parquet")
    r = pd.read_csv(CACHE / "_results.csv")
    d = lab.merge(r, on="label_id")
    d = d[d.label.isin(CLASSES)].reset_index(drop=True)
    y, groups = d.label.to_numpy(), d.state.fillna("unknown").astype(str).to_numpy()
    out = {"units": int(len(d)), "with_scene": int(d.s2_available.fillna(0).sum()),
           "with_dnbr": int(d.s2_dnbr_p95.notna().sum()), "per_class": d.label.value_counts().to_dict(), "cv": "GroupKFold(5) by state"}
    by_class = d.groupby("label")[IMAGERY].median().round(3)
    out["median_by_class"] = by_class.to_dict(orient="index")
    for name, cols in {"full": F.ALL_FEATURES, "blind": F.BLIND}.items():
        for tag, use in {"without_imagery": cols, "with_imagery": cols + IMAGERY}.items():
            pred = np.empty(len(d), dtype=object)
            for tr, te in GroupKFold(5).split(d, y, groups):
                m = HistGradientBoostingClassifier(class_weight="balanced", random_state=seed).fit(d.loc[tr, use], y[tr])
                pred[te] = m.predict(d.loc[te, use])
            out[f"{name}_{tag}_macro_f1"] = round(float(f1_score(y, pred.astype(str), average="macro")), 3)
    out["imagery_only_macro_f1"] = None
    pred = np.empty(len(d), dtype=object)
    for tr, te in GroupKFold(5).split(d, y, groups):
        m = HistGradientBoostingClassifier(class_weight="balanced", random_state=seed).fit(d.loc[tr, IMAGERY], y[tr])
        pred[te] = m.predict(d.loc[te, IMAGERY])
    out["imagery_only_macro_f1"] = round(float(f1_score(y, pred.astype(str), average="macro")), 3)
    OUT.write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__" and "--compare" in sys.argv:
    print(json.dumps(compare(), indent=2))
