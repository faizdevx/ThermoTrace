"""Dashboard summary: model card from metrics, snapshot fallback, and the response shape."""
from __future__ import annotations

from backend.app.services import dashboard as dash

METRICS = {"model_version": "m1", "results": {"model": {"macro_f1": 0.99}, "rules": {"macro_f1": 0.81}, "blind": {"macro_f1": 0.63},
           "bootstrap": {"ci": {"model": [0.98, 1.0], "rules": [0.76, 0.85], "blind": [0.59, 0.67]}}},
           "split": {"test": 1021}, "incidents": {"n_incidents": 34, "n_with_detection": 20, "rules": {"recall_of_detected": 0.8},
           "operating_points": [{"false_alarms_per_day": 0.25, "recall_of_detected": 1.0}]}}


def test_model_card_keeps_the_headline_numbers_and_the_caveat():
    c = dash.model_card(METRICS)
    assert c["macro_f1"] == {"model": 0.99, "rules": 0.81, "blind": 0.63} and c["macro_f1_ci"]["rules"] == [0.76, 0.85]
    assert c["incidents"]["with_detection"] == 20 and c["incidents"]["model_recall_of_detected_at_0.25_false_alarms_per_day"] == 1.0
    assert "rule-derived" in c["caveat"] and dash.model_card(None) is None


def test_downsample_keeps_first_and_last():
    rows = [{"i": i} for i in range(200)]
    out = dash._downsample(rows, 10)
    assert len(out) == 10 and out[0]["i"] == 0 and out[-1]["i"] == 199 and dash._downsample(rows[:5], 10) == rows[:5]


def _f(cls, state, alert=None):
    return {"properties": {"thermal_class": cls, "state": state, "alert_level": alert, "n_detections": 2, "last_seen": "2026-10-16T00:00:00+00:00"}}


def test_snapshot_fallback_counts_classes_and_states():
    out = dash.from_features([_f("industrial_persistent", "Odisha"), _f("agricultural_burning", "Punjab"), _f("agricultural_burning", "Punjab"),
                              _f("industrial_fire_candidate", None, "high")], METRICS)
    assert out["by_class"]["agricultural_burning"] == 2 and out["totals"]["sources"] == 4 and out["totals"]["detections"] == 8
    top = {s["state"]: s for s in out["top_states"]}
    assert top["Punjab"]["sources"] == 2 and top["Odisha"]["industrial"] == 1 and top["Unknown"]["flagged"] == 1
    assert out["alerts"]["today"] == 0 and out["class_mix"] == [] and out["model"]["model_version"] == "m1"
