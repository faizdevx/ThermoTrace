"""Alert inbox data layer: thin wrapper over ``src.alerts.store`` (one implementation shared with the worker)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:                                        # Docker image: src/alerts is copied next to the backend
    from src.alerts import notify, store
except ImportError:                         # development checkout: industrial-data sits next to industrial-gis
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "industrial-data"))
    from src.alerts import notify, store

from backend.app.db import get_engine

DECISIONS = tuple(store.DECISIONS)


def _conn():
    return get_engine().raw_connection()


def list_alerts(status: str | None, limit: int) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            return store.list_alerts(cur, status, limit)
    finally:
        conn.close()


def get_alert(alert_id: int) -> dict[str, Any] | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            return store.get_alert(cur, alert_id)
    finally:
        conn.close()


def decide(alert_id: int, decision: str, note: str | None, analyst: str | None) -> dict[str, Any]:
    conn = _conn()
    try:
        return store.record_decision(conn, alert_id, decision, note, analyst)
    finally:
        conn.close()


def channels(alerts: list[dict[str, Any]] | None = None) -> list[str]:
    """Channels this server is configured for; when the API has no credentials of its own (the worker holds them),
    the channels that actually delivered recent alerts."""
    configured = notify.configured()
    if configured or not alerts:
        return configured
    return sorted({ch for a in alerts for ch in (a.get("delivery") or {})})
