"""The API's front door: request id + access log, rate limits, authentication, CSRF header, in that order.

``AUTH_MODE`` off: nothing changes (legacy tokens). local: a ``tt_session`` cookie from ``POST /api/auth/login``. proxy: identity comes
from headers set by an OIDC reverse proxy (oauth2-proxy and the like) and is trusted only together with the shared secret.
"""
from __future__ import annotations

import hmac
import json
import logging
import re
import time
import uuid

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.app.config import get_settings
from backend.app.security import accounts, deps
from backend.app.security.ratelimit import RateLimiter

access_log = logging.getLogger("thermotrace.access")
limiter = RateLimiter()

PUBLIC = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/me"}
HEAVY = re.compile(r"^/api/(thermal/sources/[^/]+/imagery|alerts/\d+/report\.pdf|thermal/export|industries/export)$")
SAFE = ("GET", "HEAD", "OPTIONS")
CSRF_HEADER, CSRF_VALUE = "x-requested-with", "ThermoTrace"


def _proxy_user(request, settings) -> dict | None:
    secret = request.headers.get("x-auth-proxy-secret", "")
    if not settings.proxy_secret or not hmac.compare_digest(secret, settings.proxy_secret):
        return None
    name = (request.headers.get(settings.proxy_user_header.lower()) or "").strip().lower()
    if not name:
        return None
    groups = {g.strip() for g in (request.headers.get(settings.proxy_groups_header.lower()) or "").split(",") if g.strip()}
    role = "admin" if settings.proxy_admin_group in groups else "analyst" if settings.proxy_analyst_group in groups else "viewer"
    return {"user_id": None, "username": name, "display_name": name, "role": role, "active": True, "via": "proxy"}


def _identify(request) -> dict | None:
    s = get_settings()
    if s.auth_mode == "proxy":
        return _proxy_user(request, s)
    if s.auth_mode == "local":
        c = deps.conn()
        try:
            return accounts.session_user(c, request.cookies.get(deps.COOKIE), s.session_idle_minutes)
        finally:
            c.close()
    return None


def _deny(status: int, detail: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status, headers=headers)


async def gate(request, call_next):
    s = get_settings()
    started = time.perf_counter()
    rid = (request.headers.get("x-request-id") or "")[:64] or uuid.uuid4().hex[:16]
    request.state.request_id = rid
    request.state.user = None
    ip = deps.ip_of(request)
    path = request.url.path
    response = None
    try:
        if path.startswith("/api"):
            if s.rate_limit:
                rules = [("general", ip, s.rate_general_per_min)]
                if path == "/api/auth/login" and request.method == "POST":
                    rules.append(("login", ip, s.rate_login_per_min))
                if HEAVY.match(path):
                    rules.append(("heavy", ip, s.rate_heavy_per_min))
                for rule, key, limit in rules:
                    ok, retry = limiter.check(rule, key, limit)
                    if not ok:
                        response = _deny(429, "Too many requests; slow down.", {"Retry-After": str(int(retry) + 1)})
                        return response
            if s.auth_mode != "off":
                request.state.user = await run_in_threadpool(_identify, request)
                if request.state.user is None and path not in PUBLIC and request.method != "OPTIONS":
                    response = _deny(401, "Authentication required.")
                    return response
                if s.auth_mode == "local" and request.method not in SAFE and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
                    response = _deny(403, "Missing X-Requested-With header (CSRF protection).")
                    return response
        response = await call_next(request)
        return response
    finally:
        if response is not None:
            response.headers["X-Request-ID"] = rid
        if path.startswith("/api") and path != "/api/health":
            u = request.state.user
            access_log.info(json.dumps({"id": rid, "ip": ip, "user": u["username"] if u else None, "role": u["role"] if u else None, "method": request.method,
                                        "path": path, "status": getattr(response, "status_code", 500),
                                        "ms": round((time.perf_counter() - started) * 1000, 1)}, separators=(",", ":")))
