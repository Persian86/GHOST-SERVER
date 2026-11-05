"""
Do Not Disturb — two quiet modes, decided here for every device.

  me      your personal Do Not Disturb: your phone(s) and PC(s) go quiet
  house   house quiet mode: every device goes quiet, the hub tablets too

Each one is on when it was switched on (for a while, until a time, or until
switched off) or when its nightly schedule says so. Switching it off during the
scheduled hours skips the rest of tonight's schedule.

While a device is quiet, the server holds what it would have said
("say" / "task_done" messages) and gives a short catch-up when quiet ends.
Some things always get through:
  * anything marked urgent        (safety alerts, urgent camera alerts)
  * favourite people              (a message or announcement from them)
  * emergency words               ("emergency", "hospital", "call me now"…)
The phone applies the same rules to calls and messages it sees itself, and
mirrors them into Android's own Do Not Disturb (starred contacts ring).
"""
from __future__ import annotations

import datetime as dt
import re
import threading
import time
from zoneinfo import ZoneInfo

from . import store

SCOPES = ("me", "house")
DEFAULT_WORDS = ("emergency, urgent, hospital, ambulance, accident, police, fire, help me, call me now, "
                 "call me asap, call me back now, 000, 911, hurt, injured")
PERSONAL_KINDS = ("phone", "desktop", "laptop")       # what "me" covers by default; tablets are shared

_held: dict[str, list[dict]] = {}          # device_id → messages held while quiet
_held_lock = threading.Lock()
_last: dict[str, bool] = {}                # device_id → was quiet at the last check
_last_scope: dict[str, bool] = {}


# ── time ─────────────────────────────────────────────────────────────────────
def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(str(store.get("timezone") or "Australia/Melbourne"))
    except Exception:  # noqa: BLE001
        return ZoneInfo("Australia/Melbourne")


def _local(t: float | None = None) -> dt.datetime:
    return dt.datetime.fromtimestamp(time.time() if t is None else t, _tz())


def parse_clock(s: str) -> tuple[int, int] | None:
    """'7', '7am', '7:30 pm', '19:30', '0630' → (hour, minute)."""
    s = str(s or "").strip().lower().replace(".", ":").replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})(?::?(\d{2}))?(am|pm)?", s)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    return h, mi


def next_clock(s: str, now: float | None = None) -> float | None:
    """The next moment it's that time of day (today or tomorrow), as epoch seconds."""
    hm = parse_clock(s)
    if hm is None:
        return None
    n = _local(now)
    t = n.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    if t <= n:
        t += dt.timedelta(days=1)
    return t.timestamp()


def parse_schedule(s: str) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """'22:00-06:30' / '10pm to 6:30am' → ((22,0),(6,30)). Empty or 'off' → None."""
    s = str(s or "").strip().lower()
    if not s or s in ("off", "none", "no"):
        return None
    parts = re.split(r"\s*(?:-|–|to|until)\s*", s)
    if len(parts) != 2:
        return None
    a, b = parse_clock(parts[0]), parse_clock(parts[1])
    return (a, b) if a and b and a != b else None


def schedule_text(s: str) -> str:
    p = parse_schedule(s)
    if not p:
        return ""
    return f"{_clock_words(*p[0])}–{_clock_words(*p[1])}"


def _clock_words(h: int, m: int) -> str:
    ap = "am" if h < 12 else "pm"
    return f"{h % 12 or 12}{(':%02d' % m) if m else ''}{ap}"


def _window(sched: str, now: float) -> tuple[bool, float]:
    """(inside the window now, when this window ends — epoch)."""
    p = parse_schedule(sched)
    if not p:
        return False, 0.0
    n = _local(now)
    (sh, sm), (eh, em) = p
    start_today = n.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end_today = n.replace(hour=eh, minute=em, second=0, microsecond=0)
    if start_today < end_today:                     # same-day window, e.g. 13:00–15:00
        inside = start_today <= n < end_today
        end = end_today
    else:                                           # overnight, e.g. 22:00–06:30
        if n >= start_today:
            inside, end = True, end_today + dt.timedelta(days=1)
        elif n < end_today:
            inside, end = True, end_today
        else:
            inside, end = False, end_today + dt.timedelta(days=1)
    return inside, end.timestamp()


def until_words(t: float) -> str:
    if t == -1:
        return "until you turn it off"
    n = _local()
    u = _local(t)
    day = "" if u.date() == n.date() else (" tomorrow" if u.date() == (n + dt.timedelta(days=1)).date()
                                           else u.strftime(" %A"))
    return f"until {_clock_words(u.hour, u.minute)}{day}"


# ── state ────────────────────────────────────────────────────────────────────
def state(scope: str, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    until = float(store.get(f"quiet_{scope}_until") or 0)
    sched = str(store.get(f"quiet_{scope}_schedule") or "")
    skip = float(store.get(f"quiet_{scope}_skip_until") or 0)
    manual = until == -1 or until > now
    inside, end = _window(sched, now)
    by_schedule = inside and skip <= now and not manual
    active = manual or by_schedule
    ends = until if manual else (end if by_schedule else 0)
    return {"scope": scope, "active": active, "why": "manual" if manual else ("schedule" if by_schedule else ""),
            "until": ends, "until_text": until_words(ends) if active else "",
            "schedule": sched, "schedule_text": schedule_text(sched)}


def is_mine(kind: str, name: str = "") -> bool:
    """Does the personal Do Not Disturb cover this device? The devices named on the admin
    page (Do Not Disturb → "My devices"), or — if none are named — every phone and PC."""
    mine = [m.lower() for m in _split(store.get("quiet_me_devices"))]
    if mine and name:
        n = name.lower()
        return any(m == n or m in n for m in mine)
    return str(kind or "").lower() in PERSONAL_KINDS


def device_quiet(kind: str, now: float | None = None, name: str = "") -> bool:
    if state("house", now)["active"]:
        return True
    return is_mine(kind, name) and state("me", now)["active"]


def set_quiet(scope: str, on: bool, minutes: float | None = None, until: str | None = None) -> dict:
    """Turn a quiet mode on (for N minutes / until a time / until turned off) or off."""
    if scope not in SCOPES:
        raise ValueError("scope is 'me' or 'house'")
    now = time.time()
    if on:
        t = -1.0
        if minutes:
            t = now + float(minutes) * 60
        elif until:
            nt = next_clock(until, now)
            if nt is None:
                raise ValueError(f"I didn't understand the time '{until}'")
            t = nt
        store.put(f"quiet_{scope}_until", t)
        store.put(f"quiet_{scope}_skip_until", 0)
    else:
        store.put(f"quiet_{scope}_until", 0)
        inside, end = _window(str(store.get(f"quiet_{scope}_schedule") or ""), now)
        store.put(f"quiet_{scope}_skip_until", end if inside else 0)
    return state(scope)


def set_schedule(scope: str, sched: str) -> dict:
    if scope not in SCOPES:
        raise ValueError("scope is 'me' or 'house'")
    sched = str(sched or "").strip()
    p = parse_schedule(sched)
    if sched and sched.lower() not in ("off", "none", "no") and p is None:
        raise ValueError(f"I didn't understand the schedule '{sched}' — try '10pm to 6:30am'")
    store.put(f"quiet_{scope}_schedule", f"{p[0][0]:02d}:{p[0][1]:02d}-{p[1][0]:02d}:{p[1][1]:02d}" if p else "")
    store.put(f"quiet_{scope}_skip_until", 0)
    return state(scope)


# ── who / what gets through ──────────────────────────────────────────────────
def _split(s) -> list[str]:
    if isinstance(s, list):
        items = s
    else:
        items = re.split(r"[,\n;]+", str(s or ""))
    return [i.strip() for i in items if str(i).strip()]


def favourites() -> list[str]:
    """Favourite people: the names typed on the admin page plus the phone's starred contacts."""
    seen, out = set(), []
    for n in _split(store.get("quiet_favourites")) + _split(store.get("quiet_favourites_phone")):
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def words() -> list[str]:
    return _split(store.get("quiet_words") or DEFAULT_WORDS)


def is_favourite(who: str) -> bool:
    w = str(who or "").lower()
    if not w:
        return False
    for f in favourites():
        fl = f.lower()
        # "Hayley" matches "Hayley's phone" / "Hayley Smith"; whole words only
        if re.search(rf"\b{re.escape(fl)}\b", w) or re.search(rf"\b{re.escape(w)}\b", fl):
            return True
    return False


def has_emergency_words(text: str) -> bool:
    t = " " + re.sub(r"\s+", " ", str(text or "").lower()) + " "
    return any(re.search(rf"(?<![a-z0-9]){re.escape(wd.lower())}(?![a-z0-9])", t) for wd in words())


def gets_through(msg: dict) -> bool:
    """Would this message reach a device that's in Do Not Disturb?"""
    if msg.get("urgent"):
        return True
    return is_favourite(str(msg.get("from") or "")) or has_emergency_words(str(msg.get("text") or ""))


# ── holding and catching up ──────────────────────────────────────────────────
def hold(device_id: str, msg: dict) -> None:
    with _held_lock:
        lst = _held.setdefault(device_id, [])
        lst.append({**msg, "held_at": time.time()})
        del lst[:-40]
    store.add_event(device_id, "quiet_held", {"from": msg.get("from", ""), "text": str(msg.get("text", ""))[:300]})


def take_held(device_id: str) -> list[dict]:
    with _held_lock:
        return _held.pop(device_id, [])


def catch_up_text(items: list[dict]) -> str:
    if not items:
        return ""
    parts = []
    for m in items[:5]:
        who = str(m.get("from") or "").strip()
        txt = str(m.get("text") or "").strip().rstrip(".")
        parts.append(f"{who}: {txt}" if who and who.lower() not in ("ghost", "admin") else txt)
    more = f" — and {len(items) - 5} more" if len(items) > 5 else ""
    n = len(items)
    return (f"While Do Not Disturb was on, {n} thing{'s' if n != 1 else ''} came in. "
            + ". ".join(parts) + more + ".")


def snapshot(kind: str = "", name: str = "") -> dict:
    """Everything a device needs to show and apply Do Not Disturb."""
    me, house = state("me"), state("house")
    return {"type": "quiet", "me": me, "house": house,
            "active": device_quiet(kind, name=name) if kind else (me["active"] or house["active"]),
            "applies_to_me": is_mine(kind, name),
            "favourites": favourites(), "words": words(),
            "safety": bool(store.get("safety_alerts")), "android_sync": bool(store.get("quiet_android_sync"))}


async def tick(force: bool = False) -> None:
    """Push the state to every device when it changed, and give a catch-up to the
    ones that just came out of Do Not Disturb. Runs every 20 s and after every change."""
    from .hub import hub
    sig = {s: state(s)["active"] for s in SCOPES}
    scope_changed = sig != _last_scope
    _last_scope.clear(); _last_scope.update(sig)
    for c in list(hub.conns.values()):
        now_quiet = device_quiet(c.kind, name=c.name)
        was = _last.get(c.device_id)
        _last[c.device_id] = now_quiet
        if force or scope_changed or was is None or was != now_quiet:
            await hub.send(c.device_id, snapshot(c.kind, c.name))
        if was and not now_quiet:
            text = catch_up_text(take_held(c.device_id))
            if text:
                await hub.send(c.device_id, {"type": "say", "from": "Ghost", "text": text, "at": time.time()})


def tick_soon() -> None:
    """From a worker thread: run tick() on the server's loop."""
    import asyncio
    from .hub import hub
    if hub.loop is not None:
        try:
            asyncio.run_coroutine_threadsafe(tick(force=True), hub.loop)
        except RuntimeError:
            pass


# ── safety sensors in Home Assistant ─────────────────────────────────────────
SAFETY_CLASSES = {"smoke": "smoke detected", "carbon_monoxide": "carbon monoxide detected", "gas": "gas detected",
                  "moisture": "water leak detected", "safety": "safety alarm", "heat": "heat alarm"}
_safety_on: set[str] = set()
_safety_seeded = False


def safety_check(states: list[dict]) -> list[str]:
    """New safety problems since the last check, as spoken sentences."""
    global _safety_seeded
    now_on: dict[str, str] = {}
    for s in states:
        eid = str(s.get("entity_id", ""))
        attrs = s.get("attributes") or {}
        name = attrs.get("friendly_name") or eid
        if eid.startswith("binary_sensor.") and s.get("state") == "on" and attrs.get("device_class") in SAFETY_CLASSES:
            now_on[eid] = f"{name} — {SAFETY_CLASSES[attrs['device_class']]}"
        elif eid.startswith("alarm_control_panel.") and s.get("state") == "triggered":
            now_on[eid] = f"{name} — the alarm has gone off"
    new = [] if not _safety_seeded else [txt for eid, txt in now_on.items() if eid not in _safety_on]
    _safety_on.clear(); _safety_on.update(now_on)
    _safety_seeded = True
    return new


async def safety_loop(every: float = 15.0) -> None:
    import asyncio
    from .hub import hub
    while True:
        try:
            if store.get("safety_alerts") and store.get("ha_url") and store.get("ha_token"):
                def fetch():
                    from .tools.home import _req
                    r = _req("GET", "/api/states")
                    return r.json() if r.status_code == 200 else []
                states = await asyncio.to_thread(fetch)
                for text in safety_check(states):
                    store.add_event("", "safety_alert", {"text": text})
                    await hub.broadcast({"type": "say", "from": "Safety alert", "text": text + "!",
                                         "urgent": True, "safety": True, "at": time.time()})
        except Exception as e:  # noqa: BLE001
            print(f"[Ghost Server] safety check skipped: {e}")
        await asyncio.sleep(every)


async def quiet_loop(every: float = 20.0) -> None:
    import asyncio
    while True:
        try:
            await tick()
        except Exception as e:  # noqa: BLE001
            print(f"[Ghost Server] quiet tick failed: {e}")
        await asyncio.sleep(every)
