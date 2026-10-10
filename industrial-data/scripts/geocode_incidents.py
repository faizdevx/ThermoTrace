"""Geocode the documented incident list with OpenStreetMap Nominatim, with honest quality tiers.

The workbook leaves coordinates blank on purpose ("do not use a city centroid as a facility
coordinate"). This script therefore records HOW each point was obtained:

  facility         an OSM object that is itself an industrial feature (refinery, works, plant, ...)
  industrial_area  an OSM industrial estate / landuse=industrial polygon (GIDC, MIDC, ...) - centroid + extent
  locality         only a town / suburb was found - centroid, NOT a facility point (kept, never used for strict tests)
  unlocated        nothing usable (e.g. "see PNGRB register")

Results are auto-geocoded candidates and MUST be reviewed; the CSV keeps the OSM object link and the query.
Nominatim policy: <= 1 request/second, identifying User-Agent.  https://operations.osmfoundation.org/policies/nominatim/
"""
from __future__ import annotations

import argparse
import math
import re
import time
from pathlib import Path

import pandas as pd
import requests

UA = "ThermoTrace-validation/1.0 (+https://github.com/faizdevx/ThermoTrace)"
URL = "https://nominatim.openstreetmap.org/search"
INDUSTRIAL_TYPES = {"industrial", "works", "refinery", "factory", "plant", "chemical", "storage_tank", "petroleum_well",
                    "oil", "gas", "warehouse", "manufacture", "wastewater_plant", "generator"}
AREA_WORDS = re.compile(r"\b(GIDC|MIDC|industrial (area|estate|belt)|SEZ|phase)\b", re.I)
SKIP = re.compile(r"see (linked|pngrb)|^india;", re.I)


def clean(name: str) -> str:
    name = re.sub(r"\(.*?\)", " ", name)
    name = re.sub(r"\b(Pvt\.?|Private|Limited|Ltd\.?|Company|Corporation|CPCL)\b", " ", name, flags=re.I)
    name = re.sub(r"\b(Unit|AVU-?\d*|FCCU|MSQ|ETP|Tank \d+|DYIP.*|Hydrocracker.*|Swing Unit|tank farm)\b.*", " ", name, flags=re.I)
    return re.sub(r"\s+", " ", name.replace("/", " ")).strip(" ,.-")


def queries(row) -> list[tuple[str, str]]:
    fac, loc, state = str(row["Facility / Company"]), str(row["Locality"]), str(row["State / UT"])
    short_fac = clean(fac)
    area = loc.split(",")[0].strip()
    qs = []
    if "name not reported" not in fac.lower() and short_fac:
        qs.append(("facility", f"{short_fac}, {area}, {state}"))
        qs.append(("facility", f"{short_fac}, {state}"))
    for part in loc.split(","):
        if AREA_WORDS.search(part):
            qs.append(("industrial_area", f"{part.strip()}, {area}, {state}" if part.strip() != area else f"{part.strip()}, {state}"))
    qs.append(("industrial_area", f"{area} industrial area, {state}"))
    qs.append(("locality", f"{area}, {state}"))
    return qs


def classify(hit: dict, intent: str) -> str:
    cls, typ = hit.get("class", ""), hit.get("type", "")
    if typ in INDUSTRIAL_TYPES or cls in ("man_made", "power") and typ in INDUSTRIAL_TYPES:
        return "facility" if intent == "facility" else "industrial_area"
    if cls == "landuse" and typ == "industrial":
        return "industrial_area"
    if cls == "building" and typ in ("industrial", "warehouse", "factory"):
        return "facility"
    return "locality"


def extent_km(hit: dict) -> float:
    s, n, w, e = (float(x) for x in hit["boundingbox"])
    lat = (s + n) / 2
    dy, dx = (n - s) * 111.32, (e - w) * 111.32 * math.cos(math.radians(lat))
    return round(min(max(math.hypot(dx, dy) / 2, 0.3), 8.0), 2)


def geocode(row, session: requests.Session) -> dict:
    loc = str(row["Locality"])
    if SKIP.search(loc):
        return {"location_quality": "unlocated", "geocode_note": "location only given by external register"}
    best = None
    for intent, q in queries(row):
        time.sleep(1.1)
        r = session.get(URL, params={"q": q, "format": "jsonv2", "limit": 5, "countrycodes": "in", "extratags": 1},
                        headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        for hit in r.json():
            quality = classify(hit, intent)
            rank = {"facility": 3, "industrial_area": 2, "locality": 1}[quality]
            # a locality hit may only be accepted from the locality query itself
            if quality == "locality" and intent != "locality":
                continue
            if best is None or rank > best["rank"]:
                best = {"rank": rank, "quality": quality, "hit": hit, "query": q}
        if best and best["rank"] >= 3:
            break
    if not best:
        return {"location_quality": "unlocated", "geocode_note": "no Nominatim result"}
    h = best["hit"]
    radius = extent_km(h) if best["quality"] != "locality" else 5.0
    return {"location_quality": best["quality"], "Latitude": float(h["lat"]), "Longitude": float(h["lon"]),
            "uncertainty_km": radius, "osm_ref": f"{h.get('osm_type')}/{h.get('osm_id')}",
            "geocode_match": h.get("display_name", "")[:160], "geocode_query": best["query"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default=r"D:\india_industrial_fire_incidents_40.xlsx")
    ap.add_argument("--out", default="data/incidents/incidents_geocoded.csv")
    args = ap.parse_args()
    df = pd.read_excel(args.xlsx, sheet_name="India Incidents")
    s = requests.Session()
    rows = []
    for _, r in df.iterrows():
        g = geocode(r, s)
        rows.append({"incident_id": r["Incident ID"], "event_date": r["Event Date"].date(), "facility": r["Facility / Company"],
                     "locality": r["Locality"], "state": r["State / UT"], "industry": r["Industry"],
                     "incident_type": r["Incident Type"], "source_url": r["Source URL"], **g})
        print(r["Incident ID"], g.get("location_quality"), g.get("geocode_match", "")[:70])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False)
    print(pd.DataFrame(rows).location_quality.value_counts().to_dict())


if __name__ == "__main__":
    main()
