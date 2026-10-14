"""Write the merged facility table: GeoJSON + CSV files (atomic) and, optionally, PostGIS."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

MIGRATION = Path(__file__).resolve().parents[2] / "sql" / "migrations" / "005_facilities.sql"


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _clean(v):
    return None if v is None or (isinstance(v, float) and v != v) else v


def to_geojson(facilities: pd.DataFrame) -> dict:
    feats = []
    for r in facilities.to_dict("records"):
        props = {k: _clean(r[k]) for k in ("facility_id", "name", "kind", "radius_m", "capacity_mw", "status",
                                           "primary_source", "source_count", "sources", "attribution")}
        props["source_ref"] = props["facility_id"]  # SiteIndex.from_geojson reads source_ref / kind / radius_m
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                      "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def write_outputs(out_dir: Path, facilities: pd.DataFrame, links: pd.DataFrame) -> dict[str, Path]:
    out_dir = Path(out_dir)
    paths = {"geojson": out_dir / "facilities.geojson", "sources": out_dir / "facility_sources.csv"}
    _atomic(paths["geojson"], json.dumps(to_geojson(facilities), allow_nan=False))
    _atomic(paths["sources"], links.to_csv(index=False))
    return paths


def persist_postgis(dsn: str, facilities: pd.DataFrame, links: pd.DataFrame) -> int:
    """Replace the facility tables in one transaction (readers never see a half-built table)."""
    import psycopg2
    from psycopg2.extras import execute_values

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION.read_text(encoding="utf-8"))
        cur.execute("DELETE FROM facility_sources")
        cur.execute("DELETE FROM facilities")
        execute_values(cur, """INSERT INTO facilities (facility_id, name, kind, geometry, radius_m, capacity_mw, status,
                               primary_source, source_count, sources, attribution) VALUES %s""",
                       [(r["facility_id"], r["name"], r["kind"], f"SRID=4326;POINT({r['lon']} {r['lat']})", r["radius_m"],
                         _clean(r["capacity_mw"]), _clean(r["status"]), r["primary_source"], r["source_count"],
                         r["sources"], r["attribution"]) for r in facilities.to_dict("records")], page_size=2000)
        execute_values(cur, """INSERT INTO facility_sources (facility_id, source, source_id, name, distance_m, url,
                               licence, attribution) VALUES %s ON CONFLICT (source, source_id) DO NOTHING""",
                       [(r["facility_id"], r["source"], r["source_id"], _clean(r["name"]), _clean(r["distance_m"]),
                         _clean(r["url"]), r["licence"], r["attribution"]) for r in links.to_dict("records")],
                       page_size=2000)
    return len(facilities)
