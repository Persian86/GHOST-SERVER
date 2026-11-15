"""Reuse Home Assistant connections without replaying actions or caching data."""
from __future__ import annotations

import atexit
import threading
from dataclasses import dataclass, field

import httpx


@dataclass
class _Entry:
    identity: tuple = field(repr=False)
    client: object = field(repr=False)
    users: int = 0
    retired: bool = False


class HomeTransport:
    def __init__(self, factory=None):
        self._factory = factory or self._new_client
        self._lock = threading.Lock()
        self._current = None

    @staticmethod
    def _new_client():
        # HTTPX does not retry failed action requests automatically. Keep TLS
        # verification and per-request timeouts, including on pooled sockets.
        return httpx.Client(timeout=10, trust_env=False, follow_redirects=False,
                            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10,
                                                keepalive_expiry=60))

    def request(self, method, base, token, path, **kwargs):
        identity = (base, token)
        old = None
        with self._lock:
            entry = self._current
            if entry is None or entry.identity != identity:
                # Do not reuse authentication cookies after a server/token change.
                fresh = _Entry(identity, self._factory())
                if entry is not None:
                    entry.retired = True
                    if not entry.users:
                        old = entry
                self._current = entry = fresh
            entry.users += 1
        try:
            if old is not None:
                old.client.close()
            return entry.client.request(method, base + path,
                                        headers={"Authorization": f"Bearer {token}"}, **kwargs)
        finally:
            with self._lock:
                entry.users -= 1
                close = entry.retired and entry.users == 0
            if close:
                entry.client.close()

    def close(self):
        with self._lock:
            entry, self._current = self._current, None
            if entry is not None:
                entry.retired = True
            close = entry is not None and entry.users == 0
        if close:
            entry.client.close()


transport = HomeTransport()
atexit.register(transport.close)
