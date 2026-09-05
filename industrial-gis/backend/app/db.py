from __future__ import annotations

import logging

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import get_settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return a lazy-initialised SQLAlchemy engine (connection pool)."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            echo=False,
            future=True,
            pool_pre_ping=True,  # detect stale connections before reuse
            pool_size=5,
            max_overflow=10,
        )
        logger.info("Database engine created — url=%s", _redact_url(settings.database_url))
    return _engine


def get_session() -> Session:
    """Return a new SQLAlchemy session. Caller is responsible for closing it."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
            future=True,
        )
    return _SessionLocal()


def check_db_connection() -> bool:
    """Return True if the database is reachable; False otherwise."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Database connection check failed: %s", exc)
        return False


def _redact_url(url: str) -> str:
    """Replace the password in a database URL with ***."""
    try:
        from sqlalchemy.engine import make_url
        u = make_url(url)
        return str(u.set(password="***"))
    except Exception:  # noqa: BLE001
        return "<redacted>"
