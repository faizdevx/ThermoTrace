"""Account administration from the command line (the first admin has to come from here).

    python -m backend.app.users create --username asha --role admin          # password from THERMOTRACE_PASSWORD or a prompt
    python -m backend.app.users list
    python -m backend.app.users set-password --username asha
    python -m backend.app.users disable --username asha
    python -m backend.app.users verify-audit                                  # exit 1 when the audit chain is broken

Inside the stack: ``docker compose exec api python -m backend.app.users create --username asha --role admin``.
Every change made here is written to the audit log as actor ``cli``.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from backend.app.db import get_engine
from backend.app.security import accounts, audit


def _password(confirm: bool = True) -> str:
    env = os.getenv("THERMOTRACE_PASSWORD")
    if env:
        return env
    pw = getpass.getpass("Password: ")
    if confirm and getpass.getpass("Repeat: ") != pw:
        raise SystemExit("passwords differ")
    return pw


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["create", "list", "set-password", "disable", "enable", "set-role", "verify-audit"])
    ap.add_argument("--username")
    ap.add_argument("--role", choices=["viewer", "analyst", "admin"], default="viewer")
    ap.add_argument("--display-name")
    ap.add_argument("--must-change", action="store_true", help="force a password change at first sign-in")
    a = ap.parse_args(argv)
    conn = get_engine().raw_connection()
    try:
        if a.command == "list":
            for u in accounts.list_users(conn):
                print(f"{u['username']:<24} {u['role']:<8} {'active' if u['active'] else 'DISABLED':<9} last login {u['last_login_at'] or 'never'}")
            return 0
        if a.command == "verify-audit":
            r = audit.verify(conn)
            print(r)
            return 0 if r["ok"] else 1
        if not a.username:
            raise SystemExit("--username is required")
        name = a.username.strip().lower()
        try:
            if a.command == "create":
                u = accounts.create_user(conn, name, _password(), a.role, a.display_name, a.must_change)
                audit.record(conn, "cli", None, "user.created", name, {"role": u["role"]}, None)
            else:
                target = next((u for u in accounts.list_users(conn) if u["username"] == name), None)
                if target is None:
                    raise SystemExit(f"no such user: {name}")
                if a.command == "set-password":
                    accounts.update_user(conn, target["user_id"], password=_password(), must_change=a.must_change)
                    audit.record(conn, "cli", None, "user.updated", name, {"password": "(set)"}, None)
                elif a.command in ("disable", "enable"):
                    accounts.update_user(conn, target["user_id"], active=a.command == "enable")
                    audit.record(conn, "cli", None, "user.updated", name, {"active": a.command == "enable"}, None)
                elif a.command == "set-role":
                    accounts.update_user(conn, target["user_id"], role=a.role)
                    audit.record(conn, "cli", None, "user.updated", name, {"role": a.role}, None)
        except accounts.AccountError as exc:
            print("refused:", exc, file=sys.stderr)
            return 2
        print("ok")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
