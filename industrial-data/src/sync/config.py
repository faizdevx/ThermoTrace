"""Environment-driven configuration for the ThermoTrace sync worker."""
from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(ValueError):
    """Raised for missing or invalid configuration."""


def libpq_dsn(url: str) -> str:
    """Convert a SQLAlchemy-style URL to a libpq-compatible URL."""
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix):]
    return url


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class OsmSourceConfig:
    source: str
    region: str                    # Geofabrik path, e.g. asia/india/central-zone
    extract_url: str               # baseline .osm.pbf
    replication_url: str           # directory containing state.txt + diffs
    cadence: str                   # human description of how often the SOURCE publishes
    poll_interval_seconds: int
    freshness_target_seconds: int
    max_diff_size_mb: int
    update_timeout_seconds: int
    style_path: str
    flat_nodes_path: str
    cache_size_mb: int
    max_deactivate_fraction: float
    enabled: bool


@dataclass(frozen=True)
class SyncConfig:
    database_url: str
    osm: OsmSourceConfig
    user_agent: str
    http_timeout_seconds: int
    retry_base_seconds: int
    retry_max_seconds: int
    tick_seconds: int
    reconcile_every_seconds: int
    government_enabled: bool

    @property
    def dsn(self) -> str:
        return libpq_dsn(self.database_url)


def load_config() -> SyncConfig:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise ConfigError("DATABASE_URL is not set (see .env.example)")

    region = os.getenv("OSM_REGION", "asia/india/north-eastern-zone").strip("/")
    base = os.getenv("OSM_GEOFABRIK_BASE", "https://download.geofabrik.de").rstrip("/")
    extract_url = os.getenv("OSM_EXTRACT_URL") or f"{base}/{region}-latest.osm.pbf"
    replication_url = os.getenv("OSM_REPLICATION_URL") or f"{base}/{region}-updates"
    cadence = os.getenv("OSM_SOURCE_CADENCE", "daily (Geofabrik extract diffs)")

    osm = OsmSourceConfig(
        source="openstreetmap",
        region=region,
        extract_url=extract_url,
        replication_url=replication_url.rstrip("/"),
        cadence=cadence,
        poll_interval_seconds=_int("OSM_POLL_INTERVAL_SECONDS", 300, 5),
        freshness_target_seconds=_int("OSM_FRESHNESS_TARGET_SECONDS", 36 * 3600, 60),
        max_diff_size_mb=_int("OSM_MAX_DIFF_SIZE_MB", 50, 1),
        update_timeout_seconds=_int("OSM_UPDATE_TIMEOUT_SECONDS", 3600, 30),
        style_path=os.getenv("OSM_STYLE_PATH", "/opt/thermotrace/thermotrace.lua"),
        flat_nodes_path=os.getenv("OSM_FLAT_NODES_PATH", "").strip(),
        cache_size_mb=_int("OSM_CACHE_MB", 1024, 64),
        max_deactivate_fraction=_float("SYNC_MAX_DEACTIVATE_FRACTION", 0.2, 0.0),
        enabled=_bool("OSM_SYNC_ENABLED", True),
    )
    return SyncConfig(
        database_url=database_url,
        osm=osm,
        user_agent=os.getenv(
            "SYNC_USER_AGENT",
            "ThermoTrace-sync/1.0 (+https://github.com/faizdevx/ThermoTrace)",
        ),
        http_timeout_seconds=_int("SYNC_HTTP_TIMEOUT_SECONDS", 30, 1),
        retry_base_seconds=_int("SYNC_RETRY_BASE_SECONDS", 30, 1),
        retry_max_seconds=_int("SYNC_RETRY_MAX_SECONDS", 3600, 1),
        tick_seconds=_int("SYNC_TICK_SECONDS", 5, 1),
        reconcile_every_seconds=_int("SYNC_RECONCILE_EVERY_SECONDS", 24 * 3600, 60),
        government_enabled=_bool("GOV_SYNC_ENABLED", True),
    )
