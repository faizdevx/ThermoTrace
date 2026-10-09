"""Unit tests for sync-state derivation (no database): what each status label means."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.services.sync_status import derive_source, overall

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def row(**kw):
    base = {
        "source": "openstreetmap", "kind": "osm_replication", "enabled": True, "status": "idle",
        "update_cadence": "daily", "poll_interval_seconds": 300, "freshness_target_seconds": 36 * 3600,
        "cursor": {"sequence": 10}, "last_check_at": NOW - timedelta(seconds=60),
        "source_head_sequence": 10, "source_head_timestamp": NOW - timedelta(hours=2),
        "last_source_data_at": NOW - timedelta(hours=2), "last_commit_at": NOW - timedelta(hours=2),
        "last_success_at": NOW - timedelta(seconds=60), "last_error": None, "consecutive_failures": 0,
        "next_retry_at": None, "heartbeat_at": None, "updated_at": NOW, "totals": {},
    }
    base.update(kw)
    return base


def state(**kw):
    return derive_source(row(**kw), NOW)["state"]


def test_live_requires_recent_check_caught_up_and_within_target():
    s = derive_source(row(), NOW)
    assert s["state"] == "live" and s["data_age_seconds"] == 7200


def test_live_is_not_claimed_for_data_older_than_target():
    assert state(last_source_data_at=NOW - timedelta(hours=40)) == "delayed"


def test_delayed_when_behind_source_head():
    s = derive_source(row(source_head_sequence=12), NOW)
    assert s["state"] == "delayed" and s["replication_steps_behind"] == 2


def test_delayed_when_worker_stopped_checking():
    assert state(last_check_at=NOW - timedelta(hours=2)) == "delayed"
    assert state(last_check_at=None) == "delayed"


def test_syncing_only_while_a_job_heartbeat_is_fresh():
    assert state(status="syncing", heartbeat_at=NOW - timedelta(seconds=10)) == "syncing"
    assert state(status="syncing", heartbeat_at=NOW - timedelta(hours=3), updated_at=NOW - timedelta(hours=3)) != "syncing"


def test_offline_after_repeated_failures_with_error_visible():
    s = derive_source(row(status="error", consecutive_failures=3, last_error="connection refused"), NOW)
    assert s["state"] == "offline" and "connection refused" in s["reason"] and s["last_error"]


def test_single_failure_is_delayed_not_offline():
    assert state(status="error", consecutive_failures=1, last_error="timeout") == "delayed"


def test_awaiting_setup_and_not_configured():
    assert state(status="awaiting_setup", cursor={}) == "awaiting_setup"
    assert state(enabled=False, status="not_configured") == "not_configured"


def test_overall_ignores_unconfigured_government_sources_but_reports_worst_enabled():
    live = derive_source(row(), NOW)
    gov = derive_source(row(source="gov_x", enabled=False, status="not_configured"), NOW)
    assert overall([live, gov])["state"] == "live"
    delayed = derive_source(row(source_head_sequence=99), NOW)
    assert overall([live, delayed, gov])["state"] == "delayed"
    assert overall([gov])["state"] == "not_configured"


def test_firms_source_states():
    from backend.app.services.sync_status import derive_firms
    iso = lambda dt: dt.isoformat()  # noqa: E731
    base = {"poll_interval_seconds": 600, "last_check_at": iso(NOW - timedelta(minutes=4)),
            "last_change_at": iso(NOW - timedelta(minutes=50)), "last_success_at": iso(NOW - timedelta(minutes=50)),
            "latest_detection_at": iso(NOW - timedelta(hours=4)), "state": "idle", "consecutive_failures": 0}
    assert derive_firms(base, NOW)["state"] == "live"
    assert derive_firms({**base, "state": "processing"}, NOW)["state"] == "syncing"
    assert derive_firms({**base, "last_check_at": iso(NOW - timedelta(hours=2))}, NOW)["state"] == "delayed"
    assert derive_firms({**base, "latest_detection_at": iso(NOW - timedelta(hours=20))}, NOW)["state"] == "delayed"
    assert derive_firms({**base, "consecutive_failures": 1, "last_error": "x"}, NOW)["state"] == "delayed"
    out = derive_firms({**base, "consecutive_failures": 3, "last_error": "dns"}, NOW)
    assert out["state"] == "offline" and "dns" in out["reason"]
    live = derive_firms(base, NOW)
    assert live["last_check_at"] != live["last_commit_at"] and live["data_age_seconds"] == 4 * 3600   # kept separate
