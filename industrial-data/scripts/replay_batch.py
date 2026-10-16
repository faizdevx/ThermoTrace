"""Replay every verified incident that has FIRMS detections through the live pipeline (no database) and tabulate
whether it came out as an industrial class with an alert. This is the honest hit rate behind the curated demo scenarios.

    PYTHONPATH=industrial-data python industrial-data/scripts/replay_batch.py   (from the repository root)
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "industrial-data"))
from src.thermal import cli, replay  # noqa: E402

OUT = ROOT / "industrial-data" / "replay" / "incident_replays.json"


def main() -> None:
    logging.basicConfig(level=logging.ERROR)
    inc = pd.read_csv(replay.INCIDENTS_CSV)
    sites = cli.load_sites()
    model, facilities = cli.load_model_assets()
    rows = []
    for r in inc[inc.verified].itertuples():
        f = replay.DATA_ROOT / "incidents" / "units" / f"{r.incident_id}.parquet"
        if not f.exists():
            rows.append({"incident_id": r.incident_id, "status": "detections not downloaded yet"})
            continue
        s = replay.incident_scenario(r.incident_id)
        det = replay.load_detections(s)
        event_window = det[(det.acq_datetime >= replay.clock_range(s)[0]) & (det.acq_datetime < replay.clock_range(s)[1])]
        if event_window.empty:
            rows.append({"incident_id": r.incident_id, "event_date": r.event_date, "facility": r.facility, "status": "no detection in the replay window"})
            continue
        rep = replay.run(s, sites=sites, model=model, facilities=facilities, det=det, progress=lambda *_: None)
        v = replay.verdict(s, rep)
        fin = (rep.get("final") or {}).get("focus") or {}
        rows.append({"incident_id": r.incident_id, "event_date": r.event_date, "facility": r.facility, "state": r.state,
                     "location_basis": r.location_basis, "status": "replayed", "n_detections": int(len(event_window)),
                     "peak_frp": float(event_window.frp.max()), "class": fin.get("class"), "model_class": fin.get("model_class"),
                     "site_kind": fin.get("site_kind"), "at_facility": fin.get("site_within"), "class_ok": v.get("class_ok"),
                     "alert": bool(rep["alerts"]), "alert_severity": rep["alerts"][0]["severity"] if rep["alerts"] else None,
                     "alert_trigger": rep["alerts"][0]["trigger"] if rep["alerts"] else None,
                     "baseline_entities": rep["baseline"]["entities"]})
        print(rows[-1]["incident_id"], rows[-1]["class"], rows[-1]["alert"], flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    done = [x for x in rows if x["status"] == "replayed"]
    print(f"replayed {len(done)}; industrial class {sum(bool(x['class_ok']) for x in done)}; alert {sum(x['alert'] for x in done)}; "
          f"both {sum(bool(x['class_ok']) and x['alert'] for x in done)}")


if __name__ == "__main__":
    main()
