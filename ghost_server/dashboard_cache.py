"""Coalesced background panel refreshes with a bounded dashboard response wait."""
from __future__ import annotations

import threading
import time


class PanelCache:
    def __init__(self, values, lock):
        self.values, self.lock = values, lock
        self.active = {}
        self.failed = set()
        self.attempts = {}

    def start(self, key, ttl, load):
        now = time.time()
        with self.lock:
            hit = self.values.get(key)
            effective_ttl = ttl if hit and hit[1] else min(ttl, 60)
            if hit and now - hit[0] < effective_ttl:
                return None
            if key in self.active:
                return self.active[key]
            if key in self.failed and now - self.attempts.get(key, 0) < 30:
                return None
            # A stuck upstream must not create an unbounded collection of workers.
            if len(self.active) >= 8:
                return None
            done = threading.Event()
            self.active[key] = done
            self.attempts[key] = now

        def run():
            try:
                value = load()
                if value is None:
                    raise ValueError("panel unavailable")
                with self.lock:
                    self.values[key] = (time.time(), value)
                    self.failed.discard(key)
            except Exception:
                # Keep a previous report. Never expose provider exceptions/credential URLs.
                with self.lock:
                    self.failed.add(key)
            finally:
                with self.lock:
                    self.active.pop(key, None)
                done.set()

        threading.Thread(target=run, name="ghost-dashboard", daemon=True).start()
        return done

    def snapshot(self, key):
        with self.lock:
            hit = self.values.get(key)
            available = bool(hit and hit[1])
            loading = key in self.active
            status = ("refreshing" if available else "loading") if loading else (
                "stale" if available and key in self.failed else "ready" if available else "unavailable")
            return (hit[1] if hit else None), status, (hit[0] if hit else 0)


def wait_briefly(events, seconds=1.5):
    deadline = time.monotonic() + seconds
    for event in events:
        if event is not None:
            event.wait(max(0, deadline - time.monotonic()))
