"""Read-only access to the merged facility database (data/facilities/facilities.geojson).

Built by ``python -m src.facilities.cli build``; every facility keeps its source list and attribution,
and the response carries the licence of each contributing source so the map can credit them.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from backend.app.services.postgis_repo import parse_bbox

MAX_LIMIT = 20000
_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "data": None}

LICENCES = {
    "osm": {"name": "OpenStreetMap", "licence": "ODbL-1.0", "attribution": "(c) OpenStreetMap contributors"},
    "wri_gppd": {"name": "WRI Global Power Plant Database", "licence": "CC-BY-4.0",
                 "attribution": "World Resources Institute, Global Power Plant Database"},
    "gem": {"name": "Global Energy Monitor trackers", "licence": "CC-BY-4.0 (check each tracker)",
            "attribution": "Global Energy Monitor"},
    "vnf": {"name": "VIIRS Nightfire (flare sites)", "licence": "EOG terms (CC BY 4.0 as published)",
            "attribution": "Earth Observation Group, Colorado School of Mines"},
}


def default_path() -> Path:
    env = os.getenv("FACILITIES_GEOJSON")
    return Path(env) if env else Path(__file__).resolve().parents[4] / "data" / "facilities" / "facilities.geojson"


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    key = (str(path), path.stat().st_mtime_ns)
    with _lock:
        if _cache["key"] != key:
            _cache.update(key=key, data=json.loads(path.read_text(encoding="utf-8")))
        return _cache["data"]


def query(*, bbox: str | None, kind: str | None, source: str | None, limit: int) -> dict[str, Any]:
    data = _load(default_path())
    if data is None:
        return {"type": "FeatureCollection", "features": [],
                "meta": {"available": False, "total": 0, "returned": 0, "sources": [],
                         "reason": "No facility database yet. Run: python -m src.facilities.cli build"}}
    box = parse_bbox(bbox)
    kinds = {k.strip() for k in kind.split(",")} if kind else None
    feats = []
    present: set[str] = set()
    total = 0
    for f in data["features"]:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        if box and not (box[0] <= lon <= box[2] and box[1] <= lat <= box[3]):
            continue
        if kinds and p.get("kind") not in kinds:
            continue
        if source and source not in str(p.get("sources", "")).split(","):
            continue
        total += 1
        present.update(str(p.get("sources", "")).split(","))
        if len(feats) < limit:
            feats.append(f)
    return {"type": "FeatureCollection", "features": feats,
            "meta": {"available": True, "total": total, "returned": len(feats), "truncated": total > len(feats),
                     "sources": [{"id": s, **LICENCES[s]} for s in LICENCES if s in present]}}
