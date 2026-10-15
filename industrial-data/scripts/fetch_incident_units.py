"""Pull VIIRS detections around every verified incident: 365 baseline days (ending 14 days before) + the event window.

For each incident in data_labels/incidents_verified.csv this stores ALL detections inside radius_km + 0.5 km for
[event - 378 d, event + 2 d] in data/incidents/units/<id>.parquet, so the model features (event-window behaviour,
365-day baseline of the place) are computed exactly as for live sources. Resumable; needs FIRMS_MAP_KEY.
Run from the repo root:  python industrial-data/scripts/fetch_incident_units.py
"""
from __future__ import annotations

import sys
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "industrial-data"))
from src.thermal import archive  # noqa: E402

OUT = ROOT / "data" / "incidents" / "units"


def main() -> None:
    load_dotenv(ROOT / ".env")
    key = archive.key_from_env()
    avail = archive.availability(key)
    inc = pd.read_csv(ROOT / "industrial-data" / "data_labels" / "incidents_verified.csv")
    inc = inc[inc.verified.astype(str).str.lower() == "true"]
    OUT.mkdir(parents=True, exist_ok=True)
    order = inc.iloc[::-1] if "--reverse" in sys.argv else inc      # a second process can work from the other end
    for r in order.itertuples():
        target = OUT / f"{r.incident_id}.parquet"
        if target.exists() or (OUT / f"{r.incident_id}.lock").exists():
            continue
        (OUT / f"{r.incident_id}.lock").write_text("working")
        d = pd.Timestamp(r.event_date).date()
        start, end = d - timedelta(days=378), d + timedelta(days=2)
        radius_km = float(r.radius_km) + 0.5
        t = time.time()
        det = archive.fetch_around(key, r.lat, r.lon, start, end, radius_deg=radius_km / 111.0 + 0.02, avail=avail)
        det.to_parquet(target, index=False)
        (OUT / f"{r.incident_id}.lock").unlink(missing_ok=True)
        print(r.incident_id, d, f"{len(det)} detections", f"{time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    main()
