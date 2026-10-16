"""Replay: virtual clock, availability latency, baseline made only from the past, alert dedupe and the verdict."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from src.thermal import pipeline, replay


def _det(rows) -> pd.DataFrame:
    """rows: (utc time, lat, lon, frp, 'D'/'N')."""
    df = pd.DataFrame([{"acq_datetime": pd.Timestamp(t, tz="UTC"), "latitude": la, "longitude": lo, "frp": f, "daynight": dn,
                        "bright_ti4": 330.0, "bright_ti5": 290.0, "confidence": "nominal", "instrument": "VIIRS_SNPP_SP",
                        "scan": 0.4, "track": 0.4} for t, la, lo, f, dn in rows])
    return replay._with_ids(df)


@pytest.fixture(autouse=True)
def _no_boundary_download(monkeypatch):
    monkeypatch.setattr(pipeline, "assign_state", lambda src, cache_dir: pd.Series([None] * len(src)))


def _scn(**over) -> replay.Scenario:
    base = dict(id="t", title="t", start="2026-03-10", end="2026-03-11", source="archive", focus=(20.0, 80.0, 2.0))
    base.update(over)
    return replay.Scenario(**base)


def test_speed_parsing():
    assert replay.parse_speed("60x") == 60.0 and replay.parse_speed("600") == 600.0
    assert replay.parse_speed("max") == float("inf") and replay.parse_speed("MAX") == float("inf") and replay.parse_speed(None) == 60.0


def test_scenarios_file_is_valid_and_incident_focus_comes_from_the_incident_table():
    sc = replay.load_scenarios()
    assert {"vatva-gidc", "nahorkatiya-flare", "punjab-burning-day"} <= set(sc)
    v = sc["vatva-gidc"]
    assert v.source == "incident" and v.incident_id == "IN-024" and v.focus is not None and 22 < v.focus[0] < 24
    assert sc["punjab-burning-day"].bbox == (73.8, 29.5, 77.0, 32.6)
    assert replay.clock_range(v)[1] - replay.clock_range(v)[0] == pd.Timedelta(days=5)


def test_detections_appear_only_after_the_satellite_latency_and_pace_follows_the_virtual_clock():
    det = _det([("2026-03-10 05:00", 20.0, 80.0, 30.0, "D"), ("2026-03-10 20:00", 20.0, 80.001, 12.0, "N")])
    slept: list[float] = []
    rep = replay.run(_scn(), det=det, speed=3600.0, step_minutes=60, latency_hours=3.0, use_landcover=False, progress=lambda *_: None,
                     sleep=slept.append, cache_dir="/nonexistent")
    first, second = rep["ticks"][0], rep["ticks"][1]
    assert first["clock"].startswith("2026-03-10T08:00") and second["clock"].startswith("2026-03-10T23:00")   # acq + 3 h, on the hour
    assert first["new_detections"] == 1 and second["new_detections"] == 1
    assert slept and all(s > 0 for s in slept)                                      # 3600x: an hour of virtual time = 1 s of waiting
    assert rep["final"]["focus"]["n_detections"] == 2


def test_baseline_uses_only_history_before_the_start():
    hist = [(f"2026-02-{d:02d} 20:00", 20.0, 80.0, 5.0, "N") for d in range(1, 27)]
    det = _det(hist + [("2026-03-10 20:00", 20.0, 80.0, 40.0, "N")])
    rep = replay.run(_scn(), det=det, use_landcover=False, progress=lambda *_: None, cache_dir="/nonexistent")
    assert rep["baseline"]["history_detections"] == 26
    z = rep["focus_log"][-1]["frp_z"]
    assert z is not None and z > 3                      # 40 MW against a facility-less cell that was quiet at ~5 MW


def test_new_episodic_source_alerts_once_per_window_without_a_database():
    # night-time heat, day 1 only, 45 MW: episodic -> fire candidate; the second pass 2 h later is deduplicated
    det = _det([("2026-03-10 20:00", 20.0, 80.0, 45.0, "N"), ("2026-03-10 21:30", 20.0, 80.0005, 40.0, "N"),
                ("2026-03-10 23:40", 20.0, 80.0, 38.0, "N")])
    rep = replay.run(_scn(), det=det, use_landcover=False, progress=lambda *_: None, cache_dir="/nonexistent")
    classes = {x["class"] for x in rep["focus_log"]}
    assert rep["alerts"] == [] or len(rep["alerts"]) == 1       # never more than one alert for one source inside the 6 h window
    if rep["alerts"]:
        assert rep["alerts"][0]["severity"] in ("watch", "high", "critical") and classes


def test_verdict_judges_class_at_the_first_alert_and_the_silence_of_quiet_scenarios():
    s = _scn(expect={"classes": ["industrial_fire_candidate"], "alert": True})
    ok = {"focus_log": [{"clock": "2026-03-10T08:00:00+00:00", "class": "other"}, {"clock": "2026-03-10T10:00:00+00:00", "class": "industrial_fire_candidate"},
                        {"clock": "2026-03-11T10:00:00+00:00", "class": "industrial_persistent"}],
          "alerts": [{"at": "2026-03-10T10:00:00+00:00", "lat": 20.0, "lon": 80.0}]}
    v = replay.verdict(s, ok)
    assert v["class_shown"] == "industrial_fire_candidate" and v["pass"]            # the class the analyst saw when the alert fired
    miss = replay.verdict(s, {"focus_log": ok["focus_log"][:1], "alerts": []})
    assert not miss["pass"] and miss["alert_ok"] is False
    quiet = _scn(expect={"classes": ["industrial_persistent"], "alert": False, "max_alerts": 0})
    assert replay.verdict(quiet, {"focus_log": [{"clock": "x", "class": "industrial_persistent"}], "alerts": []})["pass"]
    assert not replay.verdict(quiet, {"focus_log": [{"clock": "x", "class": "industrial_persistent"}],
                                      "alerts": [{"at": "x", "lat": 20.0, "lon": 80.0}]})["pass"]
    area = _scn(focus=None, bbox=(0, 0, 1, 1), expect={"dominant_class": "agricultural_burning", "max_alerts": 0})
    assert replay.verdict(area, {"final": {"by_class": {"agricultural_burning": 90, "other": 10}}, "alerts": []})["pass"]
    assert not replay.verdict(area, {"final": {"by_class": {"agricultural_burning": 40, "other": 60}}, "alerts": []})["pass"]


def test_reset_refuses_a_database_that_is_not_a_replay_database():
    with pytest.raises(RuntimeError, match="refusing"):
        replay.reset_db("postgresql://u:p@localhost:5432/thermotrace")
    assert replay.replay_dsn("postgresql+psycopg2://u:p@db:5432/thermotrace") == "postgresql://u:p@db:5432/thermotrace_replay"


def test_cli_lists_scenarios(capsys):
    from src.thermal import cli
    assert cli.main(["replay", "--list"]) == 0
    out = capsys.readouterr().out
    assert "vatva-gidc" in out and "punjab-burning-day" in out
