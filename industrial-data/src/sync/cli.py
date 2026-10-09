"""Command line: python -m src.sync.cli <migrate|bootstrap|run|once|status|verify|rebuild-admin>."""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .db import connect, run_migrations
from .jobs import register_sources, run_osm_job
from .worker import Worker

logger = logging.getLogger("thermotrace.sync")
ROOT = Path(__file__).resolve().parents[2]


def _boundaries_loaded(dsn: str) -> tuple[int, int]:
    conn = connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT (SELECT COUNT(*) FROM states), (SELECT COUNT(*) FROM districts)")
            states, districts = cur.fetchone()
        conn.commit()
        return int(states), int(districts)
    finally:
        conn.close()


def cmd_bootstrap(cfg, args) -> int:
    from . import osm_adapter

    run_migrations(cfg.dsn)
    register_sources(cfg)
    states, districts = _boundaries_loaded(cfg.dsn)
    if (states == 0 or districts == 0) and not args.skip_boundaries:
        logger.info("loading administrative boundaries (geoBoundaries gbOpen, CC BY 4.0)")
        subprocess.run([sys.executable, "scripts/run_boundaries.py", "--level", "all"], cwd=ROOT, check=True)
    else:
        logger.info("boundaries present: %d states, %d districts", states, districts)

    conn = connect(cfg.dsn)
    try:
        with conn.cursor() as cur:
            baseline_done = osm_adapter.read_local_state(cur) is not None
        conn.commit()
    finally:
        conn.close()
    if not baseline_done or args.force_import:
        pbf = osm_adapter.download_extract(cfg, Path(args.data_dir), force=args.force_import)
        osm_adapter.import_baseline(cfg, pbf)
    else:
        logger.info("baseline already imported; skipping download/import (use --force-import)")
    outcome = run_osm_job(cfg, "bootstrap", force_apply=True, force_admin_rebuild=args.rebuild_admin)
    print(json.dumps({"status": outcome.status, "error": outcome.error,
                      "created": getattr(outcome.result, "created", None),
                      "updated": getattr(outcome.result, "updated", None),
                      "deleted": getattr(outcome.result, "deleted", None)}))
    return 0 if outcome.status in {"success", "no_changes"} else 1


def cmd_status(cfg, _args) -> int:
    conn = connect(cfg.dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT revision FROM data_revision WHERE id = 1")
            revision = cur.fetchone()[0]
            cur.execute("SELECT source, status, cursor, last_check_at, last_commit_at, last_error FROM sync_state")
            rows = cur.fetchall()
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({"revision": revision, "sources": [
        dict(zip(("source", "status", "cursor", "last_check_at", "last_commit_at", "last_error"), r))
        for r in rows]}, default=str, indent=2))
    return 0


def cmd_verify(cfg, _args) -> int:
    """Consistency report: raw table vs osm_industries vs industrial_sites."""
    conn = connect(cfg.dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT (SELECT COUNT(*) FROM osm_industrial_raw),
                       (SELECT COUNT(*) FROM osm_industries WHERE is_active),
                       (SELECT COUNT(*) FROM industrial_sites WHERE is_active AND primary_source='openstreetmap'),
                       (SELECT COUNT(*) FROM industrial_sites WHERE is_active AND state IS NULL),
                       (SELECT COUNT(*) FROM industrial_sites WHERE is_active AND NOT ST_IsValid(geometry))""")
            raw, osm, sites, no_state, invalid = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    report = {"raw_rows": raw, "active_osm_industries": osm, "active_sites": sites,
              "sites_without_state": no_state, "invalid_geometries": invalid,
              "consistent": osm == sites and invalid == 0}
    print(json.dumps(report, indent=2))
    return 0 if report["consistent"] else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="thermotrace-sync")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    boot = sub.add_parser("bootstrap", help="boundaries + baseline import + first sync")
    boot.add_argument("--data-dir", default="/data/osm")
    boot.add_argument("--force-import", action="store_true")
    boot.add_argument("--skip-boundaries", action="store_true")
    boot.add_argument("--rebuild-admin", action="store_true")
    sub.add_parser("run", help="run the worker loop forever")
    once = sub.add_parser("once", help="run one sync cycle now")
    once.add_argument("--reconcile", action="store_true")
    sub.add_parser("status")
    sub.add_parser("verify")
    sub.add_parser("rebuild-admin", help="recompute state/district for all sites")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 64

    if args.command == "migrate":
        print(json.dumps({"applied": run_migrations(cfg.dsn)}))
        return 0
    if args.command == "bootstrap":
        return cmd_bootstrap(cfg, args)
    if args.command == "run":
        worker = Worker(cfg)
        worker.install_signal_handlers()
        worker.run_forever()
        return 0
    if args.command == "once":
        run_migrations(cfg.dsn)
        register_sources(cfg)
        outcome = run_osm_job(cfg, "reconcile" if args.reconcile else "manual")
        print(json.dumps({"status": outcome.status, "error": outcome.error}))
        return 0 if outcome.status in {"success", "no_changes", "skipped_locked"} else 1
    if args.command == "rebuild-admin":
        outcome = run_osm_job(cfg, "reconcile", force_apply=True, force_admin_rebuild=True)
        print(json.dumps({"status": outcome.status, "error": outcome.error}))
        return 0 if outcome.status in {"success", "no_changes"} else 1
    if args.command == "status":
        return cmd_status(cfg, args)
    if args.command == "verify":
        return cmd_verify(cfg, args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
