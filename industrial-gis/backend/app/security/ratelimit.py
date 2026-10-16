"""In-process sliding-window rate limiter (per key, per rule). One API process = one budget; nginx limits at the edge as well."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()
        self._clock = clock

    def check(self, rule: str, key: str, limit: int, window: float = 60.0) -> tuple[bool, float]:
        """(allowed, retry_after_seconds). Counts the request when allowed."""
        now = self._clock()
        with self._lock:
            q = self._hits[(rule, key)]
            while q and now - q[0] >= window:
                q.popleft()
            if len(q) >= limit:
                return False, max(0.0, window - (now - q[0]))
            q.append(now)
            if len(self._hits) > 20_000:                      # forget keys that went quiet
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > window]:
                    self._hits.pop(k, None)
            return True, 0.0


def client_ip(headers, peer: str | None, hops: int) -> str:
    """The client address: the peer itself, or with ``hops`` trusted proxies the entry ``hops`` from the right of X-Forwarded-For."""
    if hops > 0:
        parts = [p.strip() for p in (headers.get("x-forwarded-for") or "").split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return peer or "unknown"
