"""
Application settings — read once from environment / .env on first import.

DATA_MODE controls the active data repository:
  postgis    (default) — queries the live PostGIS dataset maintained by the sync worker
  synthetic            — in-memory DEMO/TEST dataset (~30 invented sites), no database.
                         Never use it as an operational dataset; the API labels it.

All other env vars are documented in .env.example.
"""
from __future__ import annotations

import logging
import os
from typing import Literal

# Load .env when present (python-dotenv is in requirements.txt)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)

DataMode = Literal["synthetic", "postgis"]

_VALID_MODES: frozenset[str] = frozenset({"synthetic", "postgis"})


class Settings:
    """Immutable application settings, populated from environment variables."""

    __slots__ = ("database_url", "data_mode", "allowed_origins", "admin_token", "analyst_token", "demo_readonly", "statement_timeout_ms", "pool_size",
                 "auth_mode", "session_idle_minutes", "session_max_hours", "cookie_secure", "proxy_secret", "proxy_user_header",
                 "proxy_groups_header", "proxy_admin_group", "proxy_analyst_group", "trusted_proxy_hops", "rate_limit",
                 "rate_general_per_min", "rate_heavy_per_min", "rate_login_per_min", "offline_mode")

    def __init__(self) -> None:
        raw_mode = os.getenv("DATA_MODE", "postgis").strip().lower()
        if raw_mode not in _VALID_MODES:
            raise ValueError(
                f"DATA_MODE must be one of {sorted(_VALID_MODES)!r}, got {raw_mode!r}. "
                "Check your .env file or environment."
            )
        self.data_mode: DataMode = raw_mode  # type: ignore[assignment]

        self.database_url: str = os.getenv("DATABASE_URL", "").strip()
        if self.data_mode == "postgis" and not self.database_url:
            raise ValueError(
                "DATABASE_URL is required when DATA_MODE=postgis "
                "(e.g. postgresql+psycopg2://user:pass@host:5432/db). "
                "Set DATA_MODE=synthetic only for the labelled offline demo."
            )
        self.admin_token: str = os.getenv("ADMIN_TOKEN", "").strip()
        self.analyst_token: str = os.getenv("ANALYST_TOKEN", "").strip()
        # public demo: every request that could change anything is refused, whatever tokens are sent
        self.demo_readonly: bool = os.getenv("DEMO_READONLY", "").strip().lower() in ("1", "true", "yes")
        # authentication: off (legacy tokens, open reading) | local (accounts + cookie sessions) | proxy (identity from an OIDC
        # reverse proxy such as oauth2-proxy, trusted only with the shared secret)
        mode = os.getenv("AUTH_MODE", "off").strip().lower()
        if mode not in ("off", "local", "proxy"):
            raise ValueError("AUTH_MODE must be off, local or proxy")
        if mode != "off" and self.data_mode != "postgis":
            raise ValueError("AUTH_MODE local/proxy needs DATA_MODE=postgis (users, sessions and the audit log live in PostGIS)")
        self.auth_mode: str = mode
        self.session_idle_minutes: int = int(os.getenv("SESSION_IDLE_MINUTES", "60"))
        self.session_max_hours: int = int(os.getenv("SESSION_MAX_HOURS", "12"))
        self.cookie_secure: bool = os.getenv("COOKIE_SECURE", "auto").strip().lower() in ("1", "true", "yes")
        self.proxy_secret: str = os.getenv("AUTH_PROXY_SECRET", "").strip()
        self.proxy_user_header: str = os.getenv("AUTH_PROXY_USER_HEADER", "X-Auth-Request-User")
        self.proxy_groups_header: str = os.getenv("AUTH_PROXY_GROUPS_HEADER", "X-Auth-Request-Groups")
        self.proxy_admin_group: str = os.getenv("AUTH_PROXY_ADMIN_GROUP", "thermotrace-admin")
        self.proxy_analyst_group: str = os.getenv("AUTH_PROXY_ANALYST_GROUP", "thermotrace-analyst")
        if mode == "proxy" and len(self.proxy_secret) < 16:
            raise ValueError("AUTH_MODE=proxy needs AUTH_PROXY_SECRET (16+ characters), shared with the reverse proxy")
        self.trusted_proxy_hops: int = int(os.getenv("TRUSTED_PROXY_HOPS", "0"))
        self.rate_limit: bool = os.getenv("RATE_LIMIT", "true").strip().lower() in ("1", "true", "yes")
        self.rate_general_per_min: int = int(os.getenv("RATE_GENERAL_PER_MIN", "600"))
        self.rate_heavy_per_min: int = int(os.getenv("RATE_HEAVY_PER_MIN", "12"))
        self.rate_login_per_min: int = int(os.getenv("RATE_LOGIN_PER_MIN", "10"))
        # offline mode: no outbound connection except the FIRMS hosts (enforced by src.netguard)
        self.offline_mode: bool = os.getenv("OFFLINE_MODE", "").strip().lower() in ("1", "true", "yes")
        self.statement_timeout_ms: int = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000"))
        self.pool_size: int = int(os.getenv("DB_POOL_SIZE", "5"))

        raw_origins = os.getenv("ALLOWED_ORIGINS", "").strip()
        if raw_origins:
            self.allowed_origins: list[str] = [o.strip() for o in raw_origins.split(",") if o.strip()]
        else:
            # Explicit local-dev origins; set ALLOWED_ORIGINS for any other deployment.
            self.allowed_origins = ["http://localhost:5173", "http://localhost:8080"]

        logger.info(
            "Settings loaded — data_mode=%s, allowed_origins=%s",
            self.data_mode,
            self.allowed_origins,
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the application settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
