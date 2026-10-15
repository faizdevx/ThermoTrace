"""Test every documented incident against NASA FIRMS (VIIRS archive).

For each incident with a usable location we ask: was there heat at that place around that date that is
ABOVE what the place normally shows?  Parameters are written next to the results (the workbook's rule 4).

event window    : [date - 1 day, date + 2 days]   (VIIRS passes ~01:30 and ~13:30 local; fires start/continue)
baseline window : [date - 60 days, date - 8 days]
search radius   : location uncertainty + 0.5 km   (VIIRS pixel 375 m + geolocation error)
status          : matched_anomalous | matched_above_routine | routine_source | no_detection | not_assessable
Honest limits   : no detection != no fire (375 m pixel, clouds, short fires between passes, small buildings);
                  a detection != the incident (see 'routine_source').
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data_labels.incident_locations import CURATED, REJECT_AUTO  # noqa: E402
from src.thermal import archive  # noqa: E402
from src.thermal.sites import haversine_m  # noqa: E402

PARAMS = {"event_days_before": 1, "event_days_after": 2, "baseline_start_days": 60, "baseline_end_days": 8,
          "radius_extra_km": 0.5, "routine_active_fraction": 0.30, "products": "VIIRS S-NPP + NOAA-20 (SP, NRT fallback)"}


def resolve_locations(geo: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in geo.to_dict("records"):
        iid = r["incident_id"]
        out = {**r, "match_lat": None, "match_lon": None, "radius_km": None, "location_basis": r["location_quality"],
               "location_note": None}
        if iid in CURATED:
            ref, lat, lon, rad, quality, why = CURATED[iid]
            out.update(match_lat=lat, match_lon=lon, radius_km=rad, location_basis=quality, osm_ref=ref, location_note=why)
        elif iid not in REJECT_AUTO and r["location_quality"] in ("industrial_area", "locality") and pd.notna(r.get("Latitude")):
            out.update(match_lat=r["Latitude"], match_lon=r["Longitude"], radius_km=r.get("uncertainty_km") or 5.0,
                       location_note="auto-geocoded, unreviewed")
        elif iid in REJECT_AUTO:
            out.update(location_basis="unlocated", location_note="auto-geocode rejected (wrong object); no curated point")
        rows.append(out)
    return pd.DataFrame(rows)


def assess(row, key: str, avail: dict) -> dict:
    d: date = pd.Timestamp(row.event_date).date()
    ev0, ev1 = d - timedelta(days=PARAMS["event_days_before"]), d + timedelta(days=PARAMS["event_days_after"])
    b0, b1 = d - timedelta(days=PARAMS["baseline_start_days"]), d - timedelta(days=PARAMS["baseline_end_days"])
    radius = float(row.radius_km) + PARAMS["radius_extra_km"]
    cov = archive.coverage_days(avail, ("SNPP", "NOAA20"), b0, ev1)
    if not any(cov.values()):
        return {"status": "not_assessable", "reason": "no VIIRS archive coverage for this date"}
    det = archive.fetch_around(key, row.match_lat, row.match_lon, b0, ev1, radius_deg=radius / 111.0 + 0.05, avail=avail)
    if len(det):
        det["dist_km"] = [haversine_m(row.match_lat, row.match_lon, a, b) / 1000 for a, b in zip(det.latitude, det.longitude)]
        det = det[det.dist_km <= radius].copy()
        det["day"] = det.acq_datetime.dt.date
    ev = det[(det.day >= ev0) & (det.day <= ev1)] if len(det) else det
    bl = det[(det.day >= b0) & (det.day <= b1)] if len(det) else det
    bl_days = (b1 - b0).days + 1
    active_frac = (bl.day.nunique() / bl_days) if len(bl) else 0.0
    bl_frp = float(bl.frp.median()) if len(bl) else 0.0
    out = {"event_detections": int(len(ev)), "event_max_frp": float(ev.frp.max()) if len(ev) else 0.0,
           "event_nearest_km": round(float(ev.dist_km.min()), 2) if len(ev) else None,
           "event_first": str(ev.acq_datetime.min()) if len(ev) else None,
           "baseline_active_fraction": round(active_frac, 3), "baseline_median_frp": round(bl_frp, 2),
           "families_covered": ",".join(k for k, v in cov.items() if v)}
    if not len(ev):
        out["status"] = "no_detection"
    elif active_frac >= PARAMS["routine_active_fraction"]:
        out["status"] = "matched_above_routine" if out["event_max_frp"] >= max(3 * bl_frp, 20) else "routine_source"
    else:
        out["status"] = "matched_anomalous"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geo", default="data/incidents/incidents_geocoded.csv")
    ap.add_argument("--out", default="data/incidents/incident_firms_match.csv")
    args = ap.parse_args()
    load_dotenv(".env")
    key = archive.key_from_env()
    avail = archive.availability(key)
    loc = resolve_locations(pd.read_csv(args.geo))
    results = []
    for row in loc.itertuples():
        base = {"incident_id": row.incident_id, "event_date": row.event_date, "facility": row.facility,
                "state": row.state, "location_basis": row.location_basis, "radius_km": row.radius_km,
                "lat": row.match_lat, "lon": row.match_lon, "location_note": row.location_note, "source_url": row.source_url}
        if row.match_lat is None or pd.isna(row.match_lat):
            results.append({**base, "status": "not_assessable", "reason": "no verified location"})
        else:
            results.append({**base, **assess(row, key, avail)})
        print(row.incident_id, results[-1]["status"], results[-1].get("event_detections", ""), results[-1].get("event_max_frp", ""))
    out = pd.DataFrame(results)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    Path(args.out).with_suffix(".params.json").write_text(json.dumps(PARAMS, indent=2))
    print(out.status.value_counts().to_dict())


if __name__ == "__main__":
    main()
