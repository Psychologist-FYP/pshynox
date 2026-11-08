"""In-memory sliding-window rate limits for the public meeting server.

Funnel does not document a client-IP header, so limits are keyed by route and meeting id, plus a global key per route.
Keys hold no IP addresses. The meeting id is 128-bit and the token 256-bit, so these limits protect resources; they are
not what prevents guessing.
"""
from __future__ import annotations

import threading
import time
from collections import deque

# (requests, window seconds)
DEFAULTS = {
    "lookup": (30, 60), "lookup:*": (300, 60),
    "consent": (30, 60), "consent:*": (300, 60),
    "ice": (10, 60), "ice:*": (120, 60),
    "signal": (10, 60), "signal:*": (120, 60),
    "static:*": (600, 60),
    "failed": (20, 600),
}


class Limiter:
    def __init__(self, rules: dict[str, tuple[int, float]] | None = None, clock=time.monotonic):
        self.rules = dict(rules or DEFAULTS)
        self.clock = clock
        self.hits: dict[str, deque] = {}
        self.lock = threading.Lock()

    def _allow(self, key: str, rule: str, record: bool = True) -> bool:
        limit, window = self.rules[rule]
        now = self.clock()
        with self.lock:
            q = self.hits.setdefault(key, deque())
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            if record:
                q.append(now)
            if len(self.hits) > 10000:   # bound memory if many ids are probed
                for k in [k for k, v in self.hits.items() if not v][:5000]:
                    self.hits.pop(k, None)
            return True

    def allow(self, route: str, meeting: str | None = None) -> bool:
        """True if this request may proceed: not locked out, within the global and the per-meeting window."""
        if meeting is not None and self.locked_out(meeting):
            return False
        glob = f"{route}:*"
        if glob in self.rules and not self._allow(glob, glob):
            return False
        if meeting is not None and route in self.rules and not self._allow(f"{route}:{meeting}", route):
            return False
        return True

    def locked_out(self, meeting: str) -> bool:
        return not self._allow(f"failed:{meeting}", "failed", record=False)

    def failed(self, meeting: str) -> None:
        self._allow(f"failed:{meeting}", "failed")
