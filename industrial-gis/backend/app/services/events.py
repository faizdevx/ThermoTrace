"""Change notifications: PostgreSQL LISTEN/NOTIFY -> Server-Sent Events.

The sync worker issues ``pg_notify`` INSIDE the transaction that commits new data, so a
notification is delivered only after the data is visible to readers. Payloads carry the
new data revision and counters - never records.
"""
from __future__ import annotations

import asyncio
import json
import logging
import select
import threading
import time
from typing import Any

import psycopg2

from backend.app.config import get_settings

logger = logging.getLogger(__name__)

CHANNELS = ("thermotrace_changes", "thermotrace_sync")


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.connected = False
        self.last_revision_event: dict[str, Any] | None = None

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._loop = loop
        self._stop.clear()
        self._thread = threading.Thread(target=self._listen_forever, name="pg-listen", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ── subscribers ──────────────────────────────────────────────────────
    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _publish(self, event: str, payload: dict[str, Any]) -> None:
        message = {"event": event, "data": payload}
        if event == "revision":
            self.last_revision_event = payload
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:  # slow consumer: drop it, it will reconnect and resync
                self._subscribers.discard(queue)

    # ── snapshot watcher (thermal data published as files) ───────────────
    def start_file_watcher(self, loop, mtime_fn, info_fn) -> None:
        def watch() -> None:
            last = mtime_fn()
            while not self._stop.wait(10):
                current = mtime_fn()
                if current is not None and current != last:
                    last = current
                    payload = {"kind": "thermal", **info_fn(), "received_at_server": time.time()}
                    loop.call_soon_threadsafe(self._publish, "thermal", payload)
        threading.Thread(target=watch, name="thermal-watch", daemon=True).start()

    # ── listener thread ──────────────────────────────────────────────────
    def _listen_forever(self) -> None:
        dsn = get_settings().database_url.replace("postgresql+psycopg2://", "postgresql://")
        backoff = 1.0
        while not self._stop.is_set():
            conn = None
            try:
                conn = psycopg2.connect(dsn, application_name="thermotrace-api-listen", connect_timeout=5)
                conn.autocommit = True
                with conn.cursor() as cur:
                    for channel in CHANNELS:
                        cur.execute(f"LISTEN {channel}")
                self.connected = True
                backoff = 1.0
                logger.info("LISTEN connected on %s", ", ".join(CHANNELS))
                while not self._stop.is_set():
                    ready, _, _ = select.select([conn], [], [], 5.0)
                    if not ready:
                        continue
                    conn.poll()
                    while conn.notifies:
                        note = conn.notifies.pop(0)
                        try:
                            payload = json.loads(note.payload)
                        except ValueError:
                            payload = {"raw": note.payload}
                        event = "revision" if note.channel == "thermotrace_changes" else "sync"
                        if payload.get("kind") == "thermal":
                            event = "thermal"
                        payload["received_at_server"] = time.time()
                        if self._loop is not None:
                            self._loop.call_soon_threadsafe(self._publish, event, payload)
            except Exception as exc:  # noqa: BLE001
                self.connected = False
                logger.warning("LISTEN connection lost (%s); retrying in %.0fs", exc, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                self.connected = False
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass


hub = EventHub()


def format_sse(event: str, data: dict[str, Any], event_id: str | int | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, default=str)}")
    return "\n".join(lines) + "\n\n"
