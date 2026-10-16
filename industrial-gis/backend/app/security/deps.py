"""Request-level helpers shared by the gate and the routes: who is calling, role checks, audit entries."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request

from backend.app.config import get_settings
from backend.app.db import get_engine
from backend.app.security import audit, rank
from backend.app.security.ratelimit import client_ip

logger = logging.getLogger(__name__)
COOKIE = "tt_session"


def conn():
    return get_engine().raw_connection()


def ip_of(request: Request) -> str:
    return client_ip(request.headers, request.client.host if request.client else None, get_settings().trusted_proxy_hops)


def who(request: Request) -> tuple[str, str | None]:
    """(actor, role): the signed-in user, or ('anonymous', None) when authentication is off."""
    u = getattr(request.state, "user", None)
    return (u["username"], u["role"]) if u else ("anonymous", None)


def require(min_role: str):
    """Route dependency. With AUTH_MODE=off it lets everything through (the legacy ANALYST_TOKEN / ADMIN_TOKEN checks stay in the routes)."""
    def dep(request: Request) -> dict | None:
        if get_settings().auth_mode == "off":
            return None
        u = getattr(request.state, "user", None)
        if u is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        if rank(u["role"]) < rank(min_role):
            try:
                audit_event(request, "access.denied", request.url.path, {"needs": min_role, "method": request.method})
            except Exception:  # noqa: BLE001
                logger.exception("could not audit a denied request")
            raise HTTPException(status_code=403, detail=f"This needs the {min_role} role.")
        return u
    return dep


def audit_event(request: Request, action: str, target: str | None = None, detail: Any = None) -> dict:
    actor, role = who(request)
    c = conn()
    try:
        return audit.record(c, actor, role, action, target, detail, ip_of(request))
    finally:
        c.close()
