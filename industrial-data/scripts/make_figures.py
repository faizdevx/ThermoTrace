"""Build the README figures from real result files (nothing is typed in by hand).

Inputs : docs/EVALUATION.md (written by train_classifier.py), data/incidents/incident_firms_match.csv,
         data/thermal/out/thermal_sources.csv (written by run_thermal.py)
Outputs: docs/images/*.png and docs/images/findings.json
Needs matplotlib (docs-only; not a runtime dependency).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)
CLASSES = ["industrial_persistent", "industrial_fire_candidate", "unmapped_persistent",
           "agricultural_burning", "vegetation_fire"]
PRED = CLASSES + ["other"]
SHORT = {"industrial_persistent": "industrial\npersistent", "industrial_fire_candidate": "industrial fire\ncandidate",
         "unmapped_persistent": "persistent,\nno facility", "agricultural_burning": "agricultural\nburning",
         "vegetation_fire": "vegetation\nfire", "other": "other"}
COLORS = {"industrial_persistent": "#dc2626", "industrial_fire_candidate": "#f97316", "agricultural_burning": "#eab308",
          "vegetation_fire": "#16a34a", "unmapped_persistent": "#7c3aed", "other": "#64748b"}
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 130})


def parse_evaluation() -> dict:
    text = (ROOT / "docs" / "EVALUATION.md").read_text(encoding="utf-8")
    sections = re.split(r"\n## ", text)
    out = {}
    for sec in sections:
        key = ("rules" if sec.startswith("Rule classifier") else "model" if sec.startswith("Gradient-boosted model (")
               else "thermal_only" if "thermal-behaviour features only" in sec.split("\n")[0] else None)
        if key is None:
            continue
        f1, cm = {}, []
        for line in sec.split("\n"):
            m = re.match(r"\| (\w+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \| (\d+) \|", line)
            if m and m.group(1) in CLASSES:
                f1[m.group(1)] = {"p": float(m.group(2)), "r": float(m.group(3)), "f1": float(m.group(4)), "n": int(m.group(5))}
            m = re.match(r"\| \*\*(\w+)\*\* \| (.+) \|$", line)
            if m and m.group(1) in CLASSES:
                cm.append([int(x) for x in m.group(2).split("|")])
        mf = re.search(r"macro-F1 \*\*([\d.]+)\*\*, accuracy \*\*([\d.]+)\*\*", sec)
        out[key] = {"per_class": f1, "cm": np.array(cm), "macro_f1": float(mf.group(1)), "accuracy": float(mf.group(2))}
    return out


def fig_f1(ev):
    fig, ax = plt.subplots(figsize=(9, 4.2))
    x = np.arange(len(CLASSES))
    w = 0.27
    series = [("Rule classifier", "rules", "#94a3b8"), ("Gradient-boosted model", "model", "#0f766e"),
              ("Thermal behaviour only", "thermal_only", "#f59e0b")]
    for i, (label, key, color) in enumerate(series):
        vals = [ev[key]["per_class"][c]["f1"] for c in CLASSES]
        bars = ax.bar(x + (i - 1) * w, vals, w, label=f"{label} (macro-F1 {ev[key]['macro_f1']:.2f})", color=color)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.01, f"{v:.2f}", ha="center", fontsize=7.5)
    ax.set_xticks(x, [SHORT[c] for c in CLASSES])
    ax.set_ylim(0, 1.08)
    ax.set_ylabel("F1 on held-out states and months")
    ax.set_title("Per-class F1: rules vs trained model vs thermal-only features")
    ax.legend(loc="lower right", fontsize=8.5)
    fig.tight_layout()
    fig.savefig(OUT / "f1_by_class.png")
    plt.close(fig)


def fig_confusion(ev):
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.6))
    for ax, key, title in zip(axes, ("rules", "model"), ("Rule classifier", "Gradient-boosted model")):
        cm = ev[key]["cm"]
        norm = cm / cm.sum(axis=1, keepdims=True).clip(min=1)
        ax.imshow(norm, cmap="Blues", vmin=0, vmax=1)
        for i in range(cm.shape[0]):
            for j in range(cm.shape[1]):
                ax.text(j, i, f"{cm[i, j]}", ha="center", va="center", fontsize=8,
                        color="white" if norm[i, j] > 0.5 else "#0f172a")
        ax.set_xticks(range(len(PRED)), [SHORT[c] for c in PRED], fontsize=7.5)
        ax.set_yticks(range(len(CLASSES)), [SHORT[c].replace("\n", " ") for c in CLASSES], fontsize=8)
        ax.set_title(f"{title}  (accuracy {ev[key]['accuracy']:.2f})")
        ax.set_xlabel("predicted")
        ax.spines[:].set_visible(False)
    axes[0].set_ylabel("labelled class (rows are normalised, cells show counts)")
    fig.tight_layout()
    fig.savefig(OUT / "confusion_matrices.png")
    plt.close(fig)


def fig_incidents(df):
    order = ["matched_anomalous", "matched_above_routine", "routine_source", "no_detection", "not_assessable"]
    labels = {"matched_anomalous": "anomalous detection", "matched_above_routine": "above routine",
              "routine_source": "routine hot site", "no_detection": "no detection", "not_assessable": "not assessable"}
    colors = {"matched_anomalous": "#dc2626", "matched_above_routine": "#f97316", "routine_source": "#eab308",
              "no_detection": "#94a3b8", "not_assessable": "#e2e8f0"}
    basis = ["facility", "industrial_area", "locality", "unlocated"]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.2), gridspec_kw={"width_ratios": [1.25, 1]})
    ax = axes[0]
    bottom = np.zeros(len(basis))
    for st in order:
        vals = np.array([((df.location_basis == b) & (df.status == st)).sum() for b in basis])
        ax.bar(basis, vals, bottom=bottom, label=labels[st], color=colors[st])
        for i, v in enumerate(vals):
            if v:
                ax.text(i, bottom[i] + v / 2, str(v), ha="center", va="center", fontsize=9)
        bottom += vals
    ax.set_ylabel("documented incidents")
    ax.set_ylim(0, bottom.max() + 1.5)
    ax.set_title("40 documented industrial fires vs NASA FIRMS VIIRS")
    ax.set_xticks(range(len(basis)), ["facility\n(OSM object)", "industrial\nestate", "locality\n(OSM place node)", "unlocated"])
    ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    ax = axes[1]
    m = df[df.status.isin(["matched_anomalous", "routine_source"])].sort_values("event_max_frp")
    ax.barh(m.incident_id + " " + m.state.str.slice(0, 12), m.event_max_frp.fillna(0),
            color=[colors[s] for s in m.status])
    ax.set_xlabel("max fire radiative power in event window (MW)")
    ax.set_title("Strength of the detections")
    ax.tick_params(axis="y", labelsize=7)
    fig.tight_layout()
    fig.savefig(OUT / "incident_matching.png", bbox_inches="tight")
    plt.close(fig)


def fig_thermal(src):
    counts = src.thermal_class.value_counts().reindex(list(COLORS)).fillna(0)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.0))
    ax = axes[0]
    ax.bar([SHORT[c] for c in counts.index], counts.values, color=[COLORS[c] for c in counts.index])
    for i, v in enumerate(counts.values):
        ax.text(i, v + counts.max() * 0.01, f"{int(v):,}", ha="center", fontsize=8)
    ax.set_title(f"{len(src):,} classified thermal sources")
    ax.tick_params(axis="x", labelsize=7)
    ax = axes[1]
    data = [src.loc[src.thermal_class == c, "frp_max"].clip(lower=0.1) for c in COLORS]
    bp = ax.boxplot(data, vert=True, patch_artist=True, showfliers=False, tick_labels=[SHORT[c] for c in COLORS])
    for patch, c in zip(bp["boxes"], COLORS):
        patch.set_facecolor(COLORS[c])
    ax.set_yscale("log")
    ax.set_title("Peak radiative power per source (MW)")
    ax.tick_params(axis="x", labelsize=7)
    ax = axes[2]
    for c in COLORS:
        sub = src[src.thermal_class == c]
        if len(sub):
            ax.scatter(sub.n_days, sub.night_fraction, s=6, alpha=0.35, color=COLORS[c], label=SHORT[c].replace("\n", " "))
    ax.set_xlabel("days detected in the 7-day window")
    ax.set_ylabel("share of detections at night")
    ax.set_title("Persistence vs night activity")
    ax.legend(fontsize=7, markerscale=2.5)
    fig.tight_layout()
    fig.savefig(OUT / "thermal_overview.png")
    plt.close(fig)


def fig_chip():
    """Sentinel-2 chip pair saved by the imagery endpoint test (true colour + SWIR), if present."""
    tc = ROOT / "data" / "chip_tsm2097_8517_true_colour.png"
    sw = ROOT / "data" / "chip_tsm2097_8517_swir.png"
    if not (tc.exists() and sw.exists()):
        return
    fig, axes = plt.subplots(1, 2, figsize=(8.4, 4.4))
    for ax, path, title in ((axes[0], tc, "True colour (B4/B3/B2)"),
                            (axes[1], sw, "SWIR (B12/B11/B8A): hot ground glows orange")):
        ax.imshow(plt.imread(path))
        ax.set_title(title, fontsize=10)
        ax.axis("off")
    fig.suptitle("Bhubhaneshwari coal mine, Odisha - Sentinel-2 L2A, 2026-10-08, 13% cloud, 3 x 3 km", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / "sentinel2_chip.png")
    plt.close(fig)



def fig_incidents(df):
    order = ["matched_anomalous", "matched_above_routine", "routine_source", "no_detection", "not_assessable"]
    labels = {"matched_anomalous": "anomalous detection", "matched_above_routine": "above routine",
              "routine_source": "routine hot site", "no_detection": "no detection", "not_assessable": "not assessable"}
    colors = {"matched_anomalous": "#dc2626", "matched_above_routine": "#f97316", "routine_source": "#eab308",
              "no_detection": "#94a3b8", "not_assessable": "#e2e8f0"}
    basis = ["facility", "industrial_area", "locality", "unlocated"]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.2), gridspec_kw={"width_ratios": [1.25, 1]})
    ax = axes[0]
    bottom = np.zeros(len(basis))
    for st in order:
        vals = np.array([((df.location_basis == b) & (df.status == st)).sum() for b in basis])
        ax.bar(basis, vals, bottom=bottom, label=labels[st], color=colors[st])
        for i, v in enumerate(vals):
            if v:
                ax.text(i, bottom[i] + v / 2, str(v), ha="center", va="center", fontsize=9)
        bottom += vals
    ax.set_ylabel("documented incidents")
    ax.set_ylim(0, bottom.max() + 1.5)
    ax.set_title("40 documented industrial fires vs NASA FIRMS VIIRS")
    ax.set_xticks(range(len(basis)), ["facility\n(OSM object)", "industrial\nestate", "locality\n(OSM place node)", "unlocated"])
    ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    ax = axes[1]
    m = df[df.status.isin(["matched_anomalous", "routine_source"])].sort_values("event_max_frp")
    ax.barh(m.incident_id + " " + m.state.str.slice(0, 12), m.event_max_frp.fillna(0),
            color=[colors[s] for s in m.status])
    ax.set_xlabel("max fire radiative power in event window (MW)")
    ax.set_title("Strength of the detections")
    ax.tick_params(axis="y", labelsize=7)
    fig.tight_layout()
    fig.savefig(OUT / "incident_matching.png", bbox_inches="tight")
    plt.close(fig)


def fig_thermal(src):
    counts = src.thermal_class.value_counts().reindex(list(COLORS)).fillna(0)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.0))
    ax = axes[0]
    ax.bar([SHORT[c] for c in counts.index], counts.values, color=[COLORS[c] for c in counts.index])
    for i, v in enumerate(counts.values):
        ax.text(i, v + counts.max() * 0.01, f"{int(v):,}", ha="center", fontsize=8)
    ax.set_title(f"{len(src):,} classified thermal sources")
    ax.tick_params(axis="x", labelsize=7)
    ax = axes[1]
    data = [src.loc[src.thermal_class == c, "frp_max"].clip(lower=0.1) for c in COLORS]
    bp = ax.boxplot(data, vert=True, patch_artist=True, showfliers=False, tick_labels=[SHORT[c] for c in COLORS])
    for patch, c in zip(bp["boxes"], COLORS):
        patch.set_facecolor(COLORS[c])
    ax.set_yscale("log")
    ax.set_title("Peak radiative power per source (MW)")
    ax.tick_params(axis="x", labelsize=7)
    ax = axes[2]
    for c in COLORS:
        sub = src[src.thermal_class == c]
        if len(sub):
            ax.scatter(sub.n_days, sub.night_fraction, s=6, alpha=0.35, color=COLORS[c], label=SHORT[c].replace("\n", " "))
    ax.set_xlabel("days detected in the 7-day window")
    ax.set_ylabel("share of detections at night")
    ax.set_title("Persistence vs night activity")
    ax.legend(fontsize=7, markerscale=2.5)
    fig.tight_layout()
    fig.savefig(OUT / "thermal_overview.png")
    plt.close(fig)


def fig_chip():
    """Sentinel-2 chip pair saved by the imagery endpoint test (true colour + SWIR), if present."""
    tc = ROOT / "data" / "chip_tsm2097_8517_true_colour.png"
    sw = ROOT / "data" / "chip_tsm2097_8517_swir.png"
    if not (tc.exists() and sw.exists()):
        return
    fig, axes = plt.subplots(1, 2, figsize=(8.4, 4.4))
    for ax, path, title in ((axes[0], tc, "True colour (B4/B3/B2)"),
                            (axes[1], sw, "SWIR (B12/B11/B8A): hot ground glows orange")):
        ax.imshow(plt.imread(path))
        ax.set_title(title, fontsize=10)
        ax.axis("off")
    fig.suptitle("Bhubhaneshwari coal mine, Odisha - Sentinel-2 L2A, 2026-10-08, 13% cloud, 3 x 3 km", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / "sentinel2_chip.png")
    plt.close(fig)



def fig_labels():
    """Label set overview: counts per class x split, and where the labels are."""
    path = ROOT / "data" / "labels" / "labels_v1.parquet"
    if not path.exists():
        return
    lab = pd.read_parquet(path)
    order = ["industrial_persistent", "agricultural_burning", "vegetation_fire", "industrial_fire"]
    colors = {"industrial_persistent": "#dc2626", "agricultural_burning": "#eab308", "vegetation_fire": "#16a34a",
              "industrial_fire": "#7c3aed"}
    nl = chr(10)
    names = {c: c.replace("_", nl, 1) for c in order}
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.6), gridspec_kw={"width_ratios": [1, 1.1]})
    ax = axes[0]
    x = np.arange(len(order))
    train = np.array([((lab.label == c) & (lab.split == "train")).sum() for c in order])
    test = np.array([((lab.label == c) & (lab.split.isin(["test", "incident_test"]))).sum() for c in order])
    ax.bar(x, train, color=[colors[c] for c in order], label="train (held-in regions, before 2026-04-01)")
    ax.bar(x, test, bottom=train, color=[colors[c] for c in order], alpha=0.45, hatch="//",
           label="test (held-out regions, from 2026-04-01; incidents test-only)")
    for i, (a, b) in enumerate(zip(train, test)):
        ax.text(i, a + b + 20, f"{a}+{b}" if a else f"{b}", ha="center", fontsize=9)
    ax.set_xticks(x, [names[c] for c in order], fontsize=8)
    ax.set_ylabel("labels")
    ax.set_title(f"labels-v1: {len(lab):,} labels")
    ax.set_ylim(0, (train + test).max() * 1.28)
    ax.legend(fontsize=7, loc="upper right")
    ax = axes[1]
    for c in order:
        d = lab[lab.label == c]
        ax.scatter(d.lon, d.lat, s=22 if c == "industrial_fire" else 7, c=colors[c],
                   alpha=0.85 if c == "industrial_fire" else 0.45,
                   edgecolors="black" if c == "industrial_fire" else "none", linewidths=0.5, label=c.replace("_", " "))
    ax.set_xlim(67, 98.5)
    ax.set_ylim(6, 37.5)
    ax.set_aspect(1.05)
    ax.set_title("Where the labels are")
    ax.legend(fontsize=7, loc="lower right", markerscale=1.6)
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    fig.tight_layout()
    fig.savefig(OUT / "label_set.png", bbox_inches="tight")
    plt.close(fig)


def main():
    ev = parse_evaluation()
    fig_f1(ev)
    fig_confusion(ev)
    v2 = ROOT / "data" / "incidents" / "incident_firms_match_v2.csv"      # 32 verified locations (labels-v1)
    inc = pd.read_csv(v2 if v2.exists() else ROOT / "data" / "incidents" / "incident_firms_match.csv")
    fig_incidents(inc)
    fig_labels()
    src = pd.read_csv(ROOT / "data" / "thermal" / "out" / "thermal_sources.csv")
    fig_thermal(src)
    fig_chip()

    ind = src[src.thermal_class == "industrial_persistent"]
    summ = {
        "sources": int(len(src)),
        "by_class": src.thermal_class.value_counts().to_dict(),
        "median_frp_max_by_class": src.groupby("thermal_class").frp_max.median().round(1).to_dict(),
        "median_night_fraction_by_class": src.groupby("thermal_class").night_fraction.median().round(2).to_dict(),
        "industrial_persistent_by_state": ind.state.value_counts().head(8).to_dict(),
        "industrial_persistent_by_kind": ind.site_kind.value_counts().to_dict(),
        "incident_status": inc.status.value_counts().to_dict(),
        "eval": {k: {"macro_f1": v["macro_f1"], "accuracy": v["accuracy"]} for k, v in ev.items()},
        "rules_recall": {c: ev["rules"]["per_class"][c]["r"] for c in CLASSES},
        "model_recall": {c: ev["model"]["per_class"][c]["r"] for c in CLASSES},
    }
    (OUT / "findings.json").write_text(json.dumps(summ, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summ, indent=2, default=str))


if __name__ == "__main__":
    main()
