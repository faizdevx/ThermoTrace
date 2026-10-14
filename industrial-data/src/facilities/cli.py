"""python -m src.facilities.cli build --osm data/thermal/sites_india.geojson [--wri gppd.csv]
                                    [--gem file.xlsx:coal_plant ...] [--vnf vnf.csv] [--postgis]

Sources you do not pass are skipped and reported; the build never fabricates a missing source.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .merge import merge
from .sources import GEM_TRACKERS, discover_raw, load_gem, load_osm, load_vnf, load_wri_gppd
from .store import persist_postgis, write_outputs


def build(args: argparse.Namespace) -> dict:
    frames, counts = [], {}

    def add(name: str, frame) -> None:
        frames.append(frame)
        counts[name] = counts.get(name, 0) + len(frame)

    if args.raw_dir:
        found = discover_raw(args.raw_dir)
        args.wri = args.wri or (str(found["wri"]) if found["wri"] else None)
        args.gem = (args.gem or []) + [f"{p}:{t}" for p, t in found["gem"]]
        args.vnf = args.vnf or (str(found["vnf"][0]) if found["vnf"] else None)
        if found["unrecognised"]:
            print("ignored (unrecognised file names):", [str(p) for p in found["unrecognised"]])
    if args.osm:
        add("osm", load_osm(args.osm))
    if args.wri:
        add("wri_gppd", load_wri_gppd(args.wri))
    for spec in args.gem or []:
        path, _, tracker = spec.rpartition(":")
        if not path or tracker not in GEM_TRACKERS:
            raise SystemExit(f"--gem expects <file>:<tracker>, tracker one of {sorted(GEM_TRACKERS)}")
        add(f"gem:{tracker}", load_gem(path, tracker))
    if args.vnf:
        add("vnf", load_vnf(args.vnf, min_days=args.vnf_min_days))
    if not frames:
        raise SystemExit("no source given (use --osm / --wri / --gem / --vnf)")

    facilities, links = merge(frames, max_distance_m=args.distance)
    paths = write_outputs(Path(args.out), facilities, links)
    report = {
        "source_records": counts,
        "skipped_sources": [s for s, given in (("wri_gppd", args.wri), ("gem", args.gem), ("vnf", args.vnf)) if not given],
        "facilities": len(facilities), "linked_records": len(links),
        "multi_source_facilities": int((facilities.source_count > 1).sum()),
        "with_footprint": int(facilities.footprint.notna().sum()),
        "by_kind": facilities.kind.value_counts().to_dict(),
        "files": {k: str(v) for k, v in paths.items()},
    }
    if args.postgis:
        dsn = args.dsn or os.getenv("DATABASE_URL")
        if not dsn:
            raise SystemExit("--postgis needs --dsn or DATABASE_URL")
        report["postgis_rows"] = persist_postgis(dsn.replace("postgresql+psycopg2://", "postgresql://"), facilities, links)
    print(json.dumps(report, indent=2))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="facilities")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="merge the given sources into data/facilities (+ PostGIS)")
    b.add_argument("--osm", help="OSM cache GeoJSON from scripts/fetch_thermal_sites.py")
    b.add_argument("--wri", help="WRI Global Power Plant Database CSV")
    b.add_argument("--gem", action="append", help="<file>:<tracker>, repeatable; trackers: " + ", ".join(sorted(GEM_TRACKERS)))
    b.add_argument("--vnf", help="VIIRS Nightfire CSV (detections or flare-site list)")
    b.add_argument("--raw-dir", help="auto-discover WRI / GEM / VNF files in this folder (see sources.discover_raw)")
    b.add_argument("--vnf-min-days", type=int, default=5)
    b.add_argument("--distance", type=float, default=500.0, help="merge distance in metres (default 500)")
    b.add_argument("--out", default="data/facilities")
    b.add_argument("--postgis", action="store_true", help="also replace the PostGIS facility tables")
    b.add_argument("--dsn", help="PostgreSQL DSN (default: DATABASE_URL)")
    args = ap.parse_args(argv)
    build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
