"""Fixtures for the sync lifecycle tests.

These tests need a real PostGIS server (spatial SQL is the thing under test) but never touch the
operational database: each test module gets a throw-away database created from TEST_DATABASE_URL
(an admin URL, e.g. postgresql://postgres:pw@localhost:5433/postgres). They are skipped when it is not set.
They never call public services: replication servers and osm2pgsql are replaced by local fakes.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse

import psycopg2
import pytest

from src.sync import jobs, osm_adapter
from src.sync.config import OsmSourceConfig, SyncConfig
from src.sync.db import connect, run_migrations

ADMIN_URL = os.getenv("TEST_DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set (needs PostGIS)")

RAW_DDL = """
CREATE TABLE IF NOT EXISTS osm_industrial_raw (
    osm_type char(1) NOT NULL, osm_id bigint NOT NULL, version int4, changeset int8,
    osm_timestamp timestamptz, tags jsonb, geom geometry(Geometry, 4326) NOT NULL,
    PRIMARY KEY (osm_type, osm_id));
CREATE TABLE IF NOT EXISTS osm2pgsql_properties (property text PRIMARY KEY, value text NOT NULL);
"""


def _with_db(url: str, dbname: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path="/" + dbname))


@pytest.fixture(scope="module")
def dsn():
    if not ADMIN_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"tt_test_{uuid.uuid4().hex[:8]}"
    admin = psycopg2.connect(ADMIN_URL)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    test_dsn = _with_db(ADMIN_URL, name)
    run_migrations(test_dsn)
    conn = connect(test_dsn)
    with conn.cursor() as cur:
        cur.execute(RAW_DDL)
        # A fixture "state" and "district" so spatial assignment can be asserted.
        cur.execute("""INSERT INTO states (name, geometry, source, source_id)
            VALUES ('Testland', ST_Multi(ST_GeomFromText('POLYGON((0 0,0 10,10 10,10 0,0 0))',4326)), 'fixture', 's1')""")
        cur.execute("""INSERT INTO districts (name, geometry, source, source_id)
            VALUES ('Testdistrict', ST_Multi(ST_GeomFromText('POLYGON((0 0,0 5,5 5,5 0,0 0))',4326)), 'fixture', 'd1')""")
    conn.commit()
    conn.close()
    yield test_dsn
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    admin.close()


@pytest.fixture()
def cfg(dsn, monkeypatch) -> SyncConfig:
    monkeypatch.setenv("DATABASE_URL", dsn)
    osm = OsmSourceConfig(
        source="openstreetmap", region="fixture/region", extract_url="http://localhost/none.pbf",
        replication_url="http://localhost/none", cadence="fixture", poll_interval_seconds=60,
        freshness_target_seconds=3600, max_diff_size_mb=1, update_timeout_seconds=30,
        style_path="/dev/null", flat_nodes_path="", cache_size_mb=64,
        max_deactivate_fraction=0.2, enabled=True)
    return SyncConfig(database_url=dsn, osm=osm, user_agent="test", http_timeout_seconds=5,
                      retry_base_seconds=10, retry_max_seconds=60, tick_seconds=1,
                      reconcile_every_seconds=3600, government_enabled=True)


@pytest.fixture()
def db(cfg):
    """Clean slate for every test: empty data, fresh sync_state, empty raw table."""
    conn = connect(cfg.dsn)
    with conn.cursor() as cur:
        cur.execute("""TRUNCATE industrial_sites, osm_industries, site_source_records, sync_runs,
                       sync_state, osm_industrial_raw, osm2pgsql_properties RESTART IDENTITY CASCADE""")
        cur.execute("UPDATE data_revision SET revision = 0")
    conn.commit()
    jobs.register_sources(cfg)
    yield conn
    conn.rollback()
    conn.close()


BASE_TIME = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def set_replication(conn, sequence: int, timestamp: datetime | None = None) -> None:
    timestamp = timestamp or BASE_TIME + timedelta(hours=sequence)
    with conn.cursor() as cur:
        for prop, value in (("replication_sequence_number", str(sequence)),
                            ("replication_timestamp", timestamp.isoformat()),
                            ("replication_base_url", "http://localhost/none")):
            cur.execute("""INSERT INTO osm2pgsql_properties VALUES (%s, %s)
                           ON CONFLICT (property) DO UPDATE SET value = EXCLUDED.value""", (prop, value))
    conn.commit()


def put_raw(conn, osm_type: str, osm_id: int, tags: dict, wkt: str, version: int = 1) -> None:
    import json
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO osm_industrial_raw (osm_type, osm_id, version, changeset, osm_timestamp, tags, geom)
               VALUES (%s, %s, %s, 1, %s, %s::jsonb, ST_GeomFromText(%s, 4326))
               ON CONFLICT (osm_type, osm_id) DO UPDATE SET version = EXCLUDED.version,
                   tags = EXCLUDED.tags, geom = EXCLUDED.geom, osm_timestamp = EXCLUDED.osm_timestamp""",
            (osm_type, osm_id, version, BASE_TIME, json.dumps(tags), wkt))
    conn.commit()


def delete_raw(conn, osm_type: str, osm_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM osm_industrial_raw WHERE osm_type = %s AND osm_id = %s", (osm_type, osm_id))
    conn.commit()


def fake_remote(sequence: int, timestamp: datetime | None = None, published: datetime | None = None):
    ts = timestamp or BASE_TIME + timedelta(hours=sequence)

    def fetch(_cfg):
        return osm_adapter.RemoteState(sequence, ts, published or ts + timedelta(minutes=5))
    return fetch


def scalar(conn, sql: str, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    conn.commit()
    return row[0] if row else None
