"""Train a gradient-boosted thermal-source classifier and compare it with the rule classifier.

Labels come from LONG-RUN behaviour of a ~2 km cell over the whole S-NPP archive (not from the 7-day window
the classifiers see), plus the OSM facility file and ESA WorldCover. They are weak labels, NOT ground truth:

  industrial_persistent      facility within radius, active >= 55 days over >= 6 months
  industrial_fire_candidate  facility within radius, active <= 10 days in the archive (episodic)
  unmapped_persistent        no facility within 3 km, active >= 55 days over >= 6 months
  agricultural_burning       no facility within 3 km, <= 15 active days, cropland >= 70 %, >= 70 % of active days
                             in Oct-Nov / Apr-May
  vegetation_fire            no facility within 3 km, <= 15 active days, natural vegetation >= 70 %

Samples are 7-day windows (live-pipeline features). Split: held-out REGIONS (states) x held-out TIME (windows
from 2026-04-01) - training uses only other states and earlier windows. Never random.
Run: python industrial-data/scripts/train_classifier.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import geopandas as gpd
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "industrial-data"))
from src.thermal import archive, classifier, landcover, pipeline, sources  # noqa: E402
from src.thermal.sites import EARTH_R, KINDS, SiteIndex  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("train")
for noisy in ("rasterio", "rasterio._err", "fiona", "pyogrio"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

SEED = 0
CELL = 0.02
CLASSES = ["industrial_persistent", "industrial_fire_candidate", "unmapped_persistent",
           "agricultural_burning", "vegetation_fire"]
PRED_CLASSES = CLASSES + ["other"]
AG_MONTHS = {10, 11, 4, 5}
TEST_FROM = pd.Timestamp("2026-04-01", tz="UTC")
THERMAL_ONLY = ["n_detections", "n_days", "frp_mean", "frp_max", "frp_total", "night_fraction", "extent_m",
                "ti4_mean", "high_conf_fraction"]
CONTEXT = ["site_within", "site_distance_m", "site_continuous", "kind_code", "lc_crop", "lc_vegetation",
           "lc_built", "lc_bare"]
KIND_CODES = {k: i for i, k in enumerate(KINDS)}
OUT_DOC = ROOT / "docs" / "EVALUATION.md"


def cell_key(lat, lon):
    return (np.floor(np.asarray(lat) / CELL).astype("int64") * 10000
            + np.floor(np.asarray(lon) / CELL).astype("int64"))


def build_cells(det: pd.DataFrame, idx: SiteIndex) -> pd.DataFrame:
    det = det.assign(cell=cell_key(det.latitude, det.longitude), date=det.acq_datetime.dt.date,
                     ym=det.acq_datetime.dt.strftime("%Y-%m"))
    days = det.drop_duplicates(["cell", "date"])
    g = det.groupby("cell")
    cells = pd.DataFrame({
        "lat": g.latitude.mean(), "lon": g.longitude.mean(),
        "active_days": days.groupby("cell").size(), "active_months": g.ym.nunique(),
        "ag_share": days.assign(a=days.acq_datetime.dt.month.isin(AG_MONTHS)).groupby("cell").a.mean(),
    }).reset_index()
    m = idx.match(cells.lat.to_numpy(), cells.lon.to_numpy())
    cells["within"] = [x.within for x in m]
    dist, _ = idx._tree.query(np.radians(cells[["lat", "lon"]].to_numpy()), k=1)
    cells["nearest_m"] = dist[:, 0] * EARTH_R
    return cells


def label_cells(cells: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    persistent = (cells.active_days >= 55) & (cells.active_months >= 6)
    far = (~cells.within) & (cells.nearest_m > 3000)
    picks = {
        "industrial_persistent": cells[cells.within & persistent],
        "industrial_fire_candidate": cells[cells.within & (cells.active_days <= 10)],
        "unmapped_persistent": cells[far & persistent],
    }
    out = []
    for name, df in picks.items():
        df = df.sample(min(len(df), 1500), random_state=SEED)
        out.append(df.assign(label=name))
    low = cells[far & (cells.active_days <= 15)]
    low = low.sample(min(len(low), 4000), random_state=SEED)
    out.append(low.assign(label="_lowcandidate"))
    lab = pd.concat(out, ignore_index=True)
    log.info("land cover for %d cells", len(lab))
    lc = landcover.sample_many(lab[["lat", "lon"]], ROOT / "data" / "thermal" / "landcover_cache_eval.json")
    lab = pd.concat([lab, lc], axis=1).dropna(subset=["lc_crop", "lc_vegetation"])
    ag = (lab.label == "_lowcandidate") & (lab.lc_crop >= 0.7) & (lab.ag_share >= 0.7)
    veg = (lab.label == "_lowcandidate") & (lab.lc_vegetation >= 0.7) & ~ag
    lab.loc[ag, "label"] = "agricultural_burning"
    lab.loc[veg, "label"] = "vegetation_fire"
    lab = lab[lab.label != "_lowcandidate"]
    parts = [g.sample(min(len(g), 1500), random_state=SEED) for _, g in lab.groupby("label")]
    return pd.concat(parts, ignore_index=True)


def attach_state(cells: pd.DataFrame) -> pd.DataFrame:
    states = pipeline.load_boundary("ADM1", ROOT / "data" / "thermal").to_crs(4326)[["shapeName", "geometry"]]
    pts = gpd.GeoDataFrame(cells[["cell"]], geometry=gpd.points_from_xy(cells.lon, cells.lat), crs=4326)
    j = gpd.sjoin(pts, states, how="left", predicate="within").drop_duplicates("cell")
    return cells.merge(j[["cell", "shapeName"]].rename(columns={"shapeName": "state"}), on="cell")


def window_rows(det: pd.DataFrame, labelled: pd.DataFrame, idx: SiteIndex) -> pd.DataFrame:
    keys = set(labelled.cell)
    det = det.assign(cell=cell_key(det.latitude, det.longitude)).reset_index(drop=True)
    out = []
    for start in pd.date_range(det.acq_datetime.min().normalize(), det.acq_datetime.max(), freq="7D"):
        end = start + pd.Timedelta(days=7)
        w = det[(det.acq_datetime >= start) & (det.acq_datetime < end)].reset_index(drop=True)
        if len(w) < 50:
            continue
        w = sources.cluster_detections(w)
        keep = w.loc[w.cell.isin(keys), "cluster"].unique()
        w = w[w.cluster.isin(keep)]
        if w.empty:
            continue
        sm = sources.summarise(w, 7)
        sm["win_start"], sm["win_end"] = start, end
        out.append(sm)
    rows = pd.concat(out, ignore_index=True)
    rows["cell"] = cell_key(rows.lat, rows.lon)
    rows = rows.merge(labelled[["cell", "label", "state", "lc_crop", "lc_vegetation", "lc_built", "lc_bare",
                                "lc_dominant"]], on="cell", how="inner")
    m = idx.match(rows.lat.to_numpy(), rows.lon.to_numpy())
    rows["site_within"] = [x.within for x in m]
    rows["site_distance_m"] = [min(x.distance_m, 1e5) if x.distance_m is not None else 1e5 for x in m]
    rows["site_continuous"] = [x.continuous_prior for x in m]
    rows["site_kind"] = [x.kind for x in m]
    rows["site_name"] = [x.site_name for x in m]
    rows["site_ref"] = [x.site_ref for x in m]
    rows["kind_code"] = rows.site_kind.map(KIND_CODES).fillna(-1).astype(int)
    rows["site_within"] = rows.site_within.astype(int)
    return rows


def rules_predict(rows: pd.DataFrame) -> list[str]:
    preds = []
    for r in rows.to_dict("records"):
        res = classifier.classify_row(r, int(pd.Timestamp(r["last_seen"]).month))
        preds.append(res.cls)
    return preds


def report(y, pred, labels):
    cr = classification_report(y, pred, labels=labels, output_dict=True, zero_division=0)
    cr["accuracy"] = float(accuracy_score(y, pred))
    cm = confusion_matrix(y, pred, labels=PRED_CLASSES)
    return cr, cm


def md_table(cr, labels):
    lines = ["| class | precision | recall | F1 | support |", "|---|---|---|---|---|"]
    for c in labels:
        d = cr[c]
        lines.append(f"| {c} | {d['precision']:.2f} | {d['recall']:.2f} | {d['f1-score']:.2f} | {int(d['support'])} |")
    return "\n".join(lines)


def md_cm(cm):
    head = "| true \\ predicted | " + " | ".join(PRED_CLASSES) + " |"
    sep = "|---|" + "---|" * len(PRED_CLASSES)
    body = [f"| **{c}** | " + " | ".join(str(int(v)) for v in cm[i]) + " |" for i, c in enumerate(CLASSES)]
    return "\n".join([head, sep] + body)


def main() -> None:
    log.info("loading archive")
    det = archive.load_archive(ROOT / "data" / "archive" / "snpp", "SNPP")
    det = pipeline.clip_to_india(det, ROOT / "data" / "thermal")
    span = (det.acq_datetime.min(), det.acq_datetime.max())
    merged = ROOT / "data" / "facilities" / "facilities.geojson"   # OSM + WRI (+ GEM/VNF) when built
    sites_file = merged if merged.exists() else ROOT / "data" / "thermal" / "sites_india.geojson"
    log.info("facility file: %s", sites_file.relative_to(ROOT))
    idx = SiteIndex.from_geojson(sites_file)
    log.info("detections=%d span=%s..%s facilities=%d", len(det), span[0].date(), span[1].date(), len(idx))

    cells = build_cells(det, idx)
    lab = attach_state(label_cells(cells)).dropna(subset=["state"])
    log.info("labelled cells: %s", lab.label.value_counts().to_dict())
    rows = window_rows(det, lab, idx)
    rows = rows[(rows.win_end < TEST_FROM) | (rows.win_start >= TEST_FROM)].copy()
    rows["period"] = np.where(rows.win_start >= TEST_FROM, "test", "train")
    log.info("window rows: %d", len(rows))

    # choose the held-out region set (every 3rd state by name; offset with the best minimum test support)
    states = sorted(rows.state.unique())
    best = None
    for off in range(3):
        test_states = set(states[off::3])
        t = rows[rows.state.isin(test_states) & (rows.period == "test")]
        support = t.label.value_counts().reindex(CLASSES).fillna(0)
        if best is None or support.min() > best[0]:
            best = (support.min(), test_states)
    test_states = best[1]
    is_test = rows.state.isin(test_states) & (rows.period == "test")
    is_train = ~rows.state.isin(test_states) & (rows.period == "train")
    train, test = rows[is_train].copy(), rows[is_test].copy()
    log.info("train=%d test=%d test_states=%s", len(train), len(test), sorted(test_states))

    rules = rules_predict(test)
    results = {"rules": report(test.label, rules, CLASSES)}
    models = {}
    for name, feats in (("model", THERMAL_ONLY + CONTEXT), ("model_thermal_only", THERMAL_ONLY)):
        clf = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.08, max_depth=6, class_weight="balanced",
                                             early_stopping=False, random_state=SEED)
        clf.fit(train[feats], train.label)
        pred = clf.predict(test[feats])
        results[name] = report(test.label, pred, CLASSES)
        models[name] = (clf, feats)
    (ROOT / "data" / "models").mkdir(parents=True, exist_ok=True)
    joblib.dump(models["model"], ROOT / "data" / "models" / "thermal_gbm.joblib")

    macro = {k: float(v[0]["macro avg"]["f1-score"]) for k, v in results.items()}
    acc = {k: float(v[0]["accuracy"]) for k, v in results.items()}
    beats = macro["model"] > macro["rules"]
    verdict = ("The trained model BEATS the rule classifier on macro-F1" if beats else
               "The trained model does NOT beat the rule classifier on macro-F1")
    split = pd.DataFrame({"train": train.label.value_counts(), "test": test.label.value_counts()}).reindex(CLASSES).fillna(0).astype(int)

    doc = [
        "# Evaluation: rule classifier vs trained gradient-boosted model",
        "",
        f"_Generated by `industrial-data/scripts/train_classifier.py` (seed {SEED})._",
        "",
        f"**Verdict:** {verdict}: macro-F1 rules {macro['rules']:.3f}, model {macro['model']:.3f}, "
        f"model with thermal-only features {macro['model_thermal_only']:.3f}. "
        f"Accuracy {acc['rules']:.3f} / {acc['model']:.3f} / {acc['model_thermal_only']:.3f}.",
        "",
        "> **These labels are weak, rule-derived and NOT ground truth.** Facility proximity (OSM) and land cover "
        "(ESA WorldCover) both define the labels and are inputs to both classifiers, so the numbers measure "
        "agreement with the labelling rules on unseen regions and months, not real-world accuracy. The "
        "thermal-only model shows how much separation comes from satellite behaviour alone.",
        "",
        "## Data",
        f"* VIIRS S-NPP archive `{span[0].date()}` to `{span[1].date()}` ({len(det):,} detections inside India), "
        f"{len(idx):,} merged facilities (OSM + WRI GPPD; data/facilities), ESA WorldCover 2021.",
        "* Unit: 7-day window source (same features as the live pipeline). Labels: long-run behaviour of the ~2 km cell "
        "(rules in the script header).",
        f"* Split: train = states outside the held-out set AND windows ending before 2026-04-01; test = held-out "
        f"states ({', '.join(sorted(test_states))}) AND windows from 2026-04-01. No cell appears on both sides.",
        "* Per-class cap 1,500 cells. `other` is not a labelled class; the rules may predict it (counted as wrong).",
        "",
        "| class | train windows | test windows |", "|---|---|---|",
        *[f"| {c} | {split.loc[c, 'train']} | {split.loc[c, 'test']} |" for c in CLASSES],
        "",
    ]
    for name, title in (("rules", "Rule classifier (`src/thermal/classifier.py`)"),
                        ("model", "Gradient-boosted model (thermal + facility + land-cover features)"),
                        ("model_thermal_only", "Gradient-boosted model, thermal-behaviour features only")):
        cr, cm = results[name]
        doc += [f"## {title}", "", md_table(cr, CLASSES), "",
                f"macro-F1 **{cr['macro avg']['f1-score']:.3f}**, accuracy **{cr['accuracy']:.3f}**", "",
                "Confusion matrix (rows true, columns predicted):", "", md_cm(cm), ""]
    doc += [
        "## Limitations",
        "* Weak labels, single archive (S-NPP only, ~15 months), one train/test split; no confidence intervals.",
        "* `industrial_fire_candidate` here means *episodic detection at a facility*, not a confirmed fire. "
        "Real incident evidence is in `data/incidents/incident_firms_match.csv`, not in this table.",
        "* The model cannot use month/season (held out by design); the rules do, which favours them on agriculture.",
        "* Region and period hold-out make the test small for some classes; read support before trusting a score.",
    ]
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text("\n".join(doc) + "\n", encoding="utf-8")
    (ROOT / "data" / "models" / "eval.json").write_text(json.dumps({"macro_f1": macro, "accuracy": acc,
                                                                    "train": int(len(train)), "test": int(len(test))}, indent=2))
    print(json.dumps({"macro_f1": macro, "accuracy": acc, "train": len(train), "test": len(test),
                      "test_states": sorted(test_states), "split": split.to_dict()}, indent=2))


if __name__ == "__main__":
    main()
