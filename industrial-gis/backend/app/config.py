"""
Application settings — read once from environment / .env on first import.

DATA_MODE controls the active data repository:
  synthetic  (default) — in-memory synthetic dataset, no database required
  postgis              — queries the industrial_sites PostGIS table

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

    __slots__ = ("database_url", "data_mode", "allowed_origins")

    def __init__(self) -> None:
        self.database_url: str = os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg2://postgres:postgres@localhost:5432/industrial_gis",
        )

        raw_mode = os.getenv("DATA_MODE", "synthetic").strip().lower()
        if raw_mode not in _VALID_MODES:
            raise ValueError(
                f"DATA_MODE must be one of {sorted(_VALID_MODES)!r}, got {raw_mode!r}. "
                "Check your .env file or environment."
            )
        self.data_mode: DataMode = raw_mode  # type: ignore[assignment]

        raw_origins = os.getenv("ALLOWED_ORIGINS", "").strip()
        if raw_origins:
            self.allowed_origins: list[str] = [o.strip() for o in raw_origins.split(",") if o.strip()]
        else:
            # Default to permissive in development; tighten in production via ALLOWED_ORIGINS
            self.allowed_origins = ["*"]

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
