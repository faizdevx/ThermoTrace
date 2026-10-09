"""One-off baseline: OSM facilities that can emit persistent thermal signatures, all of India.

Runs ONE bounded Overpass query (not per-refresh) and caches the result as GeoJSON points.
In the running application the same facilities come from PostGIS (``industrial_sites``);
this file lets the thermal classifier run standalone without a database.

Data (c) OpenStreetMap contributors, ODbL 1.0. Please respect the Overpass usage policy:
https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
UA = "ThermoTrace-thermal/1.0 (+https://github.com/faizdevx/ThermoTrace)"

QUERY = """
[out:json][timeout:600][maxsize:1073741824];
area["ISO3166-1"="IN"][admin_level=2]->.in;
(
  nwr(area.in)["power"="plant"];
  nwr(area.in)["power"="generator"]["generator:source"~"^(coal|gas|oil|diesel|biomass|nuclear|waste)"];
  nwr(area.in)["man_made"="works"];
  nwr(area.in)["man_made"="flare"];
  nwr(area.in)["industrial"~"^(refinery|oil|gas|petroleum|chemical|steel|metal|metal_processing|cement|smelter|foundry|mine|mining|quarry|brickworks|fertilizer|sugar|power|factory)$"];
  nwr(area.in)["landuse"="quarry"];
  nwr(area.in)["landuse"="landfill"];
);
out center tags;
"""


def fetch(retries: int = 3) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        for url in ENDPOINTS:
            try:
                print(f"POST {url} (attempt {attempt + 1})", file=sys.stderr)
                r = requests.post(url, data={"data": QUERY}, headers={"User-Agent": UA}, timeout=900)
                r.raise_for_status()
                return r.json()
            except Exception as exc:  # noqa: BLE001
                last = exc
                print(f"  failed: {exc}", file=sys.stderr)
        time.sleep(30 * (attempt + 1))
    raise SystemExit(f"Overpass unavailable: {last}")


def to_geojson(payload: dict) -> dict:
    features = []
    for el in payload.get("elements", []):
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"source_ref": f"{el['type']}/{el['id']}", "name": tags.get("name"), "tags": tags},
        })
    return {
        "type": "FeatureCollection",
        "metadata": {
            "source": "OpenStreetMap via Overpass API", "license": "ODbL 1.0",
            "osm_base": payload.get("osm3s", {}).get("timestamp_osm_base"),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "count": len(features),
        },
        "features": features,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/thermal/sites_india.geojson")
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    gj = to_geojson(fetch())
    out.write_text(json.dumps(gj), encoding="utf-8")
    print(f"wrote {gj['metadata']['count']} facilities -> {out}")
