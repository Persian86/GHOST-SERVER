"""The family calendar for the tablet's Calendar page (day, week and month views).

Sources: the household calendars (admin → calendars, shown as "Family") and each
person's own calendar link (Visual ID people → calendar). Every person gets a colour
so their events stand out, like a paper family planner. Calendar feeds are cached
for five minutes so flicking between weeks doesn't refetch them.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
import time

from . import store

PALETTE = ["#7C6CF2", "#2F9BE0", "#E0568A", "#F29B38", "#2DB37A", "#C46BE0", "#E0C33A", "#4FC1C9"]
CACHE_FOR = 300
MAX_DAYS = 42
_cache: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()


def _fetch(url: str) -> str:
    with _lock:
        hit = _cache.get(url)
        if hit and time.time() - hit[0] < CACHE_FOR:
            return hit[1]
    import httpx
    try:
        text = httpx.get(url.replace("webcal://", "https://"), timeout=8, follow_redirects=True).text
    except Exception:  # noqa: BLE001
        return hit[1] if hit else ""
    with _lock:
        _cache[url] = (time.time(), text)
    return text


def sources() -> list[dict]:
    """[{name, colour, urls}] — "Family" first, then each person with a calendar."""
    out = []
    family = [u for u in re.split(r"[;\s]+", str(store.get("calendars") or "")) if u.strip()]
    if family:
        out.append({"name": "Family", "urls": family})
    try:
        from . import faces
        for p in faces.people():
            if str(p.get("calendar") or "").strip():
                out.append({"name": p["name"], "urls": [p["calendar"].strip()]})
    except Exception:  # noqa: BLE001 — Visual ID not set up
        pass
    for i, s in enumerate(out):
        s["colour"] = PALETTE[i % len(PALETTE)]
    return out


def days(start: dt.date, count: int) -> dict:
    from .tools.web import events_on
    count = max(1, min(MAX_DAYS, int(count)))
    srcs = sources()
    feeds = [(s, [_fetch(u) for u in s["urls"]]) for s in srcs]
    out_days = []
    for n in range(count):
        day = start + dt.timedelta(days=n)
        events = []
        for s, texts in feeds:
            seen = set()
            for text in texts:
                if not text:
                    continue
                for t, title in events_on(text, day):
                    key = (t, title.lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    events.append({"time": "" if t == "all day" else t, "all_day": t == "all day",
                                   "title": title, "who": s["name"], "colour": s["colour"]})
        events.sort(key=lambda e: (not e["all_day"], e["time"], e["who"]))
        out_days.append({"date": day.isoformat(), "events": events})
    return {"connected": bool(srcs), "members": [{"name": s["name"], "colour": s["colour"]} for s in srcs],
            "start": start.isoformat(), "days": out_days}
