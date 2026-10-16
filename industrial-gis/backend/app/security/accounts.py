"""Local accounts and cookie sessions on PostGIS (migration 011). DB-API connections, no framework imports."""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from . import ROLES, passwords

USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
LOCK_AFTER, LOCK_MINUTES = 5, 15
_COLS = "user_id, username, display_name, role, active, must_change_password, created_at, last_login_at"


class AccountError(ValueError):
    pass


def _user(row) -> dict:
    uid, name, disp, role, active, must, created, last = row
    return {"user_id": uid, "username": name, "display_name": disp, "role": role, "active": active, "must_change_password": must,
            "created_at": created.isoformat(), "last_login_at": last.isoformat() if last else None}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_user(conn, username: str, password: str, role: str, display_name: str | None = None, must_change: bool = False) -> dict:
    username = username.strip().lower()
    if not USERNAME.match(username):
        raise AccountError("username must be 3-64 characters: lower-case letters, digits, '.', '_' or '-'")
    if role not in ROLES:
        raise AccountError(f"role must be one of {list(ROLES)}")
    why = passwords.check_policy(password, username)
    if why:
        raise AccountError(why)
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE username = %s", (username,))
        if cur.fetchone():
            raise AccountError("username already exists")
        cur.execute(f"INSERT INTO users (username, display_name, role, password_hash, must_change_password) VALUES (%s, %s, %s, %s, %s) RETURNING {_COLS}",
                    (username, display_name, role, passwords.hash_password(password), must_change))
        u = _user(cur.fetchone())
    conn.commit()
    return u


def authenticate(conn, username: str, password: str) -> tuple[dict | None, str]:
    """(user, "ok") or (None, reason). reason: unknown | bad_password | locked | disabled. Five failures lock the account for 15 minutes."""
    username = (username or "").strip().lower()
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS}, password_hash, failed_logins, locked_until FROM users WHERE username = %s FOR UPDATE", (username,))
        r = cur.fetchone()
        if r is None:
            passwords.spend_time()
            conn.rollback()
            return None, "unknown"
        user, pw_hash, failed, locked_until = _user(r[:8]), r[8], r[9], r[10]
        now = datetime.now(timezone.utc)
        if locked_until and locked_until > now:
            passwords.spend_time()
            conn.rollback()
            return None, "locked"
        ok = passwords.verify_password(password, pw_hash)
        if not user["active"]:
            conn.rollback()
            return None, "disabled"
        if not ok:
            failed += 1
            lock = now + timedelta(minutes=LOCK_MINUTES) if failed >= LOCK_AFTER else None
            cur.execute("UPDATE users SET failed_logins = %s, locked_until = %s WHERE user_id = %s", (0 if lock else failed, lock, user["user_id"]))
            conn.commit()
            return None, "locked" if lock else "bad_password"
        cur.execute("UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = %s WHERE user_id = %s", (now, user["user_id"]))
    conn.commit()
    return user, "ok"


def create_session(conn, user_id: int, ip: str | None, user_agent: str | None, max_hours: int) -> str:
    token = secrets.token_urlsafe(32)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE expires_at < now()")
        cur.execute("INSERT INTO sessions (token_hash, user_id, expires_at, ip, user_agent) VALUES (%s, %s, now() + make_interval(hours => %s), %s, %s)",
                    (hash_token(token), user_id, max_hours, ip, (user_agent or "")[:300]))
    conn.commit()
    return token


def session_user(conn, token: str | None, idle_minutes: int) -> dict | None:
    """The user behind a session cookie, or None (unknown, expired, idle too long, user disabled). Slides the idle window."""
    if not token:
        return None
    with conn.cursor() as cur:
        cur.execute(f"SELECT u.user_id, u.username, u.display_name, u.role, u.active, u.must_change_password, u.created_at, u.last_login_at, s.last_seen "
                    "FROM sessions s JOIN users u USING (user_id) WHERE s.token_hash = %s AND s.expires_at > now()", (hash_token(token),))
        r = cur.fetchone()
        if r is None:
            conn.rollback()
            return None
        now = datetime.now(timezone.utc)
        if not r[4] or now - r[8] > timedelta(minutes=idle_minutes):
            cur.execute("DELETE FROM sessions WHERE token_hash = %s", (hash_token(token),))
            conn.commit()
            return None
        if now - r[8] > timedelta(seconds=60):
            cur.execute("UPDATE sessions SET last_seen = %s WHERE token_hash = %s", (now, hash_token(token)))
        conn.commit()
        return _user(r[:8])


def revoke_session(conn, token: str | None) -> None:
    if token:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE token_hash = %s", (hash_token(token),))
        conn.commit()


def list_users(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM users ORDER BY username")
        return [_user(r) for r in cur.fetchall()]


def update_user(conn, user_id: int, *, role: str | None = None, active: bool | None = None, password: str | None = None,
                display_name: str | None = None, must_change: bool | None = None) -> dict:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
        r = cur.fetchone()
        if r is None:
            raise LookupError("unknown user")
        cur_user = _user(r)
        if role is not None and role not in ROLES:
            raise AccountError(f"role must be one of {list(ROLES)}")
        loses_admin = cur_user["role"] == "admin" and cur_user["active"] and ((role is not None and role != "admin") or active is False)
        if loses_admin:
            cur.execute("SELECT count(*) FROM users WHERE role = 'admin' AND active")
            if cur.fetchone()[0] <= 1:
                raise AccountError("cannot remove the last active admin")
        sets, params = [], []
        if role is not None:
            sets.append("role = %s")
            params.append(role)
        if active is not None:
            sets.append("active = %s")
            params.append(active)
        if display_name is not None:
            sets.append("display_name = %s")
            params.append(display_name)
        if must_change is not None:
            sets.append("must_change_password = %s")
            params.append(must_change)
        if password is not None:
            why = passwords.check_policy(password, cur_user["username"])
            if why:
                raise AccountError(why)
            sets += ["password_hash = %s", "failed_logins = 0", "locked_until = NULL"]
            params.append(passwords.hash_password(password))
        if sets:
            cur.execute(f"UPDATE users SET {', '.join(sets)} WHERE user_id = %s RETURNING {_COLS}", (*params, user_id))
            cur_user = _user(cur.fetchone())
        if active is False or password is not None or role is not None:
            cur.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))      # a changed role or password, or a disabled account, ends open sessions
    conn.commit()
    return cur_user


def change_own_password(conn, user_id: int, current: str, new: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT username, password_hash FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
        name, h = cur.fetchone()
        if not passwords.verify_password(current, h):
            conn.rollback()
            raise AccountError("current password is wrong")
        why = passwords.check_policy(new, name)
        if why:
            conn.rollback()
            raise AccountError(why)
        cur.execute("UPDATE users SET password_hash = %s, must_change_password = FALSE WHERE user_id = %s", (passwords.hash_password(new), user_id))
    conn.commit()
