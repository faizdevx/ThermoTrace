"""Offline mode: no outbound connection except the NASA FIRMS hosts.

``OFFLINE_MODE=true`` makes every process that calls :func:`install_from_env` (the thermal worker, the API) refuse to resolve or
connect to any host that is not

* a FIRMS host (``OFFLINE_ALLOW_HOSTS``, default ``firms.modaps.eosdis.nasa.gov`` and its sibling NRT hosts), or
* inside the private network: ``localhost``, single-label names (docker service names such as ``db``), RFC 1918 / loopback /
  link-local addresses.

Python's ``socket`` is patched, so ``requests``, ``urllib`` and friends are covered. Native libraries that open their own
sockets (GDAL / rasterio reading Sentinel-2 or WorldCover over HTTP, libpq) do not go through Python: the code that would use
GDAL asks :func:`enabled` first and uses its disk cache or says "offline" instead; libpq only talks to the database host.
Every refused attempt is counted (:func:`blocked`) so a run can show what it *tried* to reach.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading

logger = logging.getLogger(__name__)

DEFAULT_ALLOW = ("firms.modaps.eosdis.nasa.gov", "nrt3.modaps.eosdis.nasa.gov", "nrt4.modaps.eosdis.nasa.gov")


class OfflineViolation(OSError):
    """Raised instead of opening a connection that offline mode forbids."""


_lock = threading.Lock()
_state: dict = {"installed": False, "allow": set(), "blocked": {}, "allowed_seen": {}, "orig_getaddrinfo": None, "orig_connect": None, "allowed_ips": set()}


def enabled() -> bool:
    """True when OFFLINE_MODE is set (whether or not the socket guard is installed in this process)."""
    return os.getenv("OFFLINE_MODE", "").strip().lower() in ("1", "true", "yes")


def _allow_list() -> set[str]:
    env = os.getenv("OFFLINE_ALLOW_HOSTS", "").strip()
    return {h.strip().lower() for h in env.split(",") if h.strip()} if env else set(DEFAULT_ALLOW)


def _private(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def host_allowed(host: str, allow: set[str] | None = None) -> bool:
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return True
    allow = allow if allow is not None else _state["allow"]
    if _private(h) or h == "localhost" or "." not in h:
        return True
    return any(h == a or h.endswith("." + a) for a in allow)


def _note(kind: str, host: str) -> None:
    with _lock:
        d = _state[kind]
        d[host] = d.get(host, 0) + 1


def install(allow: set[str] | None = None) -> None:
    """Patch ``socket`` (idempotent)."""
    with _lock:
        if _state["installed"]:
            return
        _state["allow"] = set(allow) if allow is not None else _allow_list()
        _state["orig_getaddrinfo"], _state["orig_connect"] = socket.getaddrinfo, socket.socket.connect
        _state["installed"] = True
    orig_gai, orig_connect = _state["orig_getaddrinfo"], _state["orig_connect"]

    def guarded_getaddrinfo(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if isinstance(name, str) and not host_allowed(name):
            _note("blocked", name)
            logger.warning("offline mode: refused to resolve %s", name)
            raise OfflineViolation(f"offline mode: {name} is not an allowed host")
        res = orig_gai(host, *args, **kwargs)
        if isinstance(name, str) and "." in name and not _private(name):
            _note("allowed_seen", name)
            _state["allowed_ips"].update(r[4][0] for r in res)
        return res

    def guarded_connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else None
        if isinstance(host, str) and not _private(host) and host not in _state["allowed_ips"] and self.family in (socket.AF_INET, socket.AF_INET6):
            _note("blocked", host)
            logger.warning("offline mode: refused to connect to %s", host)
            raise OfflineViolation(f"offline mode: connection to {host} refused")
        return orig_connect(self, address, *args, **kwargs)

    socket.getaddrinfo = guarded_getaddrinfo
    socket.socket.connect = guarded_connect
    logger.info("offline mode: outbound connections limited to %s and the private network", sorted(_state["allow"]))


def uninstall() -> None:
    """Undo :func:`install` (tests)."""
    with _lock:
        if not _state["installed"]:
            return
        socket.getaddrinfo, socket.socket.connect = _state["orig_getaddrinfo"], _state["orig_connect"]
        _state.update({"installed": False, "blocked": {}, "allowed_seen": {}, "allowed_ips": set()})


def install_from_env() -> bool:
    if enabled():
        install()
        return True
    return False


def blocked() -> dict[str, int]:
    return dict(_state["blocked"])


def allowed_seen() -> dict[str, int]:
    return dict(_state["allowed_seen"])


def status() -> dict:
    return {"offline": enabled(), "guard_installed": _state["installed"], "allowed_hosts": sorted(_state["allow"] or _allow_list()),
            "blocked": blocked(), "allowed_external_seen": allowed_seen()}
