"""Fill the caches that offline mode lives on, while the stack is still online.

    python -m src.thermal.prefetch boundaries                          # India and state polygons (geoBoundaries) the worker clips and labels with
    python -m src.thermal.prefetch landcover [--detections-days 30] [--no-facilities]   # ESA WorldCover fractions for facilities, current sources, recent detections
    python -m src.thermal.prefetch imagery                             # Sentinel-2 / Landsat analysis for every alert and imagery candidate
    python -m src.thermal.prefetch status                              # what is cached

Inside the stack: ``docker compose run --rm --entrypoint python thermal -m src.thermal.prefetch landcover``. Offline, a source in a cell that
is not cached has unknown land cover (the classifier says so) and an alert without a cached scene says "offline"; nothing is invented.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd

from src import netguard

logger = logging.getLogger("thermotrace.prefetch")


def _out_dir() -> Path:
    return Path(os.getenv("THERMAL_DATA_DIR", "data/thermal/live"))


def _dsn() -> str | None:
    d = os.getenv("DATABASE_URL")
    return d.replace("postgresql+psycopg2://", "postgresql://") if d else None


def landcover_points(days: int, facilities: bool = True) -> pd.DataFrame:
    pts = []
    fac = Path(os.getenv("THERMAL_FACILITIES_GEOJSON", "data/facilities/facilities.geojson"))
    if facilities and fac.exists():
        for f in json.loads(fac.read_text(encoding="utf-8")).get("features", []):
            lon, lat = f["geometry"]["coordinates"][:2]
            pts.append((lat, lon))
    dsn = _dsn()
    if dsn:
        import psycopg2
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            if facilities and not fac.exists():
                cur.execute("SELECT ST_Y(geometry), ST_X(geometry) FROM facilities")
                pts += list(cur.fetchall())
            cur.execute("SELECT ST_Y(geometry), ST_X(geometry) FROM thermal_sources WHERE is_current")
            pts += list(cur.fetchall())
            cur.execute("SELECT DISTINCT round(ST_Y(geometry)::numeric, 3), round(ST_X(geometry)::numeric, 3) FROM thermal_detections "
                        "WHERE acq_datetime >= now() - make_interval(days => %s)", (days,))
            pts += [(float(a), float(b)) for a, b in cur.fetchall()]
    df = pd.DataFrame(pts, columns=["lat", "lon"]).dropna().drop_duplicates()
    return df.reset_index(drop=True)


def prefetch_boundaries() -> dict:
    from src.thermal.pipeline import load_boundary
    if netguard.enabled():
        raise SystemExit("OFFLINE_MODE is on: prefetch needs the internet.")
    out = {}
    for level in ("ADM0", "ADM1"):
        gdf = load_boundary(level, _out_dir())
        out[level] = len(gdf)
    print(json.dumps({**out, "dir": str(_out_dir())}))
    return out


def prefetch_landcover(days: int, facilities: bool = True) -> dict:
    from src.thermal import landcover
    if netguard.enabled():
        raise SystemExit("OFFLINE_MODE is on: prefetch needs the internet. Run it on the online machine (or with OFFLINE_MODE unset).")
    pts = landcover_points(days, facilities)
    cache = _out_dir() / "landcover_cache.json"
    before = len(pd.read_json(cache, orient="index")) if cache.exists() else 0
    landcover.sample_many(pts, cache)
    after = len(pd.read_json(cache, orient="index"))
    out = {"points": len(pts), "cells_before": before, "cells_after": after, "cache": str(cache)}
    print(json.dumps(out))
    return out


def prefetch_imagery() -> dict:
    from src.imagery import core
    if netguard.enabled():
        raise SystemExit("OFFLINE_MODE is on: prefetch needs the internet.")
    dsn = _dsn()
    if not dsn:
        raise SystemExit("DATABASE_URL is needed to find the alerts")
    import psycopg2
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT source_id, ST_X(geometry), ST_Y(geometry), last_seen, first_seen FROM alerts WHERE NOT is_test AND last_seen IS NOT NULL ORDER BY created_at DESC LIMIT 200")
        rows = cur.fetchall()
    done = failed = 0
    cache = core.DiskCache(core.default_cache_dir())
    for sid, lon, lat, last, first in rows:
        try:
            core.cached_analysis(sid, lon, lat, last.isoformat(), cache=cache, first_seen=first.isoformat() if first else None)
            done += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning("imagery for %s failed: %s", sid, exc)
    out = {"alerts": len(rows), "cached": done, "failed": failed, "dir": str(core.default_cache_dir())}
    print(json.dumps(out))
    return out


def status() -> dict:
    lc = _out_dir() / "landcover_cache.json"
    from src.imagery import core
    img = Path(core.default_cache_dir())
    out = {"landcover_cells": len(pd.read_json(lc, orient="index")) if lc.exists() else 0,
           "imagery_results": len(list(img.glob("*.json"))) if img.exists() else 0, "offline_mode": netguard.enabled()}
    print(json.dumps(out))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["boundaries", "landcover", "imagery", "status"])
    ap.add_argument("--detections-days", type=int, default=30)
    ap.add_argument("--no-facilities", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if a.what == "boundaries":
        prefetch_boundaries()
    elif a.what == "landcover":
        prefetch_landcover(a.detections_days, not a.no_facilities)
    elif a.what == "imagery":
        prefetch_imagery()
    else:
        status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
