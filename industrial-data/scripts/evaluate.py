"""Train the thermal-source model on labels-v1 and write the evaluation (docs/EVALUATION.md + charts + model files).

    cd industrial-data && python scripts/evaluate.py [--refresh-features]

Everything reported is computed here from the label files; nothing is typed in by hand.
Outputs
  models/thermal_gbm.joblib      the model the live worker loads (committed, small)
  models/metrics.json            what /api/thermal/model returns
  docs/EVALUATION.md             the report
  docs/images/eval_*.png         charts
Run order: build the labels (python -m src.labels.cli build), fetch incident detections
(python scripts/fetch_incident_units.py), then this script.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support, roc_auc_score

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "industrial-data"))
from src.labels import rules as R  # noqa: E402
from src.ml import dataset as D  # noqa: E402
from src.ml import features as F  # noqa: E402
from src.ml import model as M  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("evaluate")
CLASSES = M.CLASSES
SEED = 0
CACHE = ROOT / "data" / "ml"
OUT_DOC = ROOT / "docs" / "EVALUATION.md"
IMG = ROOT / "docs" / "images"
MODEL_DIR = ROOT / "industrial-data" / "models"


# --------------------------------------------------------------------------- metrics
def prf(y_true, y_pred, labels):
    p, r, f, s = precision_recall_fscore_support(y_true, y_pred, labels=labels, zero_division=0)
    return {c: {"p": float(p[i]), "r": float(r[i]), "f1": float(f[i]), "n": int(s[i])} for i, c in enumerate(labels)}


def macro_f1(y_true, y_pred) -> float:
    _, _, f, _ = precision_recall_fscore_support(y_true, y_pred, labels=CLASSES, zero_division=0)
    return float(np.mean(f))


def bootstrap(y_true, preds: dict[str, np.ndarray], groups: np.ndarray, n: int = 400, seed: int = SEED) -> dict:
    """Block bootstrap over cells (units of one cell share their surroundings): 95 % CI of macro-F1 and of the
    difference between the first two predictors."""
    rng = np.random.default_rng(seed)
    ug, inv = np.unique(groups, return_inverse=True)
    idx_by = [np.flatnonzero(inv == g) for g in range(len(ug))]
    names = list(preds)
    stats = {k: [] for k in names}
    diffs = []
    for _ in range(n):
        pick = np.concatenate([idx_by[g] for g in rng.integers(len(ug), size=len(ug))])
        vals = {k: macro_f1(y_true[pick], preds[k][pick]) for k in names}
        for k in names:
            stats[k].append(vals[k])
        if len(names) >= 2:
            diffs.append(vals[names[0]] - vals[names[1]])
    ci = {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for k, v in stats.items()}
    out = {"ci": ci}
    if diffs:
        out["diff_ci"] = [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))]
        out["diff_names"] = names[:2]
    return out


def threshold_for_false_alarms(p_neg: np.ndarray, k: int) -> float:
    """Smallest score threshold that flags at most ``k`` negatives. Ties are never split: if the k-th highest negative
    score is shared with more, the threshold moves above the whole tie (otherwise 'k' alarms would silently become
    hundreds)."""
    if len(p_neg) == 0:
        return 1.0
    for t in np.unique(p_neg):                      # ascending
        if int((p_neg >= t).sum()) <= k:
            return float(t)
    return float(np.nextafter(p_neg.max(), np.inf))


def ece(proba: np.ndarray, y_idx: np.ndarray, bins: int = 10) -> float:
    conf, pred = proba.max(axis=1), proba.argmax(axis=1)
    correct = (pred == y_idx).astype(float)
    edges = np.linspace(0, 1, bins + 1)
    e = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            e += m.mean() * abs(correct[m].mean() - conf[m].mean())
    return float(e)


# --------------------------------------------------------------------------- data
def load_features(refresh: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    CACHE.mkdir(parents=True, exist_ok=True)
    lab_path, inc_path = CACHE / "features_labels.parquet", CACHE / "features_incidents.parquet"
    fac = ROOT / "data" / "facilities" / "facilities.geojson"
    if refresh or not lab_path.exists():
        lab = D.labelled_features(ROOT / "industrial-data" / "data_labels" / "labels_v1" / "labels_v1.parquet",
                                  [ROOT / "data" / "archive" / "snpp", ROOT / "data" / "archive" / "noaa20"], fac,
                                  ROOT / "data" / "labels" / "landcover_cache_labels.json")
        lab["rule_pred"] = D.rules_predict(lab)
        lab.to_parquet(lab_path, index=False)
    units = list((ROOT / "data" / "incidents" / "units").glob("*.parquet"))
    stale = inc_path.exists() and any(u.stat().st_mtime > inc_path.stat().st_mtime for u in units)   # new incident downloads
    if refresh or stale or not inc_path.exists():
        inc = D.incident_features(ROOT / "industrial-data" / "data_labels" / "incidents_verified.csv",
                                  ROOT / "data" / "incidents" / "units", fac, ROOT / "data" / "labels" / "landcover_cache_labels.json")
        det = inc[inc.detected.fillna(False).astype(bool)].copy()
        if len(det):
            inc.loc[det.index, "rule_pred"] = D.rules_predict(det)
        inc.to_parquet(inc_path, index=False)
    return pd.read_parquet(lab_path), pd.read_parquet(inc_path)


# --------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-features", action="store_true")
    args = ap.parse_args()
    lab, inc = load_features(args.refresh_features)
    train = lab[lab.split == "train"].reset_index(drop=True)
    test = lab[lab.split == "test"].reset_index(drop=True)
    log.info("train %d, test %d, incidents %d (%d with detections)", len(train), len(test), len(inc), int(inc.detected.fillna(False).sum()))

    # ---- full model ---------------------------------------------------------------------------------------------
    full = M.train(train, F.ALL_FEATURES, seed=SEED)
    y = test.label.to_numpy()
    p_full = full.predict_proba(test)
    pred_full = np.array(CLASSES)[p_full.argmax(axis=1)]
    pred_raw = np.array(CLASSES)[full.predict_proba(test, calibrated=False).argmax(axis=1)]
    pred_rules = test.rule_pred.to_numpy()
    y_idx = np.array([CLASSES.index(c) for c in y])

    # ---- ablations: leave one feature group out, and groups alone ---------------------------------------------
    ablations: dict[str, dict] = {}

    def run(name: str, feats: list[str]) -> M.ThermalModel:
        m = M.train(train, feats, seed=SEED)
        pred = m.predict(test)
        ablations[name] = {"macro_f1": macro_f1(y, pred), "accuracy": float(accuracy_score(y, pred)), "n_features": len(feats),
                           "per_class": prf(y, pred, CLASSES)}
        return m

    for g, cols in F.GROUPS.items():
        run(f"without {g}", [f for f in F.ALL_FEATURES if f not in cols])
    blind = run("thermal behaviour only (blind)", F.BLIND)
    run("thermal + baseline", F.THERMAL_BASELINE)

    # ---- headline numbers -----------------------------------------------------------------------------------------
    groups = test.cell_key.to_numpy()
    boot = bootstrap(y, {"model": pred_full, "rules": pred_rules, "blind": blind.predict(test)}, groups)
    boot_blind = bootstrap(y, {"blind": blind.predict(test), "rules": pred_rules}, groups)
    res = {
        "rules": {"macro_f1": macro_f1(y, pred_rules), "accuracy": float(accuracy_score(y, pred_rules)), "per_class": prf(y, pred_rules, CLASSES)},
        "model": {"macro_f1": macro_f1(y, pred_full), "accuracy": float(accuracy_score(y, pred_full)), "per_class": prf(y, pred_full, CLASSES),
                  "ece_calibrated": ece(p_full, y_idx), "ece_raw": ece(full.predict_proba(test, calibrated=False), y_idx),
                  "macro_f1_uncalibrated": macro_f1(y, pred_raw)},
        "blind": ablations["thermal behaviour only (blind)"],
        "bootstrap": boot, "bootstrap_blind_vs_rules": boot_blind,
    }
    labels_all = CLASSES + ["industrial_fire_candidate", "unmapped_persistent", "other"]
    cm_model = confusion_matrix(y, pred_full, labels=CLASSES)
    cm_rules = confusion_matrix(y, pred_rules, labels=labels_all[:3] + labels_all[3:])

    # ---- industrial vs not: incidents against held-out agri / vegetation fires ---------------------------------
    neg = test[test.label.isin(["agricultural_burning", "vegetation_fire"])]
    days = max((pd.to_datetime(test.window_end).max() - pd.Timestamp(R.TEST_FROM)).days + 1, 1)
    pos = inc[inc.detected.fillna(False).astype(bool)].copy()
    n_inc, n_det = len(inc), len(pos)
    p_neg = full.predict_proba(neg)[:, 0]
    p_pos = full.predict_proba(pos)[:, 0] if len(pos) else np.array([])
    rules_neg = neg.rule_pred.isin(["industrial_persistent", "industrial_fire_candidate"])
    rules_pos = pos.rule_pred.isin(["industrial_persistent", "industrial_fire_candidate"]) if len(pos) else pd.Series(dtype=bool)
    op = []
    for fa_per_day in (0.25, 0.5, 1.0, 2.0):
        k = int(round(fa_per_day * days))
        thr = threshold_for_false_alarms(p_neg, k)
        flagged = float((p_pos >= thr).sum()) if len(p_pos) else 0.0
        op.append({"false_alarms_per_day": fa_per_day, "threshold": float(thr),
                   "recall_of_detected": flagged / n_det if n_det else None, "recall_of_all": flagged / n_inc if n_inc else None,
                   "units_flagged": int((p_neg >= thr).sum())})
    auc = float(roc_auc_score(np.r_[np.zeros(len(p_neg)), np.ones(len(p_pos))], np.r_[p_neg, p_pos])) if len(p_pos) and len(p_neg) else None
    tier = {}
    for t, g in pos.assign(p=p_pos, rule_hit=rules_pos.to_numpy()).groupby("location_basis"):
        tier[t] = {"n": int(len(g)), "model_p_median": float(g.p.median()), "model_hit_at_0.5": int((g.p >= 0.5).sum()),
                   "rules_hit": int(g.rule_hit.sum())}
    incident = {"n_incidents": n_inc, "n_with_detection": n_det, "test_days": days, "negatives": int(len(neg)),
                "negatives_per_day": len(neg) / days, "auc_industrial_vs_agri_veg": auc, "operating_points": op,
                "rules": {"recall_of_detected": float(rules_pos.mean()) if n_det else None, "recall_of_all": float(rules_pos.sum() / n_inc) if n_inc else None,
                          "false_alarms_per_day": float(rules_neg.sum() / days), "units_flagged": int(rules_neg.sum())},
                "by_tier": tier}

    # ---- permutation importance (global) --------------------------------------------------------------------------
    rng = np.random.default_rng(SEED)
    X = test.reindex(columns=F.ALL_FEATURES)
    base = macro_f1(y, full.predict(test))
    imp = {}
    for f in F.ALL_FEATURES:
        drops = []
        for _ in range(5):
            Xp = X.copy()
            Xp[f] = rng.permutation(Xp[f].to_numpy())
            drops.append(base - macro_f1(y, full.predict(Xp)))
        imp[f] = float(np.mean(drops))
    top_imp = sorted(imp.items(), key=lambda kv: -kv[1])[:10]

    # ---- persist --------------------------------------------------------------------------------------------------
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    full.meta.update({"test_macro_f1": res["model"]["macro_f1"], "rules_macro_f1": res["rules"]["macro_f1"],
                      "blind_macro_f1": res["blind"]["macro_f1"], "labels_manifest": "data_labels/labels_v1/manifest_v1.json"})
    full.save(MODEL_DIR / "thermal_gbm.joblib")
    metrics = {"model_version": full.version, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "classes": CLASSES, "features": F.ALL_FEATURES, "label_set": R.RULE_VERSION,
               "split": {"train": int(len(train)), "test": int(len(test)), "test_from": str(R.TEST_FROM)},
               "results": res, "ablations": ablations, "incidents": incident, "permutation_importance_top10": top_imp,
               "leakage_note": "weak labels are produced by rules that read facility proximity, land cover, season and baseline; "
                               "the blind row removes all of them"}
    (MODEL_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    write_report(metrics, cm_model, cm_rules, labels_all, test, inc)
    charts(metrics, cm_model, cm_rules, labels_all, p_neg, p_pos, days, res)
    print(json.dumps({"model_version": full.version, "rules_macro_f1": res["rules"]["macro_f1"],
                      "model_macro_f1": res["model"]["macro_f1"], "blind_macro_f1": res["blind"]["macro_f1"],
                      "ci": boot["ci"], "diff_ci": boot.get("diff_ci"), "incident": {k: incident[k] for k in
                      ("n_incidents", "n_with_detection", "auc_industrial_vs_agri_veg", "operating_points", "rules")}}, indent=2, default=str))


# --------------------------------------------------------------------------- report
def pct(x) -> str:
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.3f}"


N_VERIFIED = int(pd.read_csv(ROOT / "industrial-data" / "data_labels" / "incidents_verified.csv")["verified"].astype(bool).sum())


def write_report(m: dict, cm_model, cm_rules, labels_all, test: pd.DataFrame, inc: pd.DataFrame) -> None:
    r, mo, bl = m["results"]["rules"], m["results"]["model"], m["results"]["blind"]
    ci = m["results"]["bootstrap"]["ci"]
    dci = m["results"]["bootstrap"].get("diff_ci")
    inc_m = m["incidents"]
    beats = mo["macro_f1"] > r["macro_f1"] and (dci is None or dci[0] > 0)
    lines = [
        "# Evaluation: rule classifier vs trained model (labels-v1)", "",
        f"_Generated by `industrial-data/scripts/evaluate.py` on {m['generated_at']}; model `{m['model_version']}`._", "",
        f"**Verdict:** {'The trained model beats the rule classifier on the held-out test split' if beats else 'The trained model does NOT clearly beat the rules on the held-out test split'}: "
        f"macro-F1 {pct(mo['macro_f1'])} (95 % CI {pct(ci['model'][0])}-{pct(ci['model'][1])}) vs rules {pct(r['macro_f1'])} "
        f"({pct(ci['rules'][0])}-{pct(ci['rules'][1])}); difference CI {pct(dci[0]) if dci else '-'} to {pct(dci[1]) if dci else '-'}. "
        f"**These labels are rule-derived**, so this is agreement with the labelling rules. The model that is blind to the rule "
        f"inputs (thermal behaviour only) scores {pct(bl['macro_f1'])} (CI {pct(ci['blind'][0])}-{pct(ci['blind'][1])}): that is the "
        "part of the answer that comes from the satellite signal alone.", "",
        "## Data", "",
        f"* Label set `{m['label_set']}` ([docs/LABELS.md](LABELS.md)): train {m['split']['train']:,} units (held-in regions, before "
        f"{m['split']['test_from']}), test {m['split']['test']:,} units (held-out regions, from that date). Region and time are both held out; "
        "no random split anywhere.",
        f"* Features ({len(m['features'])}): thermal behaviour, 365-day baseline (active fraction, FRP z-score), facility context, land cover, "
        "season / latitude. One definition (`src/ml/features.py`) serves training, evaluation and the live pipeline.",
        "* Model: scikit-learn HistGradientBoostingClassifier, balanced class weights, isotonic calibration on a validation set of whole regions.",
        "* Rules: the live `src/thermal/classifier.py`, run on the same features; rule classes with no label counterpart count as wrong.", "",
        "## Held-out test results", "",
        "| | macro-F1 | accuracy |", "|---|---|---|",
        f"| Rule classifier | {pct(r['macro_f1'])} | {pct(r['accuracy'])} |",
        f"| Gradient-boosted model | **{pct(mo['macro_f1'])}** | **{pct(mo['accuracy'])}** |",
        f"| Model, thermal behaviour only (blind to the rule inputs) | {pct(bl['macro_f1'])} | {pct(bl['accuracy'])} |", "",
        "### Per class (precision / recall / F1, support)", "",
        "| class | rules | model | blind model |", "|---|---|---|---|",
    ]
    for c in CLASSES:
        def cell(d):
            x = d["per_class"][c]
            return f"{x['p']:.2f} / {x['r']:.2f} / {x['f1']:.2f}"
        lines.append(f"| {c} (n={r['per_class'][c]['n']}) | {cell(r)} | {cell(mo)} | {cell(bl)} |")
    lines += ["", f"Calibration (expected calibration error on the test split): {mo['ece_calibrated']:.3f} calibrated vs "
              f"{mo['ece_raw']:.3f} raw; macro-F1 of the uncalibrated model {pct(mo['macro_f1_uncalibrated'])}.", "",
              "### Confusion matrices (rows true, columns predicted)", "", "Model:", "",
              "| true \\ predicted | " + " | ".join(CLASSES) + " |", "|---|" + "---|" * len(CLASSES)]
    for i, c in enumerate(CLASSES):
        lines.append(f"| **{c}** | " + " | ".join(str(int(v)) for v in cm_model[i]) + " |")
    lines += ["", "Rules:", "", "| true \\ predicted | " + " | ".join(labels_all) + " |", "|---|" + "---|" * len(labels_all)]
    for i, c in enumerate(CLASSES):
        lines.append(f"| **{c}** | " + " | ".join(str(int(v)) for v in cm_rules[i]) + " |")
    lines += ["", "## Which inputs carry the result (ablations on the same test split)", "", "| model | features | macro-F1 | accuracy |", "|---|---|---|---|",
              f"| all features | {len(m['features'])} | {pct(mo['macro_f1'])} | {pct(mo['accuracy'])} |"]
    for name, a in m["ablations"].items():
        lines.append(f"| {name} | {a['n_features']} | {pct(a['macro_f1'])} | {pct(a['accuracy'])} |")
    lines += ["", "Permutation importance on the test split (drop in macro-F1 when a feature is shuffled): " +
              ", ".join(f"`{f}` {v:+.3f}" for f, v in m["permutation_importance_top10"][:8]) + ".", "",
              "## Industrial fires: the independent check", "",
              "The weak labels cannot tell us whether the system separates a real industrial fire from agricultural or forest "
              f"burning, because the rules define the classes. The {inc_m['n_incidents']} verified incidents "
              + (f"evaluated here (of {N_VERIFIED} verified; the rest are still being downloaded from the rate-limited FIRMS archive) "
                 if inc_m['n_incidents'] < N_VERIFIED else "")
              + "are independent of the rule inputs. "
              f"For {inc_m['n_with_detection']} of them VIIRS saw heat inside the incident circle during the event window; the other "
              f"{inc_m['n_incidents'] - inc_m['n_with_detection']} produced no detection and cannot be classified by any thermal method.", "",
              f"Negatives are the {inc_m['negatives']:,} held-out agricultural / vegetation units over {inc_m['test_days']} days "
              f"({inc_m['negatives_per_day']:.1f} per day in the four held-out states). AUC of P(industrial) separating incidents from them: "
              f"**{pct(inc_m['auc_industrial_vs_agri_veg'])}**.", "",
              "| operating point | false alarms / day | incidents flagged (of those detected) | of all incidents |", "|---|---|---|---|"]
    for o in inc_m["operating_points"]:
        lines.append(f"| model, P(industrial) >= {o['threshold']:.3g} | {o['false_alarms_per_day']} | "
                     f"{pct(o['recall_of_detected'])} | {pct(o['recall_of_all'])} |")
    ru = inc_m["rules"]
    lines.append(f"| rules (persistent or fire-candidate class) | {ru['false_alarms_per_day']:.2f} | {pct(ru['recall_of_detected'])} | {pct(ru['recall_of_all'])} |")
    lines += ["", "By how well the incident's location is known:", "", "| location tier | incidents with detection | model P(industrial) median | model >= 0.5 | rules flagged |", "|---|---|---|---|---|"]
    for t, d in inc_m["by_tier"].items():
        lines.append(f"| {t} | {d['n']} | {d['model_p_median']:.2f} | {d['model_hit_at_0.5']} | {d['rules_hit']} |")
    lines += ["", "## Limits of these numbers", "",
              "* Weak, rule-derived labels: the headline macro-F1 is agreement with the labelling rules. Only the incident check and the "
              "blind model are independent of them.",
              f"* {inc_m['n_incidents']} incidents is a small sample; per-tier counts are single digits. Treat the recall figures as indications, not rates.",
              "* One model, one seed, one split. The confidence intervals are block bootstraps over cells; they do not cover choice of split.",
              "* Vegetation fires are not confirmed by burned-area data; GEM trackers are not loaded (see LABELS.md).",
              "* `industrial_fire_candidate` has no training examples (the verified incidents are test-only), so the model cannot output it; "
              "an industrial fire shows up as a high P(industrial) with episodic behaviour and a large frp_z.", ""]
    OUT_DOC.write_text("\n".join(lines), encoding="utf-8")


def charts(m, cm_model, cm_rules, labels_all, p_neg, p_pos, days, res) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    IMG.mkdir(parents=True, exist_ok=True)
    # 1: per-class F1 and the ablation ladder
    fig, ax = plt.subplots(1, 2, figsize=(11.5, 4.2), gridspec_kw={"width_ratios": [1, 1.2]})
    w = 0.27
    x = np.arange(len(CLASSES))
    for k, (name, col) in enumerate((("rules", "#94a3b8"), ("model", "#0369a1"), ("blind", "#f59e0b"))):
        vals = [res[name]["per_class"][c]["f1"] for c in CLASSES]
        ax[0].bar(x + (k - 1) * w, vals, w, label={"rules": "rules", "model": "model", "blind": "model, thermal only"}[name], color=col)
    ax[0].set_xticks(x, [c.replace("_", "\n") for c in CLASSES], fontsize=8)
    ax[0].set_ylim(0, 1.05)
    ax[0].set_ylabel("F1 on held-out test units")
    ax[0].legend(fontsize=8)
    ax[0].set_title("Per-class F1")
    names = ["rules", "all features"] + list(m["ablations"])
    vals = [res["rules"]["macro_f1"], res["model"]["macro_f1"]] + [a["macro_f1"] for a in m["ablations"].values()]
    cols = ["#94a3b8", "#0369a1"] + ["#cbd5e1"] * (len(vals) - 2)
    ax[1].barh(names[::-1], vals[::-1], color=cols[::-1])
    ax[1].set_xlim(0, 1)
    ax[1].set_xlabel("macro-F1 (held-out regions and months)")
    ax[1].set_title("What the features contribute")
    for i, v in enumerate(vals[::-1]):
        ax[1].text(v + 0.01, i, f"{v:.2f}", va="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(IMG / "eval_f1_ablation.png", bbox_inches="tight")
    plt.close(fig)
    # 2: confusion matrices
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4), gridspec_kw={"width_ratios": [1, 1.35]})
    for ax_, cm, cols, title in ((axes[0], cm_model, CLASSES, "Model"), (axes[1], cm_rules, labels_all, "Rules")):
        norm = cm / np.maximum(cm.sum(axis=1, keepdims=True), 1)
        ax_.imshow(norm, cmap="Blues", vmin=0, vmax=1)
        ax_.set_xticks(range(len(cols)), [c.replace("_", "\n") for c in cols], fontsize=7)
        ax_.set_yticks(range(len(CLASSES)), [c.replace("_", "\n") for c in CLASSES], fontsize=7)
        for i in range(cm.shape[0]):
            for j in range(cm.shape[1]):
                ax_.text(j, i, int(cm[i, j]), ha="center", va="center", fontsize=8, color="white" if norm[i, j] > 0.5 else "black")
        ax_.set_title(title)
        ax_.set_xlabel("predicted")
        ax_.set_ylabel("label")
    fig.tight_layout()
    fig.savefig(IMG / "eval_confusion.png", bbox_inches="tight")
    plt.close(fig)
    # 3: industrial vs agri / vegetation, incident recall against false alarms per day
    if len(p_pos) and len(p_neg):
        fig, ax = plt.subplots(1, 2, figsize=(11.5, 4.2))
        fa = np.linspace(0.05, 3.0, 60)
        rec = []
        for f in fa:
            thr = threshold_for_false_alarms(p_neg, max(int(round(f * days)), 1))
            rec.append((p_pos >= thr).mean())
        ax[0].plot(fa, rec, color="#0369a1", lw=2, label="model")
        ru = m["incidents"]["rules"]
        ax[0].scatter([ru["false_alarms_per_day"]], [ru["recall_of_detected"] or 0], color="#dc2626", zorder=3, label="rules (one operating point)")
        ax[0].set_xlabel("false alarms per day (held-out agricultural / vegetation units flagged industrial)")
        ax[0].set_ylabel(f"share of the {len(p_pos)} detected incidents flagged")
        ax[0].set_ylim(0, 1.02)
        ax[0].legend(fontsize=8)
        ax[0].set_title("Industrial fire vs crop / forest burning")
        ax[1].hist([p_neg, p_pos], bins=np.linspace(0, 1, 21), label=["agri / vegetation test units", "verified incidents"],
                   color=["#cbd5e1", "#7c3aed"], density=True)
        ax[1].set_xlabel("model P(industrial persistent / context)")
        ax[1].legend(fontsize=8)
        ax[1].set_title("Score distributions")
        fig.tight_layout()
        fig.savefig(IMG / "eval_incidents.png", bbox_inches="tight")
        plt.close(fig)


if __name__ == "__main__":
    main()
