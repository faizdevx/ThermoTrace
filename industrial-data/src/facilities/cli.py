"""python -m src.facilities.cli build --osm data/thermal/sites_india.geojson [--wri gppd.csv] [--gem file.xlsx:coal_plant] [--vnf vnf.csv]

Sources you do not pass are skipped and reported; the build never fabricates a missing source.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from .merge import merge
from .sources import GEM_TRACKERS, load_gem, load_osm, load_vnf, load_wri_gppd
from .store import persist_postgis, write_outputs


def build(args: argparse.Namespace) -> int:
    frames, report = [], {}
    if args.osm:
        frames.append(load_osm(args.osm))
    if args.wri:
        frames.append(load_wri_gppd(args.wri))
    for spec in args.gem or []:
        path, _, tracker = spec.rpartition(":")
        if not path or tracker not in GEM_TRACKERS:
            raise SystemExit(f"--gem expects <file>:<tracker>, tracker one of {sorted(GEM_TRACKERS)}")
        frames.append(load_gem(path, tracker))
    if args.vnf:
        frames.append(load_vnf(args.vnf, min_days=args.vnf_min_days))
    for f in frames:
        report[f.source.iloc[0] if len(f) else "empty"] = report.get(f.source.iloc[0] if len(f) else "empty", 0) + len(f)
    facilities, links = merge(frames, max_distance_m=args.distance)
    paths = write_outputs(Path(args.out), facilities, links)
    print("source records:", report)
    print(f"facilities: {len(facilities)} (merged from {len(links)} records; "
          f"{int((facilities.source_count > 1).sum())} confirmed by 2+ sources)")
    print(facilities.kind.value_counts().to_dict())
    print({k: str(v) for k, v in paths.items()})
    dsn = args.dsn or os.getenv("DATABASE_URL")
    if args.postgis:
        if not dsn:
            raise SystemExit("--postgis needs --dsn or DATABASE_URL")
        print("postgis rows:", persist_postgis(dsn, facilities, links))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="facilities")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--osm"), b.add_argument("--wri"), b.add_argument("--vnf")
    b.add_argument("--gem", action="append", help="<file>:<tracker>, repeatable")
    b.add_argument("--vnf-min-days", type=int, default=5)
    b.add_argument("--distance", type=float, default=500.0)
    b.add_argument("--out", default="data/facilities")
    b.add_argument("--postgis", action="store_true"), b.add_argument("--dsn")
    b.set_defaults(fn=build)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
