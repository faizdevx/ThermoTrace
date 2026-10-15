"""python -m src.labels.cli build  [--incidents data_labels/incidents_verified.csv] [--postgis]
python -m src.labels.cli verify   # re-hash the files named in the manifest
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from . import build as B


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(prog="labels")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--archive", action="append", default=None)
    b.add_argument("--facilities", default="../data/facilities/facilities.geojson")
    b.add_argument("--incidents", default="data_labels/incidents_verified.csv")
    b.add_argument("--out", default="../data/labels")
    b.add_argument("--cache-dir", default="../data/thermal")
    b.add_argument("--max-per-class", type=int, default=1500)
    b.add_argument("--no-landcover", action="store_true")
    b.add_argument("--postgis", action="store_true")
    b.add_argument("--publish", default="data_labels/labels_v1", help="copy parquet, csv.gz and manifest here (committed; small)")
    b.add_argument("--dsn", default=os.getenv("DATABASE_URL"))
    v = sub.add_parser("verify")
    v.add_argument("--out", default="data_labels/labels_v1", help="folder with the manifest (default: the committed copy)")
    v.add_argument("--manifest", default="manifest_v1.json")
    args = ap.parse_args(argv)

    if args.cmd == "verify":
        out = Path(args.out)
        m = json.loads((out / args.manifest).read_text())
        bad = 0
        for name, meta in m["outputs"].items():
            ok = B.sha256_of(out / name) == meta["sha256"]
            bad += not ok
            print(("OK  " if ok else "FAIL"), name, meta["sha256"][:16])
        return 1 if bad else 0

    archives = [Path(a) for a in (args.archive or ["../data/archive/snpp", "../data/archive/noaa20"])]
    manifest = B.build(archives, Path(args.facilities), Path(args.incidents), Path(args.out), cache_dir=Path(args.cache_dir),
                       max_per_class=args.max_per_class, use_landcover=not args.no_landcover)
    print(json.dumps({k: manifest[k] for k in ("rule_version", "total_labels", "class_totals", "counts", "ambiguous_units_dropped", "split")},
                     indent=2, default=str))
    if args.publish:
        import shutil
        dest = Path(args.publish)
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("labels_v1.parquet", "labels_v1.csv.gz", "manifest_v1.json"):
            shutil.copy2(Path(args.out) / name, dest / name)
        print("published ->", dest)
    if args.postgis:
        if not args.dsn:
            raise SystemExit("--postgis needs --dsn or DATABASE_URL")
        n = B.persist_postgis(args.dsn.replace("postgresql+psycopg2://", "postgresql://"), Path(args.out) / "labels_v1.parquet")
        print("postgis rows:", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
