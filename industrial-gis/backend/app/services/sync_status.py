"""Derive human-meaningful sync states from raw ``sync_state`` rows.

Definitions (also documented in the README):

* ``not_configured``  source disabled/unconfigured (e.g. every government feed today).
* ``awaiting_setup``  OSM source enabled but the baseline import has not completed.
* ``syncing``         a job is running (status checking/syncing with a live heartbeat/recent update).
* ``offline``         the upstream source failed >= 3 consecutive checks, or the last job failed
                      and is waiting for backoff retry.
* ``delayed``         no recent successful check (worker stalled), OR the applied data is behind
                      the source head, OR applied data is older than the source's freshness target.
* ``live``            checked recently, caught up with the source head, data age within target.

``live`` therefore means "as fresh as the source allows", never merely "the API answers".
A source with a slow cadence (OSM Geofabrik diffs are daily) can be live with data many
hours old - that is why ``update_cadence`` and ``data_age_seconds`` are always reported.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SYNCING_STALE_AFTER_SECONDS = 15 * 60
OFFLINE_AFTER_FAILURES = 3


def _age(now: datetime, ts: datetime | None) -> float | None:
    return None if ts is None else max((now - ts).total_seconds(), 0.0)


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def derive_source(row: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    poll = int(row.get("poll_interval_seconds") or 300)
    target = row.get("freshness_target_seconds")
    cursor = row.get("cursor") or {}
    applied_seq = cursor.get("sequence")
    head_seq = row.get("source_head_sequence")
    stale_check_after = max(3 * poll, 900)

    check_age = _age(now, row.get("last_check_at"))
    data_age = _age(now, row.get("last_source_data_at"))
    lag_behind = None
    if row.get("source_head_timestamp") and row.get("last_source_data_at"):
        lag_behind = max((row["source_head_timestamp"] - row["last_source_data_at"]).total_seconds(), 0.0)
    behind_by = None
    if head_seq is not None and applied_seq is not None:
        behind_by = max(int(head_seq) - int(applied_seq), 0)

    status = row.get("status")
    state, reason = "live", "Caught up with the source and within its freshness target."
    if not row.get("enabled", True) or status == "not_configured":
        state, reason = "not_configured", row.get("last_error") or "Source is not configured or authorised."
    elif status == "awaiting_setup" or applied_seq is None:
        state, reason = "awaiting_setup", "Baseline import has not completed (run the bootstrap step)."
    elif status in ("checking", "syncing") and (
        _age(now, row.get("heartbeat_at") or row.get("updated_at")) or 0) < SYNCING_STALE_AFTER_SECONDS:
        state, reason = "syncing", "A synchronization job is processing source changes."
    elif (row.get("consecutive_failures") or 0) >= OFFLINE_AFTER_FAILURES or status == "error":
        state = "offline" if (row.get("consecutive_failures") or 0) >= OFFLINE_AFTER_FAILURES else "delayed"
        reason = f"Last job failed: {row.get('last_error') or 'unknown error'}"
    elif check_age is None or check_age > stale_check_after:
        state, reason = "delayed", "Source has not been checked recently (worker may be stopped)."
    elif behind_by:
        state, reason = "delayed", f"Applied data is {behind_by} replication step(s) behind the source."
    elif target is not None and data_age is not None and data_age > target:
        state, reason = "delayed", "Applied data is older than the freshness target."

    return {
        "source": row["source"],
        "kind": row.get("kind"),
        "state": state,
        "reason": reason,
        "enabled": bool(row.get("enabled", True)),
        "update_cadence": row.get("update_cadence"),
        "poll_interval_seconds": row.get("poll_interval_seconds"),
        "freshness_target_seconds": target,
        "last_check_at": _iso(row.get("last_check_at")),
        "last_check_age_seconds": check_age,
        "last_source_data_at": _iso(row.get("last_source_data_at")),
        "data_age_seconds": data_age,
        "source_head_timestamp": _iso(row.get("source_head_timestamp")),
        "source_head_published_at": _iso(row.get("source_head_published_at")),
        "lag_behind_source_seconds": lag_behind,
        "replication_steps_behind": behind_by,
        "last_commit_at": _iso(row.get("last_commit_at")),
        "last_success_at": _iso(row.get("last_success_at")),
        "cursor": cursor,
        "status_raw": status,
        "consecutive_failures": row.get("consecutive_failures") or 0,
        "next_retry_at": _iso(row.get("next_retry_at")),
        "last_error": row.get("last_error") if state not in ("live",) else None,
        "totals": row.get("totals") or {},
    }


_SEVERITY = {"live": 0, "syncing": 1, "not_configured": 2, "awaiting_setup": 3, "delayed": 4, "offline": 5}


def overall(sources: list[dict[str, Any]]) -> dict[str, str]:
    """Overall state = worst state among ENABLED sources (not_configured sources do not degrade it)."""
    active = [s for s in sources if s["enabled"]]
    if not active:
        return {"state": "not_configured", "reason": "No synchronization source is enabled."}
    worst = max(active, key=lambda s: _SEVERITY[s["state"]])
    # a syncing source should not hide a worse state elsewhere, nor mask 'live' siblings
    return {"state": worst["state"], "reason": worst["reason"], "source": worst["source"]}


FIRMS_SOURCE = "nasa_firms"


def derive_firms(st: dict[str, Any], now: datetime) -> dict[str, Any]:
    """FIRMS thermal worker status -> sync-source shape.

    live: checked within 3 poll intervals AND newest detection < 12 h old (VIIRS NRT trails the
    satellite pass by ~3 h, so hours-old newest data is normal, days-old is not).
    """
    def ts(key):
        v = st.get(key)
        return datetime.fromisoformat(v) if v else None

    interval = int(st.get("poll_interval_seconds") or 600)
    last_check, last_change = ts("last_check_at") or ts("last_success_at"), ts("last_change_at") or ts("last_success_at")
    latest = ts("latest_detection_at")
    check_age = _age(now, last_check)
    data_age = _age(now, latest)
    failures = int(st.get("consecutive_failures") or 0)
    if st.get("state") in ("checking", "processing"):
        state, reason = "syncing", "Fetching / classifying a new FIRMS batch."
    elif failures >= OFFLINE_AFTER_FAILURES:
        state, reason = "offline", f"FIRMS fetch failed {failures} times: {st.get('last_error')}"
    elif failures:
        state, reason = "delayed", f"Last FIRMS refresh failed: {st.get('last_error')}"
    elif check_age is None or check_age > max(3 * interval, 900):
        state, reason = "delayed", "FIRMS has not been checked recently (thermal worker may be stopped)."
    elif data_age is not None and data_age > 12 * 3600:
        state, reason = "delayed", "Newest FIRMS detection is more than 12 h old."
    else:
        state, reason = "live", "Checked recently; newest detections are within the satellite-latency window."
    return {
        "source": FIRMS_SOURCE, "kind": "thermal_firms", "state": state, "reason": reason, "enabled": True,
        "update_cadence": f"near-real-time: FIRMS publishes VIIRS passes ~3 h after acquisition; checked every {interval // 60} min",
        "poll_interval_seconds": interval, "freshness_target_seconds": 12 * 3600,
        "last_check_at": _iso(last_check), "last_check_age_seconds": check_age,
        "last_source_data_at": _iso(latest), "data_age_seconds": data_age,
        "source_head_timestamp": None, "source_head_published_at": st.get("source_published_at"),
        "lag_behind_source_seconds": 0.0, "replication_steps_behind": 0,
        "last_commit_at": _iso(last_change), "last_success_at": _iso(ts("last_success_at")),
        "cursor": {}, "status_raw": st.get("state"), "consecutive_failures": failures, "next_retry_at": None,
        "last_error": st.get("last_error") if state != "live" else None,
        "totals": {"new_detections_last_batch": st.get("new_detections", 0),
                   "ingest_latency_seconds": st.get("ingest_latency_seconds")},
    }
