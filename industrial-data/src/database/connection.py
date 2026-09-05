from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def get_database_url(config: dict | None = None) -> str:
    env_name = None
    if config:
        env_name = config.get("database", {}).get("url_env")

    if env_name:
        value = os.getenv(env_name)
        if value:
            return value

    value = os.getenv("DATABASE_URL")
    if value:
        return value

    raise ValueError("DATABASE_URL is not configured")


def create_database_engine(database_url: str, echo: bool = False) -> Engine:
    return create_engine(database_url, future=True, echo=echo)