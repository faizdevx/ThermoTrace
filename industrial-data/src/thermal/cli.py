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

from . import baseline as baseline_mod
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
    # merged, provenance-tracked facility database first (python -m src.facilities.cli build)
    merged = env_path("THERMAL_FACILITIES_GEOJSON", "data/facilities/facilities.geojson")
    if merged.exists():
        return SiteIndex.from_geojson(merged)
    geojson = env_path("THERMAL_SITES_GEOJSON", "data/thermal/sites_india.geojson")
    if geojson.exists():
        return SiteIndex.from_geojson(geojson)
    logger.warning("no facility source available; every source will be treated as unmapped")
    return None


def load_baseline(sites: SiteIndex | None) -> baseline_mod.BaselineIndex | None:
    """365-day history per facility / cell: the PostGIS view when it is populated, else the file written by
    ``python -m src.thermal.baseline compute`` (THERMAL_BASELINE_DIR), else None (sources show no baseline)."""
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        try:
            index = baseline_mod.BaselineIndex.from_postgis(dsn.replace("postgresql+psycopg2://", "postgresql://"))
            if len(index):
                return index
        except Exception as exc:  # noqa: BLE001
            logger.warning("baseline view unavailable (%s); trying the baseline file", exc)
    index = baseline_mod.BaselineIndex.load(env_path("THERMAL_BASELINE_DIR", "data/baseline"))
    if index is None:
        logger.warning("no thermal baseline available: run `python -m src.thermal.baseline load|compute`")
    return index


def load_model_assets() -> tuple:
    """(model, facilities table) for the second opinion; (None, None) keeps the rules as the only answer."""
    from src.ml import live
    model = live.load_model()
    if model is None:
        return None, None
    dsn = os.getenv("DATABASE_URL")
    dsn = dsn.replace("postgresql+psycopg2://", "postgresql://") if dsn and os.getenv("THERMAL_SITES_FROM_DB", "").lower() in ("1", "true", "yes") else None
    fac = live.facilities_frame(env_path("THERMAL_FACILITIES_GEOJSON", "data/facilities/facilities.geojson"), dsn)
    return model, fac


def refresh_once(out_dir: Path, window_days: int, use_landcover: bool = True, interval_seconds: int = 1800,
                 force: bool = False) -> dict:
    """One cycle. Publishes (snapshot + PostGIS + notifications) ONLY when FIRMS delivered new detections,
    so an unchanged source never creates a new revision or wakes clients."""
    started = datetime.now(timezone.utc)
    store.write_status(out_dir, state="checking", last_attempt_at=started, poll_interval_seconds=interval_seconds)
    try:
        fresh = firms.fetch(firms.FirmsConfig.from_env())
        history = store.load_history(out_dir)
        known = set(history["detection_id"]) if len(history) else set()
        new_count = int((~fresh["detection_id"].isin(known)).sum())
        checked = datetime.now(timezone.utc)
        if known and new_count == 0 and not force and store.snapshot_exists(out_dir):
            status = store.write_status(out_dir, state="idle", last_check_at=checked, last_error=None,
                                        consecutive_failures=0, new_detections=0, poll_interval_seconds=interval_seconds,
                                        source_published_at=fresh.attrs.get("published_at"))
            logger.info("FIRMS checked: no new detections (nothing published)")
            return status
        store.write_status(out_dir, state="processing", new_detections=new_count)
        merged = store.merge_history(history, fresh, keep_days=window_days)
        store.save_history(out_dir, merged)
        span_days = max(int((merged.acq_datetime.max() - merged.acq_datetime.min()).days) + 1, 1)
        sites = load_sites()
        model, fac_frame = load_model_assets()
        result = pipeline.analyse(merged, sites, window_days=min(span_days, window_days),
                                  use_landcover=use_landcover, cache_dir=out_dir, baseline=load_baseline(sites),
                                  model=model, facilities=fac_frame)
        _attach_imagery(result, out_dir)
        store.write_snapshot(out_dir, result)
        run_id = None
        dsn = os.getenv("DATABASE_URL")
        if dsn and os.getenv("THERMAL_PERSIST_DB", "true").lower() in ("1", "true", "yes"):
            try:
                run_id = store.persist_postgis(dsn.replace("postgresql+psycopg2://", "postgresql://"), result)
                _maybe_refresh_baseline(dsn.replace("postgresql+psycopg2://", "postgresql://"), out_dir)
                from src.ml import live as ml_live
                store.register_model(dsn.replace("postgresql+psycopg2://", "postgresql://"), ml_live.model_path().with_name("metrics.json"))
                _raise_alerts(dsn.replace("postgresql+psycopg2://", "postgresql://"), result, out_dir)
            except Exception as exc:  # noqa: BLE001 - snapshot is still valid; report, do not hide
                logger.error("PostGIS persist failed: %s", exc)
                store.write_status(out_dir, db_error=str(exc)[:300])
        finished = datetime.now(timezone.utc)
        latest = merged.acq_datetime.max()
        published = fresh.attrs.get("published_at")
        status = store.write_status(
            out_dir, state="idle", last_attempt_at=started, last_check_at=checked, last_success_at=finished,
            last_change_at=finished, last_error=None, latest_detection_at=latest,
            fetch_errors=fresh.attrs.get("errors", []), db_run_id=run_id, summary=result.summary,
            duration_seconds=(finished - started).total_seconds(), consecutive_failures=0,
            new_detections=new_count, poll_interval_seconds=interval_seconds, source_published_at=published,
            # source file published -> our commit (the pipeline's own latency; satellite->file latency is upstream)
            ingest_latency_seconds=(finished - published).total_seconds() if published else None)
        logger.info("thermal refresh ok: %d sources, latest detection %s", len(result.sources), latest)
        return status
    except Exception as exc:  # noqa: BLE001
        prev = store.read_status(out_dir)
        failures = int(prev.get("consecutive_failures", 0)) + 1
        store.write_status(out_dir, state="error", last_error=str(exc)[:500], consecutive_failures=failures)
        logger.exception("thermal refresh failed (%d in a row)", failures)
        raise


def _raise_alerts(dsn: str, result, out_dir: Path) -> None:
    """Industrial-fire alerts for this cycle (deduplicated per facility, delivered to every configured channel)."""
    if os.getenv("ALERTS_ENABLED", "true").lower() not in ("1", "true", "yes") or result.sources.empty:
        return
    import psycopg2
    from src.alerts import store as alert_store
    conn = psycopg2.connect(dsn, application_name="thermotrace-alerts")
    try:
        stats = alert_store.process_cycle(conn, result.sources)
    finally:
        conn.close()
    logger.info("alerts: %s", stats)
    store.write_status(out_dir, alerts=stats)


def _attach_imagery(result, out_dir: Path) -> None:
    """Sentinel-2 / Landsat evidence for alerts and industrial candidates (cached; a few new ones per cycle)."""
    if os.getenv("THERMAL_IMAGERY", "true").lower() not in ("1", "true", "yes") or result.sources.empty:
        return
    try:
        from src.imagery import core, enrich
        priority = []
        try:                                  # sources that raise an alert get their imagery first
            from src.alerts import rules as alert_rules
            priority = [a.source_id for a in alert_rules.evaluate(result.sources)]
        except Exception:  # noqa: BLE001
            pass
        cache_dir = env_path("THERMAL_IMAGERY_DIR", str(out_dir.parent / "imagery"))
        result.sources, stats = enrich.enrich_sources(
            result.sources, cache_dir, per_cycle=int(os.getenv("THERMAL_IMAGERY_PER_CYCLE", "4")),
            budget_seconds=float(os.getenv("THERMAL_IMAGERY_BUDGET_SECONDS", "240")), priority_ids=priority)
        result.summary["imagery"] = stats
        logger.info("imagery: %s", stats)
    except Exception as exc:  # noqa: BLE001 - never lose a cycle over evidence
        logger.warning("imagery enrichment skipped: %s", exc)


def _maybe_refresh_baseline(dsn: str, out_dir: Path, every_hours: float = 24.0) -> None:
    """Newly stored detections get their facility/cell assigned; the baseline view is rebuilt at most once a day
    (it excludes the last 14 days, so it does not need to follow every 10-minute cycle)."""
    try:
        status = store.read_status(out_dir)
        last = status.get("baseline_refreshed_at")
        due = last is None or (datetime.now(timezone.utc) - pd.Timestamp(last).to_pydatetime()).total_seconds() > every_hours * 3600
        if due:
            info = baseline_mod.refresh_postgis(dsn)
            store.write_status(out_dir, baseline_refreshed_at=datetime.now(timezone.utc), baseline_geo_assigned=info["geo_assigned"])
            logger.info("baseline view refreshed (%d new detections assigned)", info["geo_assigned"])
        else:
            with __import__("psycopg2").connect(dsn) as conn, conn.cursor() as cur:
                cur.execute("SELECT assign_thermal_geo()")
    except Exception as exc:  # noqa: BLE001 - the baseline is context, never a reason to lose a cycle
        logger.warning("baseline refresh skipped: %s", exc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="thermotrace-thermal")
    ap.add_argument("command", choices=["refresh", "run"])
    ap.add_argument("--out", default=os.getenv("THERMAL_DATA_DIR", "data/thermal/live"))
    ap.add_argument("--window-days", type=int, default=int(os.getenv("THERMAL_WINDOW_DAYS", "14")))
    ap.add_argument("--interval", type=int, default=int(os.getenv("THERMAL_INTERVAL_SECONDS", "600")))
    ap.add_argument("--no-landcover", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    out = Path(args.out)

    if args.command == "refresh":
        try:
            refresh_once(out, args.window_days, not args.no_landcover, args.interval, force=True)
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
            refresh_once(out, args.window_days, not args.no_landcover, args.interval)
            failures, wait = 0, args.interval
        except Exception:  # noqa: BLE001
            failures += 1
            wait = min(args.interval * 2 ** min(failures, 4), 3600) * (0.5 + random.random() / 2)
        stop.wait(wait)
    return 0


if __name__ == "__main__":
    sys.exit(main())
