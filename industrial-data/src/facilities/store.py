"""Write the merged facility table: GeoJSON + CSV files (atomic) and, optionally, PostGIS.

Files (all in one directory, replaced atomically one by one):
  facilities.geojson            points + attributes (what the thermal matcher and the map list read)
  facility_footprints.geojson   OSM polygons keyed by facility_id (detail view; kept out of the light list)
  facility_sources.csv          one row per source record: provenance, licence, attribution
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
from shapely import wkt as shapely_wkt
from shapely.geometry import mapping

MIGRATION = Path(__file__).resolve().parents[2] / "sql" / "migrations" / "005_facilities.sql"
PROPS = ("facility_id", "name", "kind", "radius_m", "capacity_mw", "status", "fuel", "primary_source",
         "source_count", "sources", "attribution")


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _clean(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v.item() if hasattr(v, "item") else v


def to_geojson(facilities: pd.DataFrame) -> dict:
    feats = []
    for r in facilities.to_dict("records"):
        props = {k: _clean(r.get(k)) for k in PROPS}
        props["source_ref"] = props["facility_id"]  # SiteIndex.from_geojson reads source_ref / kind / radius_m
        props["has_footprint"] = bool(_clean(r.get("footprint")))
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                      "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def footprints_geojson(facilities: pd.DataFrame) -> dict:
    feats = []
    for r in facilities.to_dict("records"):
        fp = _clean(r.get("footprint"))
        if fp:
            feats.append({"type": "Feature", "geometry": mapping(shapely_wkt.loads(fp)),
                          "properties": {"facility_id": r["facility_id"]}})
    return {"type": "FeatureCollection", "features": feats}


def write_outputs(out_dir: Path, facilities: pd.DataFrame, links: pd.DataFrame) -> dict[str, Path]:
    out_dir = Path(out_dir)
    paths = {"geojson": out_dir / "facilities.geojson", "footprints": out_dir / "facility_footprints.geojson",
             "sources": out_dir / "facility_sources.csv"}
    _atomic(paths["sources"], links.to_csv(index=False))
    _atomic(paths["footprints"], json.dumps(footprints_geojson(facilities), allow_nan=False))
    _atomic(paths["geojson"], json.dumps(to_geojson(facilities), allow_nan=False))  # last: readers key on it
    return paths


def persist_postgis(dsn: str, facilities: pd.DataFrame, links: pd.DataFrame) -> int:
    """Replace the facility tables in one transaction (readers never see a half-built table)."""
    import psycopg2
    from psycopg2.extras import execute_values

    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(MIGRATION.read_text(encoding="utf-8"))
            cur.execute("DELETE FROM facility_sources")
            cur.execute("DELETE FROM facilities")
            execute_values(cur, """INSERT INTO facilities (facility_id, name, kind, geometry, footprint, radius_m,
                                   capacity_mw, status, fuel, primary_source, source_count, sources, attribution)
                                   VALUES %s""",
                           [(r["facility_id"], _clean(r["name"]), r["kind"], f"SRID=4326;POINT({r['lon']} {r['lat']})",
                             f"SRID=4326;{r['footprint']}" if _clean(r.get("footprint")) else None,
                             float(r["radius_m"]), _clean(r["capacity_mw"]), _clean(r["status"]), _clean(r.get("fuel")),
                             r["primary_source"], int(r["source_count"]), r["sources"], r["attribution"])
                            for r in facilities.to_dict("records")],
                           template="(%s, %s, %s, ST_GeomFromEWKT(%s), ST_MakeValid(ST_GeomFromEWKT(%s)), %s, %s, %s,"
                                    " %s, %s, %s, %s, %s)",
                           page_size=2000)
            execute_values(cur, """INSERT INTO facility_sources (facility_id, source, source_id, name, distance_m, url,
                                   licence, attribution) VALUES %s ON CONFLICT (source, source_id) DO NOTHING""",
                           [(r["facility_id"], r["source"], str(r["source_id"]), _clean(r["name"]),
                             _clean(r["distance_m"]), _clean(r["url"]), r["licence"], r["attribution"])
                            for r in links.to_dict("records")],
                           page_size=2000)
    finally:
        conn.close()
    return len(facilities)
