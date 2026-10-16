"""Append-only, hash-chained audit log (table ``audit_log``, migration 011).

``record_cur`` writes inside the caller's transaction (so a decision and its audit row commit or fail together); ``record``
opens its own. Rows are serialised with an advisory lock so the chain has one order. ``verify`` recomputes the whole chain.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

LOCK_KEY = 7316_0001
GENESIS = "genesis"


def canonical(at: datetime, actor: str, role: str | None, action: str, target: str | None, detail: Any, ip: str | None) -> str:
    return json.dumps({"at": at.astimezone(timezone.utc).isoformat(timespec="microseconds"), "actor": actor, "role": role, "action": action,
                       "target": target, "detail": detail, "ip": ip}, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)


def chain_hash(prev: str, content: str) -> str:
    return hashlib.sha256((prev + "|" + content).encode("utf-8")).hexdigest()


def record_cur(cur, actor: str, role: str | None, action: str, target: str | None = None, detail: Any = None, ip: str | None = None) -> dict:
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
    cur.execute("SELECT row_hash FROM audit_log ORDER BY audit_id DESC LIMIT 1")
    r = cur.fetchone()
    prev = r[0] if r else GENESIS
    at = datetime.now(timezone.utc)
    detail = json.loads(json.dumps(detail, default=str)) if detail is not None else None     # exactly what JSONB will hold
    row_hash = chain_hash(prev, canonical(at, actor, role, action, target, detail, ip))
    cur.execute("INSERT INTO audit_log (at, actor, role, action, target, detail, ip, prev_hash, row_hash) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s) RETURNING audit_id",
                (at, actor, role, action, target, json.dumps(detail) if detail is not None else None, ip, prev, row_hash))
    return {"audit_id": cur.fetchone()[0], "row_hash": row_hash}


def record(conn, actor: str, role: str | None, action: str, target: str | None = None, detail: Any = None, ip: str | None = None) -> dict:
    with conn.cursor() as cur:
        out = record_cur(cur, actor, role, action, target, detail, ip)
    conn.commit()
    return out


def verify(conn) -> dict:
    """Recompute the chain. ``ok`` is False at the first row whose hash or link does not match; ``head`` is the hash to anchor elsewhere."""
    prev, n = GENESIS, 0
    with conn.cursor(name="audit_verify") as cur:
        cur.itersize = 2000
        cur.execute("SELECT audit_id, at, actor, role, action, target, detail, ip, prev_hash, row_hash FROM audit_log ORDER BY audit_id")
        for audit_id, at, actor, role, action, target, detail, ip, prev_hash, row_hash in cur:
            n += 1
            if prev_hash != prev or chain_hash(prev, canonical(at, actor, role, action, target, detail, ip)) != row_hash:
                return {"ok": False, "rows": n, "first_bad_id": audit_id, "head": None}
            prev = row_hash
    return {"ok": True, "rows": n, "first_bad_id": None, "head": None if n == 0 else prev}


def entries(conn, limit: int = 100, before_id: int | None = None, action: str | None = None, actor: str | None = None) -> list[dict]:
    where, params = [], []
    if before_id:
        where.append("audit_id < %s")
        params.append(before_id)
    if action:
        where.append("action LIKE %s")
        params.append(action.replace("%", "") + "%")
    if actor:
        where.append("actor = %s")
        params.append(actor)
    sql = "SELECT audit_id, at, actor, role, action, target, detail, ip, row_hash FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "") + \
          " ORDER BY audit_id DESC LIMIT %s"
    with conn.cursor() as cur:
        cur.execute(sql, (*params, min(max(limit, 1), 1000)))
        return [{"audit_id": a, "at": at.isoformat(), "actor": ac, "role": ro, "action": act, "target": tg, "detail": d, "ip": ip, "row_hash": h}
                for a, at, ac, ro, act, tg, d, ip, h in cur.fetchall()]
