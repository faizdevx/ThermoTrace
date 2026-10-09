"""PostGIS API tests: run only with DATA_MODE=postgis and TEST_DATABASE_URL (admin URL).

A throw-away database is created, migrated with the real sync migrations, and seeded with a
small controlled fixture (clearly fake, in the 'Testland' state) - never the operational data.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest

ADMIN_URL = os.getenv("TEST_DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")

if os.getenv("DATA_MODE") == "postgis" and ADMIN_URL:
    import psycopg2

    _name = f"tt_api_{uuid.uuid4().hex[:8]}"
    _admin = psycopg2.connect(ADMIN_URL)
    _admin.autocommit = True
    with _admin.cursor() as _cur:
        _cur.execute(f'CREATE DATABASE "{_name}"')
    _parsed = urlparse(ADMIN_URL)
    TEST_DSN = urlunparse(_parsed._replace(path="/" + _name))
    os.environ["DATABASE_URL"] = TEST_DSN.replace("postgresql://", "postgresql+psycopg2://")
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "industrial-data"))

pytestmark = pytest.mark.skipif(
    os.getenv("DATA_MODE") != "postgis" or not ADMIN_URL, reason="needs DATA_MODE=postgis + TEST_DATABASE_URL")


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    if os.getenv("DATA_MODE") == "postgis" and ADMIN_URL:
        with _admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{_name}" WITH (FORCE)')
        _admin.close()
