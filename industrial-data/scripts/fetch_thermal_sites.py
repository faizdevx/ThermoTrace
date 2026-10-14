"""One-off baseline: OSM facilities that can emit persistent thermal signatures, all of India.

Runs bounded Overpass queries (not per-refresh) and caches the result as GeoJSON points:
  * every facility as its centre point, with its bounding box (``bounds``) for an extent-based radius;
  * footprint polygons (``footprint``) for the high-value kinds where OSM maps an area: power plants,
    refineries / oil & gas, steel / metal, cement, chemical / fertilizer. Mines, landfills and generic works
    keep bounding boxes only (thousands of large polygons would bloat the cache for little gain).

    python scripts/fetch_thermal_sites.py --out data/thermal/sites_india.geojson
    python scripts/fetch_thermal_sites.py --out data/thermal/sites_india.geojson --footprints-only

``--footprints-only`` adds polygons to an existing cache with one small query instead of refetching all.
The facility merge (``python -m src.facilities.cli build``) reads this file as its OSM source.
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
from shapely.geometry import LineString, Polygon, mapping
from shapely.ops import polygonize, unary_union

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
UA = "ThermoTrace-thermal/1.0 (+https://github.com/faizdevx/ThermoTrace)"
AREA = 'area["ISO3166-1"="IN"][admin_level=2]->.in;'
QUERY = f"""
[out:json][timeout:600][maxsize:1073741824];
{AREA}
(
  nwr(area.in)["power"="plant"];
  nwr(area.in)["power"="generator"]["generator:source"~"^(coal|gas|oil|diesel|biomass|nuclear|waste)"];
  nwr(area.in)["man_made"="works"];
  nwr(area.in)["man_made"="flare"];
  nwr(area.in)["industrial"~"^(refinery|oil|gas|petroleum|chemical|steel|metal|metal_processing|cement|smelter|foundry|mine|mining|quarry|brickworks|fertilizer|sugar|power|factory)$"];
  nwr(area.in)["landuse"="quarry"];
  nwr(area.in)["landuse"="landfill"];
);
out center bb tags;
"""
FOOTPRINT_QUERY = f"""
[out:json][timeout:600][maxsize:536870912];
{AREA}
(
  wr(area.in)["power"="plant"];
  wr(area.in)["industrial"~"^(refinery|oil|gas|petroleum|chemical|steel|metal|metal_processing|cement|smelter|foundry|fertilizer)$"];
);
out geom tags;
"""


def fetch(query: str = QUERY, retries: int = 3) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        for url in ENDPOINTS:
            try:
                print(f"POST {url} (attempt {attempt + 1})", file=sys.stderr)
                r = requests.post(url, data={"data": query}, headers={"User-Agent": UA}, timeout=900)
                r.raise_for_status()
                return r.json()
            except Exception as exc:  # noqa: BLE001
                last = exc
                print(f"  failed: {exc}", file=sys.stderr)
        time.sleep(30 * (attempt + 1))
    raise SystemExit(f"Overpass unavailable: {last}")


def element_polygon(el: dict):
    """Polygon of a closed way or a multipolygon relation (outer rings), or None."""
    if el["type"] == "way":
        pts = [(p["lon"], p["lat"]) for p in el.get("geometry", []) if p]
        if len(pts) >= 4 and pts[0] == pts[-1]:
            poly = Polygon(pts)
            if not poly.is_valid:
                poly = poly.buffer(0)
            return poly if not poly.is_empty and poly.area > 0 else None
        return None
    if el["type"] == "relation":
        lines = [LineString([(p["lon"], p["lat"]) for p in m["geometry"] if p])
                 for m in el.get("members", []) if m.get("type") == "way" and m.get("role", "outer") in ("outer", "")
                 and len(m.get("geometry") or []) >= 2]
        polys = list(polygonize(unary_union(lines))) if lines else []
        if not polys:
            return None
        geom = unary_union(polys)
        return geom if not geom.is_empty and geom.area > 0 else None
    return None


def footprints(payload: dict) -> dict[str, dict]:
    out = {}
    for el in payload.get("elements", []):
        geom = element_polygon(el)
        if geom is not None:
            out[f"{el['type']}/{el['id']}"] = mapping(geom.simplify(0.00005, preserve_topology=True))
    return out


def to_geojson(payload: dict, polys: dict[str, dict] | None = None) -> dict:
    polys = polys or {}
    features = []
    for el in payload.get("elements", []):
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        ref = f"{el['type']}/{el['id']}"
        props = {"source_ref": ref, "name": tags.get("name"), "tags": tags}
        if el.get("bounds"):
            props["bounds"] = el["bounds"]
        if ref in polys:
            props["footprint"] = polys[ref]
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props})
    return {
        "type": "FeatureCollection",
        "metadata": {
            "source": "OpenStreetMap via Overpass API", "license": "ODbL 1.0",
            "osm_base": payload.get("osm3s", {}).get("timestamp_osm_base"),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "count": len(features), "footprints": sum(1 for f in features if "footprint" in f["properties"]),
        },
        "features": features,
    }


def add_footprints(gj: dict, polys: dict[str, dict]) -> dict:
    n = 0
    for f in gj["features"]:
        ref = f["properties"].get("source_ref")
        if ref in polys:
            f["properties"]["footprint"] = polys[ref]
            n += 1
    gj.setdefault("metadata", {})["footprints"] = n
    gj["metadata"]["footprints_fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return gj


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/thermal/sites_india.geojson")
    ap.add_argument("--footprints-only", action="store_true", help="add polygons to an existing --out file")
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    polys = footprints(fetch(FOOTPRINT_QUERY))
    if args.footprints_only:
        gj = add_footprints(json.loads(out.read_text(encoding="utf-8")), polys)
    else:
        gj = to_geojson(fetch(QUERY), polys)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(gj), encoding="utf-8")
    tmp.replace(out)
    print(f"wrote {len(gj['features'])} facilities ({gj['metadata']['footprints']} with footprints) -> {out}")
