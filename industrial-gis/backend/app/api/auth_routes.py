"""Sign-in, sign-out, who am I, account administration and the audit log."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from backend.app.config import get_settings
from backend.app.security import accounts, audit, deps

router = APIRouter(prefix="/api")


def _need_postgis() -> None:
    if get_settings().data_mode != "postgis":
        raise HTTPException(status_code=404, detail="Accounts need the PostGIS stack.")


def _need_accounts() -> None:
    """User administration exists only with local accounts; with AUTH_MODE=off these routes would otherwise be open."""
    _need_postgis()
    if get_settings().auth_mode != "local":
        raise HTTPException(status_code=404, detail="User administration needs AUTH_MODE=local.")


def _need_audit() -> None:
    _need_postgis()
    if get_settings().auth_mode == "off":
        raise HTTPException(status_code=404, detail="The audit log needs AUTH_MODE=local or proxy.")


def _secure_cookie(request: Request) -> bool:
    s = get_settings()
    return s.cookie_secure or request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


@router.get("/auth/me")
def me(request: Request) -> dict[str, Any]:
    s = get_settings()
    u = getattr(request.state, "user", None)
    return {"auth_mode": s.auth_mode, "user": u, "demo_readonly": s.demo_readonly, "offline_mode": s.offline_mode,
            "session_idle_minutes": s.session_idle_minutes}


@router.post("/auth/login")
def login(payload: dict[str, Any], request: Request, response: Response) -> dict[str, Any]:
    s = get_settings()
    if s.auth_mode != "local":
        raise HTTPException(status_code=404, detail="Local sign-in is not enabled (AUTH_MODE).")
    _need_postgis()
    username, password = str(payload.get("username", ""))[:64], str(payload.get("password", ""))
    ip = deps.ip_of(request)
    c = deps.conn()
    try:
        user, reason = accounts.authenticate(c, username, password)
        if user is None:
            audit.record(c, username.strip().lower() or "unknown", None, "login.failed", None, {"reason": reason}, ip)
            raise HTTPException(status_code=423 if reason == "locked" else 401,
                                detail="Account locked for 15 minutes after repeated failures." if reason == "locked" else "Wrong username or password.")
        token = accounts.create_session(c, user["user_id"], ip, request.headers.get("user-agent"), s.session_max_hours)
        audit.record(c, user["username"], user["role"], "login.ok", None, None, ip)
    finally:
        c.close()
    response.set_cookie(deps.COOKIE, token, httponly=True, samesite="strict", secure=_secure_cookie(request), path="/", max_age=s.session_max_hours * 3600)
    return {"user": user}


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    if get_settings().auth_mode == "local":
        c = deps.conn()
        try:
            u = getattr(request.state, "user", None)
            accounts.revoke_session(c, request.cookies.get(deps.COOKIE))
            if u:
                audit.record(c, u["username"], u["role"], "logout", None, None, deps.ip_of(request))
        finally:
            c.close()
    response.delete_cookie(deps.COOKIE, path="/")
    return {"ok": True}


@router.post("/auth/password")
def change_password(payload: dict[str, Any], request: Request, user=Depends(deps.require("viewer"))) -> dict[str, Any]:
    if user is None or user.get("user_id") is None:
        raise HTTPException(status_code=400, detail="Passwords are managed by the identity provider.")
    c = deps.conn()
    try:
        try:
            accounts.change_own_password(c, user["user_id"], str(payload.get("current", "")), str(payload.get("new", "")))
        except accounts.AccountError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        audit.record(c, user["username"], user["role"], "user.password_changed", user["username"], None, deps.ip_of(request))
    finally:
        c.close()
    return {"ok": True}


# ---------------------------------------------------------------- administration
@router.get("/admin/users")
def list_users(user=Depends(deps.require("admin"))) -> dict[str, Any]:
    _need_accounts()
    c = deps.conn()
    try:
        return {"users": accounts.list_users(c)}
    finally:
        c.close()


@router.post("/admin/users", status_code=201)
def create_user(payload: dict[str, Any], request: Request, user=Depends(deps.require("admin"))) -> dict[str, Any]:
    _need_accounts()
    c = deps.conn()
    try:
        try:
            u = accounts.create_user(c, str(payload.get("username", "")), str(payload.get("password", "")), str(payload.get("role", "viewer")),
                                     payload.get("display_name"), bool(payload.get("must_change_password", True)))
        except accounts.AccountError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        audit.record(c, *deps.who(request), "user.created", u["username"], {"role": u["role"]}, deps.ip_of(request))
        return {"user": u}
    finally:
        c.close()


@router.patch("/admin/users/{user_id}")
def update_user(user_id: int, payload: dict[str, Any], request: Request, user=Depends(deps.require("admin"))) -> dict[str, Any]:
    _need_accounts()
    c = deps.conn()
    try:
        try:
            u = accounts.update_user(c, user_id, role=payload.get("role"), active=payload.get("active"), password=payload.get("password"),
                                     display_name=payload.get("display_name"), must_change=payload.get("must_change_password"))
        except accounts.AccountError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except LookupError:
            raise HTTPException(status_code=404, detail="Unknown user.") from None
        changed = {k: ("(set)" if k == "password" else v) for k, v in payload.items() if k in ("role", "active", "password", "display_name", "must_change_password")}
        audit.record(c, *deps.who(request), "user.updated", u["username"], changed, deps.ip_of(request))
        return {"user": u}
    finally:
        c.close()


@router.get("/admin/audit")
def audit_entries(limit: int = Query(default=100, ge=1, le=1000), before: int | None = None, action: str | None = None, actor: str | None = None,
                  user=Depends(deps.require("admin"))) -> dict[str, Any]:
    _need_audit()
    c = deps.conn()
    try:
        return {"entries": audit.entries(c, limit, before, action, actor)}
    finally:
        c.close()


@router.get("/admin/audit/verify")
def audit_verify(request: Request, user=Depends(deps.require("admin"))) -> dict[str, Any]:
    _need_audit()
    c = deps.conn()
    try:
        return audit.verify(c)
    finally:
        c.close()
