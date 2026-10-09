"""Long-running sync worker: scheduling, manual triggers, reconcile, graceful shutdown."""
from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime, timezone

from .config import SyncConfig
from .db import connect, run_migrations
from .jobs import OSM_SOURCE, register_sources, run_osm_job

logger = logging.getLogger(__name__)


def _due_trigger(cfg: SyncConfig, row: dict, now: datetime) -> str | None:
    """Decide whether the OSM source needs a run now and why."""
    if not row["enabled"]:
        return None
    if row["manual_requested_at"] is not None:
        return "manual"
    if row["next_retry_at"] is not None and row["next_retry_at"] > now:
        return None
    last_reconcile = (row["totals"] or {}).get("last_reconcile_at")
    if last_reconcile:
        age = (now - datetime.fromisoformat(last_reconcile)).total_seconds()
        if age >= cfg.reconcile_every_seconds and row["cursor"]:
            return "reconcile"
    last_check = row["last_check_at"]
    if row["status"] == "awaiting_setup":
        return None   # needs the explicit bootstrap procedure
    if last_check is None or (now - last_check).total_seconds() >= cfg.osm.poll_interval_seconds:
        return "schedule"
    return None


def load_osm_state(cfg: SyncConfig) -> dict | None:
    conn = connect(cfg.dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT enabled, status, manual_requested_at, next_retry_at, last_check_at,
                          cursor, totals FROM sync_state WHERE source = %s""", (OSM_SOURCE,))
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    if row is None:
        return None
    keys = ("enabled", "status", "manual_requested_at", "next_retry_at", "last_check_at", "cursor", "totals")
    return dict(zip(keys, row))


class Worker:
    def __init__(self, cfg: SyncConfig, stop: threading.Event | None = None) -> None:
        self.cfg = cfg
        self.stop = stop or threading.Event()

    def install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            logger.info("signal %s received: finishing current step and shutting down", signum)
            self.stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except ValueError:  # not main thread
                pass

    def tick(self) -> str | None:
        """Run at most one due job. Returns the trigger that ran, if any."""
        row = load_osm_state(self.cfg)
        if row is None:
            register_sources(self.cfg)
            return None
        trigger = _due_trigger(self.cfg, row, datetime.now(timezone.utc))
        if trigger is None:
            return None
        outcome = run_osm_job(self.cfg, trigger, self.stop)
        logger.info("job trigger=%s outcome=%s", trigger, outcome.status)
        return trigger

    def run_forever(self) -> None:
        logger.info("worker starting (poll %ss)", self.cfg.osm.poll_interval_seconds)
        waited = 0
        while not self.stop.is_set():
            try:
                run_migrations(self.cfg.dsn)
                register_sources(self.cfg)
                break
            except Exception:  # noqa: BLE001 - DB may still be starting
                logger.exception("startup failed; retrying in 5s")
                if self.stop.wait(5):
                    return
                waited += 5
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - never let one bad tick kill the worker
                logger.exception("tick failed")
            self.stop.wait(self.cfg.tick_seconds)
        logger.info("worker stopped cleanly")
