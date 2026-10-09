"""Thermal refresh service.

    python -m src.thermal.cli refresh      # one cycle
    python -m src.thermal.cli run          # loop forever (default every 30 min)

Each cycle: fetch FIRMS NRT detections -> merge with the stored history -> cluster, match
facilities, sample land cover, classify -> publish an atomic snapshot -> (optionally) commit to PostGIS
and notify API clients. A failed cycle never replaces the last good snapshot.
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import firms, pipeline, store
from .sites import SiteIndex

logger = logging.getLogger("thermotrace.thermal")


def env_path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default))


def load_sites() -> SiteIndex | None:
    dsn = os.getenv("DATABASE_URL")
    if dsn and os.getenv("THERMAL_SITES_FROM_DB", "").lower() in ("1", "true", "yes"):
        try:
            return SiteIndex.from_postgis(dsn.replace("postgresql+psycopg2://", "postgresql://"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not read facilities from PostGIS (%s); falling back to GeoJSON", exc)
    geojson = env_path("THERMAL_SITES_GEOJSON", "data/thermal/sites_india.geojson")
    if geojson.exists():
        return SiteIndex.from_geojson(geojson)
    logger.warning("no facility source available; every source will be treated as unmapped")
    return None


def refresh_once(out_dir: Path, window_days: int, use_landcover: bool = True) -> dict:
    started = datetime.now(timezone.utc)
    store.write_status(out_dir, state="running", last_attempt_at=started)
    try:
        fresh = firms.fetch(firms.FirmsConfig.from_env())
        history = store.load_history(out_dir)
        merged = store.merge_history(history, fresh, keep_days=window_days)
        store.save_history(out_dir, merged)
        span_days = max(int((merged.acq_datetime.max() - merged.acq_datetime.min()).days) + 1, 1)
        result = pipeline.analyse(merged, load_sites(), window_days=min(span_days, window_days),
                                  use_landcover=use_landcover, cache_dir=out_dir)
        store.write_snapshot(out_dir, result)
        run_id = None
        dsn = os.getenv("DATABASE_URL")
        if dsn and os.getenv("THERMAL_PERSIST_DB", "true").lower() in ("1", "true", "yes"):
            try:
                run_id = store.persist_postgis(dsn.replace("postgresql+psycopg2://", "postgresql://"), result)
            except Exception as exc:  # noqa: BLE001 - snapshot is still valid; report, do not hide
                logger.error("PostGIS persist failed: %s", exc)
                store.write_status(out_dir, db_error=str(exc)[:300])
        finished = datetime.now(timezone.utc)
        latest = merged.acq_datetime.max()
        status = store.write_status(
            out_dir, state="idle", last_attempt_at=started, last_success_at=finished, last_error=None,
            latest_detection_at=latest, fetch_errors=fresh.attrs.get("errors", []), db_run_id=run_id,
            summary=result.summary, duration_seconds=(finished - started).total_seconds(),
            consecutive_failures=0)
        logger.info("thermal refresh ok: %d sources, latest detection %s", len(result.sources), latest)
        return status
    except Exception as exc:  # noqa: BLE001
        prev = store.read_status(out_dir)
        failures = int(prev.get("consecutive_failures", 0)) + 1
        store.write_status(out_dir, state="error", last_error=str(exc)[:500], consecutive_failures=failures)
        logger.exception("thermal refresh failed (%d in a row)", failures)
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="thermotrace-thermal")
    ap.add_argument("command", choices=["refresh", "run"])
    ap.add_argument("--out", default=os.getenv("THERMAL_DATA_DIR", "data/thermal/live"))
    ap.add_argument("--window-days", type=int, default=int(os.getenv("THERMAL_WINDOW_DAYS", "14")))
    ap.add_argument("--interval", type=int, default=int(os.getenv("THERMAL_INTERVAL_SECONDS", "1800")))
    ap.add_argument("--no-landcover", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    out = Path(args.out)

    if args.command == "refresh":
        try:
            refresh_once(out, args.window_days, not args.no_landcover)
        except Exception:  # noqa: BLE001
            return 1
        return 0

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except ValueError:
            pass
    failures = 0
    while not stop.is_set():
        try:
            refresh_once(out, args.window_days, not args.no_landcover)
            failures, wait = 0, args.interval
        except Exception:  # noqa: BLE001
            failures += 1
            wait = min(args.interval * 2 ** min(failures, 4), 3600) * (0.5 + random.random() / 2)
        stop.wait(wait)
    return 0


if __name__ == "__main__":
    sys.exit(main())
