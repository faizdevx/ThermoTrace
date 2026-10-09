"""Standalone thermal analysis (no database required).

    python scripts/run_thermal.py --sites ../data/thermal/sites_india.geojson --out ../data/thermal/out
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.thermal import firms, pipeline  # noqa: E402
from src.thermal.sites import SiteIndex  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sites", help="GeoJSON of OSM facilities (scripts/fetch_thermal_sites.py)")
    ap.add_argument("--postgis", help="use industrial_sites from this DSN instead of --sites")
    ap.add_argument("--span", default="7d", choices=["24h", "48h", "7d"])
    ap.add_argument("--no-landcover", action="store_true")
    ap.add_argument("--cache", default="data/thermal")
    ap.add_argument("--out", default="data/thermal/out")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    det = firms.fetch(firms.FirmsConfig(span=args.span, map_key=None))
    index = SiteIndex.from_postgis(args.postgis) if args.postgis else \
        SiteIndex.from_geojson(args.sites) if args.sites else None
    window = {"24h": 1, "48h": 2, "7d": 7}[args.span]
    result = pipeline.analyse(det, index, window_days=window, use_landcover=not args.no_landcover,
                              cache_dir=Path(args.cache))
    pipeline.write_outputs(result, Path(args.out))
    import json
    print(json.dumps(result.summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
