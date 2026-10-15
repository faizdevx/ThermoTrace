"""Read-only access to the merged facility database (OSM + WRI GPPD + GEM + VNF).

Two backends with the same contract:
  * PostGIS - tables ``facilities`` / ``facility_sources`` (``python -m src.facilities.cli build --postgis``),
    used when DATA_MODE=postgis and the table has rows;
  * files   - ``data/facilities/{facilities.geojson, facility_footprints.geojson, facility_sources.csv}``
    (``FACILITIES_DIR``), used otherwise.
Every response carries the licence and attribution of each contributing source.
"""
from __future__ import annotations

import csv
import json
import os
import threading
from pathlib import Path
from typing import Any

from backend.app.services.postgis_repo import InvalidFilter, parse_bbox

MAX_LIMIT = 20000
LICENCES = {
    "osm": {"name": "OpenStreetMap", "licence": "ODbL-1.0", "attribution": "(c) OpenStreetMap contributors"},
    "wri_gppd": {"name": "WRI Global Power Plant Database", "licence": "CC-BY-4.0",
                 "attribution": "World Resources Institute, Global Power Plant Database"},
    "gem": {"name": "Global Energy Monitor trackers", "licence": "CC-BY-4.0 (check each tracker)",
            "attribution": "Global Energy Monitor"},
    "ggfr": {"name": "World Bank flare sites (VIIRS Nightfire)", "licence": "CC-BY-4.0",
             "attribution": "World Bank GFMR / Earth Observation Group VIIRS Nightfire"},
    "vnf": {"name": "VIIRS Nightfire (flare sites)", "licence": "EOG terms (CC BY 4.0 as published)",
            "attribution": "Earth Observation Group, Colorado School of Mines"},
}
PROPS = ("facility_id", "name", "kind", "radius_m", "capacity_mw", "status", "fuel", "primary_source",
         "source_count", "sources", "attribution", "has_footprint")
EMPTY_REASON = "No facility database yet. Run: python -m src.facilities.cli build"


class Filters:
    def __init__(self, *, bbox: str | None, kind: str | None, source: str | None, limit: int):
        self.bbox = parse_bbox(bbox)
        self.kinds = {k.strip() for k in kind.split(",") if k.strip()} if kind else set()
        if source and source not in LICENCES:
            raise InvalidFilter(f"unknown source: {source} (one of {', '.join(LICENCES)})")
        self.source, self.limit = source, limit


def _source_meta(present: set[str]) -> list[dict]:
    return [{"id": s, **LICENCES[s]} for s in LICENCES if s in present]


def _collection(features: list, total: int, present: set[str], backend: str) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features,
            "meta": {"available": True, "backend": backend, "total": total, "returned": len(features),
                     "truncated": total > len(features), "sources": _source_meta(present)}}


def _empty(backend: str) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": [],
            "meta": {"available": False, "backend": backend, "total": 0, "returned": 0, "truncated": False,
                     "sources": [], "reason": EMPTY_REASON}}


# --------------------------------------------------------------------------- files
def default_dir() -> Path:
    env = os.getenv("FACILITIES_DIR")
    if env:
        return Path(env)
    legacy = os.getenv("FACILITIES_GEOJSON")  # older setting pointed at the GeoJSON itself
    if legacy:
        return Path(legacy).parent
    return Path(__file__).resolve().parents[4] / "data" / "facilities"


class FileBackend:
    name = "files"
    _lock = threading.Lock()
    _cache: dict[str, tuple] = {}

    def __init__(self, directory: Path | None = None):
        self.dir = directory or default_dir()
        self.points = self.dir / "facilities.geojson"
        if os.getenv("FACILITIES_GEOJSON"):
            self.points = Path(os.environ["FACILITIES_GEOJSON"])

    def available(self) -> bool:
        return self.points.exists()

    def _cached(self, path: Path, loader):
        if not path.exists():
            return None
        key = str(path)
        stamp = path.stat().st_mtime_ns
        with self._lock:
            hit = self._cache.get(key)
            if hit is None or hit[0] != stamp:
                hit = (stamp, loader(path))
                self._cache[key] = hit
            return hit[1]

    def _points(self) -> dict | None:
        return self._cached(self.points, lambda p: json.loads(p.read_text(encoding="utf-8")))

    def _footprints(self) -> dict[str, dict]:
        data = self._cached(self.dir / "facility_footprints.geojson",
                            lambda p: {f["properties"]["facility_id"]: f["geometry"]
                                       for f in json.loads(p.read_text(encoding="utf-8"))["features"]})
        return data or {}

    def _links(self) -> dict[str, list[dict]]:
        def load(p: Path) -> dict[str, list[dict]]:
            out: dict[str, list[dict]] = {}
            with p.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    row["distance_m"] = float(row["distance_m"]) if row.get("distance_m") else None
                    for k in ("name", "url"):
                        row[k] = row.get(k) or None
                    out.setdefault(row["facility_id"], []).append(row)
            return out
        return self._cached(self.dir / "facility_sources.csv", load) or {}

    def query(self, f: Filters) -> dict[str, Any]:
        data = self._points()
        if data is None:
            return _empty(self.name)
        feats, present, total = [], set(), 0
        for feat in data["features"]:
            p = feat["properties"]
            lon, lat = feat["geometry"]["coordinates"][:2]
            if f.bbox and not (f.bbox[0] <= lon <= f.bbox[2] and f.bbox[1] <= lat <= f.bbox[3]):
                continue
            if f.kinds and p.get("kind") not in f.kinds:
                continue
            srcs = str(p.get("sources", "")).split(",")
            if f.source and f.source not in srcs:
                continue
            total += 1
            present.update(srcs)
            if len(feats) < f.limit:
                feats.append(feat)
        return _collection(feats, total, present, self.name)

    def detail(self, facility_id: str) -> dict[str, Any] | None:
        data = self._points()
        if data is None:
            return None
        feat = next((x for x in data["features"] if x["properties"].get("facility_id") == facility_id), None)
        if feat is None:
            return None
        return {**feat, "footprint": self._footprints().get(facility_id),
                "source_records": self._links().get(facility_id, [])}


# --------------------------------------------------------------------------- PostGIS
class PostgisBackend:
    name = "postgis"

    def available(self) -> bool:
        from sqlalchemy import text
        from backend.app.db import get_engine
        try:
            with get_engine().connect() as c:
                return bool(c.execute(text(
                    "SELECT to_regclass('public.facilities') IS NOT NULL "
                    "AND EXISTS (SELECT 1 FROM facilities)")).scalar())
        except Exception:  # noqa: BLE001 - no database / no table: caller falls back to files
            return False

    @staticmethod
    def _feature(r) -> dict[str, Any]:
        props = {k: r[k] for k in PROPS if k in r.keys()}
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                "properties": props}

    _SELECT = """SELECT facility_id, name, kind, radius_m, capacity_mw, status, fuel, primary_source, source_count,
                        sources, attribution, footprint IS NOT NULL AS has_footprint,
                        ST_X(geometry) AS lon, ST_Y(geometry) AS lat"""

    def query(self, f: Filters) -> dict[str, Any]:
        from sqlalchemy import text
        from backend.app.db import get_engine
        cond, p = ["TRUE"], {"limit": f.limit}
        if f.bbox:
            cond.append("geometry && ST_MakeEnvelope(:w, :s, :e, :n, 4326)")
            p.update(w=f.bbox[0], s=f.bbox[1], e=f.bbox[2], n=f.bbox[3])
        if f.kinds:
            cond.append("kind = ANY(:kinds)")
            p["kinds"] = sorted(f.kinds)
        if f.source:
            cond.append(":source = ANY(string_to_array(sources, ','))")
            p["source"] = f.source
        where = " AND ".join(cond)
        with get_engine().connect() as c:
            total = c.execute(text(f"SELECT count(*) FROM facilities WHERE {where}"), p).scalar()
            present = {s for (row,) in c.execute(text(
                f"SELECT DISTINCT unnest(string_to_array(sources, ',')) FROM facilities WHERE {where}"), p) for s in [row]}
            rows = c.execute(text(f"{self._SELECT} FROM facilities WHERE {where} "
                                  "ORDER BY source_count DESC, facility_id LIMIT :limit"), p).mappings().all()
        return _collection([self._feature(r) for r in rows], int(total or 0), present, self.name)

    def detail(self, facility_id: str) -> dict[str, Any] | None:
        from sqlalchemy import text
        from backend.app.db import get_engine
        with get_engine().connect() as c:
            r = c.execute(text(f"{self._SELECT}, ST_AsGeoJSON(footprint, 6) AS footprint_json "
                               "FROM facilities WHERE facility_id = :id"), {"id": facility_id}).mappings().first()
            if r is None:
                return None
            links = c.execute(text("""SELECT facility_id, source, source_id, name, distance_m, url, licence, attribution
                                      FROM facility_sources WHERE facility_id = :id ORDER BY source, source_id"""),
                              {"id": facility_id}).mappings().all()
        return {**self._feature(r), "footprint": json.loads(r["footprint_json"]) if r["footprint_json"] else None,
                "source_records": [dict(x) for x in links]}


def get_backend():
    """PostGIS when the API runs against a database that holds the facility table, else the files."""
    from backend.app.config import get_settings
    mode = os.getenv("FACILITIES_BACKEND", "auto")
    if mode == "files":
        return FileBackend()
    try:
        postgis = get_settings().data_mode == "postgis"
    except Exception:  # noqa: BLE001 - misconfigured settings: stay on files
        postgis = False
    if mode == "postgis" or (postgis and PostgisBackend().available()):
        return PostgisBackend()
    return FileBackend()


def query(*, bbox: str | None, kind: str | None, source: str | None, limit: int) -> dict[str, Any]:
    return get_backend().query(Filters(bbox=bbox, kind=kind, source=source, limit=limit))


def detail(facility_id: str) -> dict[str, Any] | None:
    return get_backend().detail(facility_id)
