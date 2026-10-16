"""Offline mode: the socket guard, and the places that must not use native HTTP (land cover, imagery) when it is on."""
from __future__ import annotations

import socket

import pandas as pd
import pytest

from src import netguard


@pytest.fixture()
def guard(monkeypatch):
    monkeypatch.setenv("OFFLINE_MODE", "true")
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.7", 443))])
    netguard.uninstall()
    netguard.install({"firms.modaps.eosdis.nasa.gov"})
    yield netguard
    netguard.uninstall()


def test_host_rules():
    allow = {"firms.modaps.eosdis.nasa.gov"}
    for ok in ("firms.modaps.eosdis.nasa.gov", "FIRMS.modaps.eosdis.nasa.gov.", "localhost", "db", "api", "127.0.0.1", "10.2.3.4", "172.18.0.5", "192.168.1.9", "::1"):
        assert netguard.host_allowed(ok, allow), ok
    for bad in ("tile.openstreetmap.org", "earth-search.aws.element84.com", "8.8.8.8", "fonts.googleapis.com", "evil-firms.modaps.eosdis.nasa.gov.example.com",
                "planetarycomputer.microsoft.com"):
        assert not netguard.host_allowed(bad, allow), bad


def test_guard_refuses_to_resolve_other_hosts_counts_them_and_passes_firms_and_local(guard):
    with pytest.raises(netguard.OfflineViolation):
        socket.getaddrinfo("tile.openstreetmap.org", 443)
    with pytest.raises(OSError):                                   # it is an OSError: callers' network error handling still works
        socket.getaddrinfo("tile.openstreetmap.org", 443)
    assert socket.getaddrinfo("firms.modaps.eosdis.nasa.gov", 443)[0][4][0] == "203.0.113.7"
    assert socket.getaddrinfo("db", 5432)
    assert guard.blocked() == {"tile.openstreetmap.org": 2}
    assert guard.allowed_seen() == {"firms.modaps.eosdis.nasa.gov": 1}
    assert guard.status()["guard_installed"] and "firms.modaps.eosdis.nasa.gov" in guard.status()["allowed_hosts"]


def test_guard_refuses_direct_connection_to_a_public_address(guard):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(netguard.OfflineViolation):
            s.connect(("8.8.8.8", 53))
        assert guard.blocked()["8.8.8.8"] == 1
    finally:
        s.close()


def test_install_is_idempotent_and_reversible(monkeypatch):
    netguard.uninstall()
    before = socket.getaddrinfo
    netguard.install({"x.example"})
    first = socket.getaddrinfo
    netguard.install({"y.example"})
    assert socket.getaddrinfo is first and first is not before
    netguard.uninstall()
    assert socket.getaddrinfo is before


def test_enabled_follows_the_environment(monkeypatch):
    monkeypatch.delenv("OFFLINE_MODE", raising=False)
    assert not netguard.enabled() and not netguard.install_from_env()
    monkeypatch.setenv("OFFLINE_MODE", "yes")
    assert netguard.enabled()


def test_landcover_offline_leaves_unknown_cells_unknown_and_unwritten(monkeypatch, tmp_path):
    from src.thermal import landcover
    monkeypatch.setenv("OFFLINE_MODE", "true")
    called = []
    monkeypatch.setattr(landcover, "sample_one", lambda lat, lon: called.append((lat, lon)) or {"crop": 1.0})
    cache = tmp_path / "lc.json"
    pts = pd.DataFrame({"lat": [21.0, 22.0], "lon": [83.0, 84.0]})
    out = landcover.sample_many(pts, cache)
    assert called == [] and out["lc_crop"].isna().all() and not cache.exists()       # nothing read, nothing remembered as "no data"
    monkeypatch.delenv("OFFLINE_MODE")
    out = landcover.sample_many(pts, cache)                                          # back online: read normally
    assert len(called) == 2 and (out["lc_crop"] == 1.0).all() and cache.exists()


def test_imagery_offline_serves_the_cache_and_says_so_for_the_rest(monkeypatch, tmp_path):
    from src.imagery import core
    monkeypatch.setenv("OFFLINE_MODE", "true")
    cache = core.DiskCache(tmp_path)
    cache.put("s1", "2026-10-10T00:00:00+00:00", {"available": True, "scene": {"id": "S2B_cached"}})
    monkeypatch.setattr(core, "analyse_source", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not search the network")))
    monkeypatch.setattr(core, "_mem", core.OrderedDict())
    hit = core.cached_analysis("s1", 83.0, 21.0, "2026-10-10T05:00:00+00:00", cache=cache)
    assert hit["available"] and hit["scene"]["id"] == "S2B_cached"
    miss = core.cached_analysis("s2", 83.0, 21.0, "2026-10-10T05:00:00+00:00", cache=cache)
    assert miss["available"] is False and miss["offline"] is True and "Offline mode" in miss["reason"]
    assert cache.get("s2", "2026-10-10T05:00:00+00:00") is None                       # the "offline" answer is not cached as if it were a result


def test_imagery_enrichment_computes_nothing_offline(monkeypatch, tmp_path):
    from src.imagery import enrich
    monkeypatch.setenv("OFFLINE_MODE", "true")
    src = pd.DataFrame([{"source_id": "a", "lat": 21.0, "lon": 83.0, "last_seen": pd.Timestamp("2026-10-13T20:00", tz="UTC"),
                         "first_seen": pd.Timestamp("2026-10-12T20:00", tz="UTC"), "alert_level": "high", "thermal_class": "industrial_fire_candidate",
                         "frp_max": 40.0, "frp_z": 5.0, "model_class": "industrial_persistent", "reasons": ["r"]}])
    calls = []
    out, stats = enrich.enrich_sources(src, tmp_path, per_cycle=4, analyse=lambda *a, **k: calls.append(1))
    assert calls == [] and stats["computed"] == 0 and len(out) == 1
