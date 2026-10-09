"""Sync jobs: one OSM replication cycle with durable checkpoint, locking and backoff."""
from __future__ import annotations

import json
import logging
import random
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import osm_adapter
from .config import SyncConfig
from .db import NOTIFY_CHANNEL_DATA, NOTIFY_CHANNEL_SYNC, advisory_lock, connect, notify
from .master import OsmSyncResult, apply_osm_sync

logger = logging.getLogger(__name__)

OSM_SOURCE = "openstreetmap"
OSM_LOCK = "thermotrace:sync:openstreetmap"


@dataclass
class JobOutcome:
    status: str                  # success | no_changes | failed | skipped_locked | awaiting_setup
    run_id: Optional[int] = None
    result: Optional[OsmSyncResult] = None
    error: Optional[str] = None
    ingest_latency_seconds: Optional[float] = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def backoff_seconds(cfg: SyncConfig, failures: int, rng: random.Random | None = None) -> float:
    """Exponential backoff with jitter in [0.5, 1.0] of the nominal delay."""
    rng = rng or random
    nominal = min(cfg.retry_max_seconds, cfg.retry_base_seconds * (2 ** max(failures - 1, 0)))
    return nominal * (0.5 + rng.random() / 2)


def register_sources(cfg: SyncConfig) -> None:
    """Create/refresh sync_state rows. Configuration lives in env, state lives in the DB."""
    from .government import government_sources  # local import: avoids cycle

    conn = connect(cfg.dsn)
    try:
        with conn.cursor() as cur:
            osm = cfg.osm
            cur.execute(
                """
                INSERT INTO sync_state (source, kind, enabled, status, update_cadence,
                                        poll_interval_seconds, freshness_target_seconds)
                VALUES (%s, 'osm_replication', %s,
                        CASE WHEN %s THEN 'awaiting_setup' ELSE 'not_configured' END, %s, %s, %s)
                ON CONFLICT (source) DO UPDATE SET
                    enabled = EXCLUDED.enabled, update_cadence = EXCLUDED.update_cadence,
                    poll_interval_seconds = EXCLUDED.poll_interval_seconds,
                    freshness_target_seconds = EXCLUDED.freshness_target_seconds,
                    status = CASE WHEN NOT EXCLUDED.enabled THEN 'not_configured'
                                  WHEN sync_state.status = 'not_configured' THEN 'awaiting_setup'
                                  ELSE sync_state.status END,
                    updated_at = NOW()
                """,
                (OSM_SOURCE, osm.enabled, osm.enabled, osm.cadence,
                 osm.poll_interval_seconds, osm.freshness_target_seconds),
            )
            if cfg.government_enabled:
                for src in government_sources():
                    cur.execute(
                        """
                        INSERT INTO sync_state (source, kind, enabled, status, update_cadence,
                                                last_error, totals)
                        VALUES (%s, 'government', FALSE, 'not_configured', %s, %s, %s::jsonb)
                        ON CONFLICT (source) DO UPDATE SET
                            update_cadence = EXCLUDED.update_cadence,
                            last_error = EXCLUDED.last_error, totals = EXCLUDED.totals,
                            status = CASE WHEN sync_state.enabled THEN sync_state.status
                                          ELSE 'not_configured' END,
                            updated_at = NOW()
                        """,
                        (src.source, src.cadence, src.reason, json.dumps(src.describe())),
                    )
        conn.commit()
    finally:
        conn.close()


def _set_status(conn, status: str, **extra) -> None:
    sets = ["status = %s", "updated_at = NOW()"]
    params: list = [status]
    for key, value in extra.items():
        sets.append(f"{key} = %s")
        params.append(value)
    with conn.cursor() as cur:
        cur.execute(f"UPDATE sync_state SET {', '.join(sets)} WHERE source = %s", params + [OSM_SOURCE])
        notify(cur, NOTIFY_CHANNEL_SYNC, {"source": OSM_SOURCE, "status": status})
    conn.commit()


def _heartbeat(dsn: str, stop: threading.Event) -> None:
    conn = connect(dsn, "thermotrace-heartbeat")
    conn.autocommit = True
    try:
        while not stop.wait(20):
            with conn.cursor() as cur:
                cur.execute("UPDATE sync_state SET heartbeat_at = NOW() WHERE source = %s", (OSM_SOURCE,))
    except Exception:  # noqa: BLE001 - heartbeat is best effort
        logger.warning("heartbeat thread stopped", exc_info=True)
    finally:
        conn.close()


def _record_failure(cfg: SyncConfig, run_id: int | None, error: str) -> None:
    conn = connect(cfg.dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT consecutive_failures FROM sync_state WHERE source = %s FOR UPDATE", (OSM_SOURCE,)
            )
            failures = int((cur.fetchone() or [0])[0]) + 1
            retry_at = utcnow() + timedelta(seconds=backoff_seconds(cfg, failures))
            cur.execute(
                """UPDATE sync_state SET status = 'error', last_error = %s, last_check_error = %s,
                       consecutive_failures = %s, next_retry_at = %s, heartbeat_at = NULL,
                       updated_at = NOW() WHERE source = %s""",
                (error[:1000], error[:1000], failures, retry_at, OSM_SOURCE),
            )
            if run_id:
                cur.execute(
                    """UPDATE sync_runs SET status = 'failed', finished_at = NOW(), error = %s,
                           failed_count = 1,
                           duration_seconds = EXTRACT(EPOCH FROM (NOW() - started_at))
                       WHERE id = %s""",
                    (error[:1000], run_id),
                )
            notify(cur, NOTIFY_CHANNEL_SYNC, {"source": OSM_SOURCE, "status": "error"})
        conn.commit()
    finally:
        conn.close()


def run_osm_job(
    cfg: SyncConfig,
    trigger: str = "schedule",
    stop: threading.Event | None = None,
    *,
    force_apply: bool = False,
    force_admin_rebuild: bool = False,
    remote_fetch: Callable[[SyncConfig], osm_adapter.RemoteState] = osm_adapter.fetch_remote_state,
    replication_update: Callable[..., str] = osm_adapter.run_replication_update,
) -> JobOutcome:
    """One cycle: check source -> apply diffs to raw table -> sync master tables -> checkpoint.

    The checkpoint (``sync_state.cursor``) moves only inside the same transaction that
    commits the corresponding master-table changes.
    """
    with advisory_lock(cfg.dsn, OSM_LOCK) as acquired:
        if not acquired:
            logger.info("another worker holds the %s lock; skipping", OSM_SOURCE)
            return JobOutcome("skipped_locked")
        return _run_locked(cfg, trigger, stop, force_apply, force_admin_rebuild, remote_fetch, replication_update)


def _run_locked(cfg, trigger, stop, force_apply, force_admin_rebuild, remote_fetch, replication_update) -> JobOutcome:
    conn = connect(cfg.dsn)
    run_id: int | None = None
    started = utcnow()
    hb_stop = threading.Event()
    hb_thread: threading.Thread | None = None
    try:
        with conn.cursor() as cur:
            # Locked => no live worker owns older 'running' rows: they are orphans of a crash.
            cur.execute(
                """UPDATE sync_runs SET status = 'failed', finished_at = NOW(),
                       error = 'interrupted (worker restart or crash)'
                   WHERE source = %s AND status = 'running'""",
                (OSM_SOURCE,),
            )
            cur.execute("SELECT cursor, totals FROM sync_state WHERE source = %s", (OSM_SOURCE,))
            row = cur.fetchone()
            cursor_before = (row[0] if row else {}) or {}
            cur.execute(
                "INSERT INTO sync_runs (source, trigger, from_cursor) VALUES (%s, %s, %s) RETURNING id",
                (OSM_SOURCE, trigger, json.dumps(cursor_before)),
            )
            run_id = cur.fetchone()[0]
        conn.commit()
        _set_status(conn, "checking", heartbeat_at=started)

        try:
            remote = remote_fetch(cfg)
        except osm_adapter.SourceUnavailable as exc:
            _record_failure(cfg, run_id, f"source unavailable: {exc}")
            return JobOutcome("failed", run_id, error=str(exc))
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE sync_state SET last_check_at = NOW(), last_check_error = NULL,
                       source_head_sequence = %s, source_head_timestamp = %s,
                       source_head_published_at = %s, updated_at = NOW() WHERE source = %s""",
                (remote.sequence, remote.timestamp, remote.published_at, OSM_SOURCE),
            )
            local = osm_adapter.read_local_state(cur)
        conn.commit()

        if local is None:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sync_runs SET status = 'failed', finished_at = NOW(),"
                    " error = 'no baseline import yet (run bootstrap)' WHERE id = %s", (run_id,)
                )
            conn.commit()
            _set_status(conn, "awaiting_setup", heartbeat_at=None)
            return JobOutcome("awaiting_setup", run_id, error="no baseline import")

        if local.sequence < remote.sequence:
            _set_status(conn, "syncing", heartbeat_at=utcnow())
            hb_thread = threading.Thread(target=_heartbeat, args=(cfg.dsn, hb_stop), daemon=True)
            hb_thread.start()
            try:
                replication_update(cfg, stop)
            except osm_adapter.ReplicationError as exc:
                _record_failure(cfg, run_id, f"replication update failed: {exc}")
                return JobOutcome("failed", run_id, error=str(exc))
            with conn.cursor() as cur:
                local = osm_adapter.read_local_state(cur) or local
            conn.commit()

        cursor_seq = cursor_before.get("sequence")
        needs_apply = force_apply or cursor_seq is None or int(cursor_seq) != local.sequence \
            or trigger in {"bootstrap", "reconcile"}
        if not needs_apply:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE sync_runs SET status = 'no_changes', finished_at = NOW(), to_cursor = %s,
                           source_data_at = %s, source_published_at = %s,
                           duration_seconds = EXTRACT(EPOCH FROM (NOW() - started_at))
                       WHERE id = %s""",
                    (json.dumps(cursor_before), local.timestamp, remote.published_at, run_id),
                )
                cur.execute(
                    """UPDATE sync_state SET status = 'idle', last_success_at = NOW(), last_error = NULL,
                           consecutive_failures = 0, next_retry_at = NULL, heartbeat_at = NULL,
                           manual_requested_at = NULL, updated_at = NOW() WHERE source = %s""",
                    (OSM_SOURCE,),
                )
                notify(cur, NOTIFY_CHANNEL_SYNC, {"source": OSM_SOURCE, "status": "idle"})
            conn.commit()
            return JobOutcome("no_changes", run_id)

        _set_status(conn, "syncing", heartbeat_at=utcnow())
        try:
            with conn.cursor() as cur:
                result = apply_osm_sync(
                    cur,
                    max_deactivate_fraction=cfg.osm.max_deactivate_fraction,
                    force_admin_rebuild=force_admin_rebuild,
                    allow_mass_deactivation=(trigger == "bootstrap"),
                )
                cursor_after = {"sequence": local.sequence,
                                "timestamp": local.timestamp.isoformat() if local.timestamp else None}
                cur.execute("SELECT clock_timestamp()")
                committed_at = cur.fetchone()[0]
                latency = None
                if remote.published_at is not None and local.sequence == remote.sequence:
                    latency = max((committed_at - remote.published_at).total_seconds(), 0.0)
                cur.execute(
                    """UPDATE sync_runs SET status = %s, finished_at = clock_timestamp(),
                           to_cursor = %s, created_count = %s, updated_count = %s, deleted_count = %s,
                           skipped_count = %s, revision = %s, source_data_at = %s,
                           source_published_at = %s, committed_at = %s, ingest_latency_seconds = %s,
                           duration_seconds = EXTRACT(EPOCH FROM (clock_timestamp() - started_at)),
                           details = %s WHERE id = %s""",
                    ("success" if result.changed else "no_changes", json.dumps(cursor_after),
                     result.created, result.updated, result.deleted, result.skipped_invalid,
                     result.revision, local.timestamp, remote.published_at, committed_at, latency,
                     json.dumps({"raw_rows": result.raw_rows, "unchanged": result.unchanged,
                                 "trigger": trigger}), run_id),
                )
                cur.execute("SELECT totals FROM sync_state WHERE source = %s FOR UPDATE", (OSM_SOURCE,))
                totals = (cur.fetchone()[0] or {})
                for key, value in (("created", result.created), ("updated", result.updated),
                                   ("deleted", result.deleted), ("skipped", result.skipped_invalid)):
                    totals[key] = int(totals.get(key, 0)) + value
                if trigger in {"reconcile", "bootstrap"}:
                    totals["last_reconcile_at"] = committed_at.isoformat()
                cur.execute(
                    """UPDATE sync_state SET status = 'idle', cursor = %s, totals = %s,
                           last_source_data_at = %s,
                           last_commit_at = CASE WHEN %s THEN %s ELSE last_commit_at END,
                           last_success_at = %s, last_error = NULL, last_check_error = NULL,
                           consecutive_failures = 0, next_retry_at = NULL, heartbeat_at = NULL,
                           manual_requested_at = NULL, updated_at = NOW() WHERE source = %s""",
                    (json.dumps(cursor_after), json.dumps(totals), local.timestamp,
                     result.changed, committed_at, committed_at, OSM_SOURCE),
                )
                if result.changed:
                    notify(cur, NOTIFY_CHANNEL_DATA, {
                        "revision": result.revision, "source": OSM_SOURCE,
                        "created": result.created, "updated": result.updated,
                        "deleted": result.deleted, "committed_at": committed_at.isoformat(),
                        "source_data_at": local.timestamp.isoformat() if local.timestamp else None,
                    })
                notify(cur, NOTIFY_CHANNEL_SYNC, {"source": OSM_SOURCE, "status": "idle"})
            conn.commit()  # data + checkpoint + notifications become visible atomically
            logger.info("sync ok: +%d ~%d -%d (revision %s)", result.created, result.updated,
                        result.deleted, result.revision)
            return JobOutcome("success" if result.changed else "no_changes", run_id, result, None, latency)
        except Exception as exc:  # noqa: BLE001
            conn.rollback()   # master tables AND checkpoint roll back together
            logger.exception("sync apply failed; checkpoint not advanced")
            _record_failure(cfg, run_id, f"master sync failed: {exc}")
            return JobOutcome("failed", run_id, error=str(exc))
    finally:
        hb_stop.set()
        if hb_thread is not None:
            hb_thread.join(timeout=2)
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
