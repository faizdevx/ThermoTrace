"""Update-lifecycle tests: new / changed / deleted source objects, idempotency, checkpoints,
failure recovery, locking, notifications. Real PostGIS, fake replication source (deterministic)."""
from __future__ import annotations

import select
import threading
import time
import tracemalloc
from datetime import timedelta

import pytest

from src.sync import jobs, osm_adapter
from src.sync.db import NOTIFY_CHANNEL_DATA, advisory_key, advisory_lock, connect
from src.sync.jobs import OSM_LOCK, run_osm_job
from src.sync.master import MassDeactivationError, apply_osm_sync

from .conftest import (BASE_TIME, delete_raw, fake_remote, put_raw, scalar, set_replication)

pytestmark = pytest.mark.usefixtures("db")

FACTORY = {"industrial": "factory", "name": "Alpha Works", "operator": "Alpha Ltd"}
SQUARE = "POLYGON((1 1,1 2,2 2,2 1,1 1))"


def noop_update(_cfg, _stop=None):
    return ""


def sync_once(cfg, **kw):
    kw.setdefault("remote_fetch", fake_remote(1))
    kw.setdefault("replication_update", noop_update)
    return run_osm_job(cfg, kw.pop("trigger", "manual"), **kw)


def active_sites(db):
    return scalar(db, "SELECT COUNT(*) FROM industrial_sites WHERE is_active")


def revision(db):
    return scalar(db, "SELECT revision FROM data_revision WHERE id = 1")


# 1 ── new source record -----------------------------------------------------
def test_new_record_creates_site_with_provenance_and_admin_area(cfg, db):
    set_replication(db, 1)
    put_raw(db, "W", 1001, FACTORY, SQUARE)
    outcome = sync_once(cfg)
    assert outcome.status == "success" and outcome.result.created == 1
    row = None
    with db.cursor() as cur:
        cur.execute("""SELECT site_id, name, industry_type, state, district, source_ref, source_url,
                              evidence_level, operational_status, match_method, is_active
                       FROM industrial_sites""")
        row = cur.fetchone()
    db.commit()
    assert row[1] == "Alpha Works" and row[2] == "Manufacturing"
    assert (row[3], row[4]) == ("Testland", "Testdistrict")
    assert row[5] == "way/1001" and row[6] == "https://www.openstreetmap.org/way/1001"
    assert row[7] == "high"            # name + operator + specific type
    assert row[8] == "mapped"           # never "operational" without evidence
    assert row[9] == "osm_only" and row[10] is True
    assert revision(db) == 1


# 2 ── changed source record ---------------------------------------------------
def test_changed_record_updates_in_place_and_keeps_site_id(cfg, db):
    set_replication(db, 1)
    put_raw(db, "W", 1001, FACTORY, SQUARE)
    sync_once(cfg)
    site_id = scalar(db, "SELECT site_id FROM industrial_sites")
    put_raw(db, "W", 1001, {**FACTORY, "name": "Alpha Works Renamed"}, "POLYGON((1 1,1 3,3 3,3 1,1 1))", version=2)
    set_replication(db, 2)
    outcome = sync_once(cfg, remote_fetch=fake_remote(2))
    assert outcome.result.updated == 1 and outcome.result.created == 0
    assert active_sites(db) == 1
    assert scalar(db, "SELECT site_id FROM industrial_sites") == site_id          # stable identity
    assert scalar(db, "SELECT name FROM industrial_sites") == "Alpha Works Renamed"
    assert scalar(db, "SELECT osm_version FROM industrial_sites") == 2
    assert revision(db) == 2


# 3 ── deleted / de-classified source object -------------------------------------
def test_deleted_record_is_deactivated_not_destroyed(cfg, db):
    set_replication(db, 1)
    for i in range(3):
        put_raw(db, "W", 2000 + i, {"industrial": "factory", "name": f"F{i}"}, SQUARE)
    sync_once(cfg)
    delete_raw(db, "W", 2001)       # object deleted (or industrial tag removed) in OSM
    set_replication(db, 2)
    outcome = sync_once(cfg, remote_fetch=fake_remote(2))
    assert outcome.result.deleted == 1
    assert active_sites(db) == 2
    assert scalar(db, "SELECT COUNT(*) FROM industrial_sites") == 3                  # row kept for provenance
    assert scalar(db, "SELECT deleted_at IS NOT NULL FROM industrial_sites WHERE source_ref='way/2001'")
    # the object coming back (re-tagged) reactivates the SAME site
    put_raw(db, "W", 2001, {"industrial": "factory", "name": "F1"}, SQUARE)
    set_replication(db, 3)
    sync_once(cfg, remote_fetch=fake_remote(3))
    assert active_sites(db) == 3 and scalar(db, "SELECT COUNT(*) FROM industrial_sites") == 3


def test_invalid_geometry_is_skipped_and_counted(cfg, db):
    set_replication(db, 1)
    put_raw(db, "W", 1, FACTORY, SQUARE)
    put_raw(db, "W", 2, FACTORY, "POLYGON((0 0,0 0,0 0,0 0))")      # degenerate
    outcome = sync_once(cfg)
    assert outcome.result.created == 1 and outcome.result.skipped_invalid == 1


# 4 ── duplicates / unchanged data ------------------------------------------------
def test_replaying_same_state_creates_no_duplicates_or_revisions(cfg, db):
    set_replication(db, 1)
    put_raw(db, "W", 1001, FACTORY, SQUARE)
    put_raw(db, "N", 1002, {"man_made": "works"}, "POINT(2 2)")
    sync_once(cfg)
    rev = revision(db)
    for _ in range(3):
        outcome = sync_once(cfg, trigger="reconcile")        # forces a full pass every time
        assert outcome.status == "no_changes"
        assert (outcome.result.created, outcome.result.updated, outcome.result.deleted) == (0, 0, 0)
    assert revision(db) == rev and active_sites(db) == 2
    assert scalar(db, "SELECT COUNT(*) FROM osm_industries") == 2


def test_site_id_depends_only_on_osm_identity(cfg, db):
    import uuid
    set_replication(db, 1)
    put_raw(db, "W", 77, FACTORY, SQUARE)
    sync_once(cfg)
    expected = str(uuid.uuid5(uuid.NAMESPACE_URL, "industrial-site:v2:osm:way:77"))
    assert scalar(db, "SELECT site_id FROM industrial_sites") == expected


# 5/7 ── checkpoint semantics ---------------------------------------------------------
def test_failed_transaction_does_not_advance_checkpoint_then_recovers(cfg, db, monkeypatch):
    set_replication(db, 1)
    put_raw(db, "W", 1, FACTORY, SQUARE)
    sync_once(cfg)                                  # baseline: cursor sequence 1
    assert scalar(db, "SELECT cursor->>'sequence' FROM sync_state WHERE source = 'openstreetmap'") == "1"

    put_raw(db, "W", 2, FACTORY, SQUARE)
    set_replication(db, 2)
    real_apply = jobs.apply_osm_sync

    def apply_then_crash(cur, **kw):
        real_apply(cur, **kw)                       # data written inside the open transaction ...
        raise RuntimeError("simulated crash before commit")

    monkeypatch.setattr(jobs, "apply_osm_sync", apply_then_crash)
    outcome = sync_once(cfg, remote_fetch=fake_remote(2))
    assert outcome.status == "failed"
    assert active_sites(db) == 1                                     # ... and rolled back
    assert scalar(db, "SELECT cursor->>'sequence' FROM sync_state WHERE source = 'openstreetmap'") == "1"   # checkpoint NOT advanced
    assert scalar(db, "SELECT status FROM sync_state WHERE source = 'openstreetmap'") == "error"
    assert scalar(db, "SELECT consecutive_failures FROM sync_state WHERE source = 'openstreetmap'") == 1
    assert scalar(db, "SELECT next_retry_at IS NOT NULL FROM sync_state WHERE source = 'openstreetmap'")

    monkeypatch.setattr(jobs, "apply_osm_sync", real_apply)          # "restart" with the bug gone
    outcome = sync_once(cfg, remote_fetch=fake_remote(2))
    assert outcome.status == "success" and active_sites(db) == 2
    assert scalar(db, "SELECT cursor->>'sequence' FROM sync_state WHERE source = 'openstreetmap'") == "2"
    assert scalar(db, "SELECT consecutive_failures FROM sync_state WHERE source = 'openstreetmap'") == 0


def test_resumes_after_interrupted_replication_from_durable_state(cfg, db):
    """osm2pgsql applied diffs (raw table + properties moved) but the worker died before the
    master sync: the next cycle must notice cursor != properties and apply, without re-downloading."""
    set_replication(db, 1)
    put_raw(db, "W", 1, FACTORY, SQUARE)
    sync_once(cfg)
    put_raw(db, "W", 2, FACTORY, SQUARE)
    set_replication(db, 2)                          # raw table ahead of the master tables
    calls = []
    outcome = sync_once(cfg, remote_fetch=fake_remote(2),
                        replication_update=lambda *a, **k: calls.append(1) or "")
    assert outcome.status == "success" and active_sites(db) == 2
    assert calls == []                              # nothing left to download
    assert scalar(db, "SELECT cursor->>'sequence' FROM sync_state WHERE source = 'openstreetmap'") == "2"


# 6 ── failed download then recovery ------------------------------------------------------------
def test_source_unavailable_then_recovery_updates_status_and_backoff(cfg, db):
    set_replication(db, 1)
    put_raw(db, "W", 1, FACTORY, SQUARE)

    def down(_cfg):
        raise osm_adapter.SourceUnavailable("connection refused")

    outcome = sync_once(cfg, remote_fetch=down)
    assert outcome.status == "failed"
    assert scalar(db, "SELECT last_check_error FROM sync_state WHERE source = 'openstreetmap'") is not None
    assert scalar(db, "SELECT last_check_at FROM sync_state WHERE source = 'openstreetmap'") is None        # a failed request is not a check
    assert scalar(db, "SELECT next_retry_at > NOW() FROM sync_state WHERE source = 'openstreetmap'")
    assert active_sites(db) == 0

    outcome = sync_once(cfg)
    assert outcome.status == "success" and active_sites(db) == 1
    assert scalar(db, "SELECT last_check_error FROM sync_state WHERE source = 'openstreetmap'") is None
    assert scalar(db, "SELECT next_retry_at FROM sync_state WHERE source = 'openstreetmap'") is None


def test_replication_update_failure_is_recorded_and_retried(cfg, db):
    set_replication(db, 1)
    put_raw(db, "W", 1, FACTORY, SQUARE)
    sync_once(cfg)

    def broken(_cfg, _stop=None):
        raise osm_adapter.ReplicationError("osm2pgsql exited 1")

    outcome = sync_once(cfg, remote_fetch=fake_remote(2), replication_update=broken)
    assert outcome.status == "failed"
    assert scalar(db, "SELECT cursor->>'sequence' FROM sync_state WHERE source = 'openstreetmap'") == "1"
    assert "replication update failed" in scalar(db, "SELECT last_error FROM sync_state WHERE source = 'openstreetmap'")


def test_backoff_grows_exponentially_with_jitter_and_cap(cfg):
    import random
    rng = random.Random(1)
    delays = [jobs.backoff_seconds(cfg, n, rng) for n in range(1, 8)]
    assert all(cfg.retry_base_seconds * 0.5 <= d <= cfg.retry_max_seconds for d in delays)
    assert delays[3] > delays[0]
    assert max(delays) <= cfg.retry_max_seconds


# 8 ── multiple workers -----------------------------------------------------------------------
def test_second_worker_skips_while_lock_is_held(cfg, db):
    set_replication(db, 1)
    put_raw(db, "W", 1, FACTORY, SQUARE)
    with advisory_lock(cfg.dsn, OSM_LOCK) as acquired:
        assert acquired
        outcome = sync_once(cfg)
        assert outcome.status == "skipped_locked"
        assert active_sites(db) == 0
    assert sync_once(cfg).status == "success"        # lock released -> proceeds


def test_concurrent_workers_run_the_job_exactly_once(cfg, db):
    set_replication(db, 1)
    put_raw(db, "W", 1, FACTORY, SQUARE)
    started = threading.Event()
    release = threading.Event()
    results: list[str] = []

    def slow_update(_cfg, _stop=None):
        started.set()
        release.wait(10)
        return ""

    def worker():
        results.append(sync_once(cfg, remote_fetch=fake_remote(2), replication_update=slow_update).status)

    first = threading.Thread(target=worker)
    first.start()
    assert started.wait(10)
    second = sync_once(cfg, remote_fetch=fake_remote(2), replication_update=slow_update)
    release.set()
    first.join(15)
    assert second.status == "skipped_locked"
    assert results == ["success"] and active_sites(db) == 1
    assert scalar(db, "SELECT COUNT(*) FROM sync_runs WHERE status = 'success'") == 1


def test_restart_closes_orphaned_running_runs(cfg, db):
    with db.cursor() as cur:
        cur.execute("INSERT INTO sync_runs (source, trigger, status) VALUES ('openstreetmap', 'schedule', 'running')")
    db.commit()
    set_replication(db, 1)
    sync_once(cfg)
    assert scalar(db, "SELECT COUNT(*) FROM sync_runs WHERE status = 'running'") == 0
    assert "interrupted" in scalar(db, "SELECT error FROM sync_runs WHERE trigger = 'schedule'")


# 10 ── notifications ---------------------------------------------------------------------------
def _listen(cfg):
    conn = connect(cfg.dsn, "test-listener")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"LISTEN {NOTIFY_CHANNEL_DATA}")
    return conn


def _drain(conn, timeout=3.0):
    import json
    out = []
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([conn], [], [], 0.3)
        if r:
            conn.poll()
            while conn.notifies:
                out.append(json.loads(conn.notifies.pop(0).payload))
            if out:
                break
    return out


def test_notification_is_sent_after_commit_with_revision_only(cfg, db):
    listener = _listen(cfg)
    set_replication(db, 1)
    put_raw(db, "W", 1, FACTORY, SQUARE)
    sync_once(cfg)
    events = _drain(listener)
    listener.close()
    assert len(events) == 1
    assert events[0]["revision"] == 1 and events[0]["created"] == 1
    assert "features" not in events[0] and "committed_at" in events[0]


def test_no_notification_when_nothing_changed_or_transaction_rolled_back(cfg, db, monkeypatch):
    set_replication(db, 1)
    put_raw(db, "W", 1, FACTORY, SQUARE)
    sync_once(cfg)
    listener = _listen(cfg)
    sync_once(cfg, trigger="reconcile")                    # unchanged
    assert _drain(listener, 1.5) == []
    put_raw(db, "W", 2, FACTORY, SQUARE)
    set_replication(db, 2)
    real = jobs.apply_osm_sync
    monkeypatch.setattr(jobs, "apply_osm_sync", lambda cur, **kw: (real(cur, **kw), (_ for _ in ()).throw(RuntimeError("x")))[0])
    sync_once(cfg, remote_fetch=fake_remote(2))            # rolled back
    assert _drain(listener, 1.5) == []
    listener.close()


# 12/13 ── freshness bookkeeping ---------------------------------------------------------------
def test_freshness_fields_track_check_data_and_commit_separately(cfg, db):
    set_replication(db, 1)
    put_raw(db, "W", 1, FACTORY, SQUARE)
    sync_once(cfg, remote_fetch=fake_remote(1))
    first_commit = scalar(db, "SELECT last_commit_at FROM sync_state WHERE source = 'openstreetmap'")
    first_data = scalar(db, "SELECT last_source_data_at FROM sync_state WHERE source = 'openstreetmap'")
    assert first_data == BASE_TIME + timedelta(hours=1)
    time.sleep(0.05)
    sync_once(cfg, remote_fetch=fake_remote(1))            # nothing new upstream
    assert scalar(db, "SELECT last_commit_at FROM sync_state WHERE source = 'openstreetmap'") == first_commit      # no commit => unchanged
    assert scalar(db, "SELECT last_source_data_at FROM sync_state WHERE source = 'openstreetmap'") == first_data
    assert scalar(db, "SELECT last_check_at FROM sync_state WHERE source = 'openstreetmap'") is not None            # but it WAS checked
    assert scalar(db, "SELECT source_head_sequence FROM sync_state WHERE source = 'openstreetmap'") == 1


def test_run_history_records_latency_and_counts(cfg, db):
    set_replication(db, 1)
    put_raw(db, "W", 1, FACTORY, SQUARE)
    sync_once(cfg)
    with db.cursor() as cur:
        cur.execute("""SELECT status, created_count, revision, ingest_latency_seconds, duration_seconds,
                              source_published_at FROM sync_runs ORDER BY id DESC LIMIT 1""")
        status, created, rev, latency, duration, published = cur.fetchone()
    db.commit()
    assert (status, created, rev) == ("success", 1, 1)
    assert latency is not None and latency >= 0 and duration >= 0 and published is not None


def test_awaiting_setup_without_baseline(cfg, db):
    outcome = sync_once(cfg)                               # osm2pgsql_properties is empty
    assert outcome.status == "awaiting_setup"
    assert scalar(db, "SELECT status FROM sync_state WHERE source = 'openstreetmap'") == "awaiting_setup"


def test_government_sources_are_registered_as_not_configured(cfg, db):
    with db.cursor() as cur:
        cur.execute("SELECT source, status, enabled FROM sync_state WHERE kind = 'government'")
        rows = cur.fetchall()
    db.commit()
    assert len(rows) == 3
    assert all(status == "not_configured" and enabled is False for _, status, enabled in rows)


# safety --------------------------------------------------------------------------------------
def test_mass_deactivation_guard_protects_against_bad_imports(cfg, db):
    set_replication(db, 1)
    with db.cursor() as cur:
        cur.execute("""INSERT INTO osm_industrial_raw (osm_type, osm_id, version, osm_timestamp, tags, geom)
            SELECT 'N', g, 1, NOW(), '{"industrial":"factory"}'::jsonb, ST_SetSRID(ST_MakePoint(1+g*0.0001, 1), 4326)
            FROM generate_series(1, 400) g""")
    db.commit()
    sync_once(cfg)
    assert active_sites(db) == 400
    with db.cursor() as cur:                               # simulate a truncated/failed import
        cur.execute("DELETE FROM osm_industrial_raw WHERE osm_id > 100")
    db.commit()
    set_replication(db, 2)
    outcome = sync_once(cfg, remote_fetch=fake_remote(2))
    assert outcome.status == "failed" and "refusing" in outcome.error
    assert active_sites(db) == 400                         # nothing deactivated
    assert scalar(db, "SELECT cursor->>'sequence' FROM sync_state WHERE source = 'openstreetmap'") == "1"


# 15 ── large batch, bounded Python memory ----------------------------------------------------------
def test_large_batch_is_processed_inside_the_database_with_bounded_python_memory(cfg, db):
    set_replication(db, 1)
    rows = 60_000
    with db.cursor() as cur:
        cur.execute("""INSERT INTO osm_industrial_raw (osm_type, osm_id, version, osm_timestamp, tags, geom)
            SELECT 'N', g, 1, NOW(), jsonb_build_object('industrial', 'factory', 'name', 'Site ' || g),
                   ST_SetSRID(ST_MakePoint(random()*9+0.5, random()*9+0.5), 4326)
            FROM generate_series(1, %s) g""", (rows,))
    db.commit()
    tracemalloc.start()
    outcome = sync_once(cfg)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert outcome.result.created == rows
    assert peak < 40 * 1024 * 1024, f"python heap peaked at {peak/1e6:.1f} MB"


def test_mass_deactivation_error_type_is_exposed():
    assert issubclass(MassDeactivationError, RuntimeError)
    assert advisory_key("a") != advisory_key("b")
