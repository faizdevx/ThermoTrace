"""Database helpers: connections, migrations, advisory locks, notifications."""
from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg2

logger = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
BASE_SQL = ("schema.sql", "functions.sql", "indexes.sql")
NOTIFY_CHANNEL_DATA = "thermotrace_changes"
NOTIFY_CHANNEL_SYNC = "thermotrace_sync"


def connect(dsn: str, application_name: str = "thermotrace-sync"):
    return psycopg2.connect(dsn, application_name=application_name, connect_timeout=10)


def advisory_key(name: str) -> int:
    """Stable signed 63-bit key for pg advisory locks."""
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


@contextmanager
def advisory_lock(dsn: str, name: str) -> Iterator[bool]:
    """Hold a session-level advisory lock on its OWN connection.

    Yields True when acquired, False when another process holds it.
    The lock is released when the connection closes, so a crashed worker can
    never leave a stale lock behind.
    """
    conn = connect(dsn, application_name=f"thermotrace-lock:{name}")
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (advisory_key(name),))
            acquired = bool(cur.fetchone()[0])
        try:
            yield acquired
        finally:
            if acquired:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (advisory_key(name),))
    finally:
        conn.close()


def notify(cur, channel: str, payload: dict) -> None:
    """Queue a NOTIFY; PostgreSQL delivers it only if the transaction commits."""
    cur.execute("SELECT pg_notify(%s, %s)", (channel, json.dumps(payload, default=str)))


def run_migrations(dsn: str, sql_dir: Path = SQL_DIR) -> list[str]:
    """Apply base schema (idempotent) then numbered migrations once each.

    Returns the migration file names applied by this call.
    """
    conn = connect(dsn, application_name="thermotrace-migrate")
    applied: list[str] = []
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (advisory_key("thermotrace:migrate"),))
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                cur.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations ("
                    " name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
                )
                for name in BASE_SQL:
                    path = sql_dir / name
                    if path.exists():
                        cur.execute(path.read_text(encoding="utf-8"))
                        logger.info("base sql ok: %s", name)
                for path in sorted((sql_dir / "migrations").glob("*.sql")):
                    cur.execute("SELECT 1 FROM schema_migrations WHERE name = %s", (path.name,))
                    if cur.fetchone():
                        continue
                    conn.autocommit = False
                    try:
                        cur.execute(path.read_text(encoding="utf-8"))
                        cur.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
                    finally:
                        conn.autocommit = True
                    applied.append(path.name)
                    logger.info("migration applied: %s", path.name)
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s)", (advisory_key("thermotrace:migrate"),))
    finally:
        conn.close()
    return applied
