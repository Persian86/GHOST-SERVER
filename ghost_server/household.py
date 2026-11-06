"""
The household's shared things: timers and alarms, lists, and notes for each other.

Everything lives on the server, so a timer set on a room screen can be seen on the
kitchen tablet and stopped from your phone, and the shopping list is the same everywhere.

Whenever something changes, every device gets a fresh snapshot over its live link:
    {"type": "timers", "items": [...]}    {"type": "lists", "lists": {...}}    {"type": "notes", "items": [...]}
A timer that goes off is sent to the device (or room) that set it:
    {"type": "timer", "event": "ring", "timer": {...}}     (always rings, even in Do Not Disturb)
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
import secrets
import time

from . import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS timers (
    id TEXT PRIMARY KEY, kind TEXT, label TEXT, ends_at REAL, device_id TEXT, room TEXT,
    status TEXT, alarm_time TEXT DEFAULT '', days TEXT DEFAULT '', rang_at REAL DEFAULT 0,
    rings INTEGER DEFAULT 0, created_at REAL);
CREATE TABLE IF NOT EXISTS list_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, list TEXT, text TEXT, done INTEGER DEFAULT 0,
    added_by TEXT DEFAULT '', at REAL);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, from_name TEXT DEFAULT '', to_name TEXT DEFAULT '',
    at REAL, done INTEGER DEFAULT 0);
"""
RING_FOR = 10 * 60          # a ringing timer stops by itself after 10 minutes
ROOM_REPEAT = 45            # room speakers re-announce every 45 s, three times
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_ready = False


def _schema() -> None:
    global _ready
    if not _ready:
        store._con().executescript(SCHEMA)
        _ready = True


def _push(msg: dict) -> None:
    """Tell every device. Safe from worker threads and from the server's own event loop."""
    from .hub import hub
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None and running is hub.loop:
        asyncio.ensure_future(hub.broadcast(msg))
    else:
        hub.broadcast_sync(msg)


# ─────────────────────────────────────────────────────────────────────────────
#  Timers and alarms
# ─────────────────────────────────────────────────────────────────────────────
def _row(r) -> dict:
    d = dict(r)
    d["remaining"] = max(0, round(d["ends_at"] - time.time())) if d["status"] == "running" else 0
    return d


def timers(include_done: bool = False) -> list[dict]:
    _schema()
    sql = "SELECT * FROM timers" + ("" if include_done else " WHERE status IN ('running','ringing')")
    return [_row(r) for r in store.q(sql + " ORDER BY ends_at")]


def timers_snapshot() -> dict:
    return {"type": "timers", "items": timers(), "server_time": time.time()}


def _parse_days(s: str) -> str:
    s = str(s or "").lower().strip()
    if not s or s in ("once", "today", "tomorrow"):
        return ""
    if s in ("daily", "every day", "everyday", "all"):
        return ",".join(DAYS)
    if s in ("weekdays", "workdays", "week days"):
        return ",".join(DAYS[:5])
    if s in ("weekends", "weekend"):
        return "sat,sun"
    found = [d for d in DAYS if d in s]
    return ",".join(found)


def _next_alarm(hhmm: str, days: str, after: float | None = None) -> float | None:
    from .quiet import _local, parse_clock
    hm = parse_clock(hhmm)
    if hm is None:
        return None
    now = _local(after)
    for add in range(0, 8):
        cand = (now + dt.timedelta(days=add)).replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        if cand <= now:
            continue
        if not days or DAYS[cand.weekday()] in days.split(","):
            return cand.timestamp()
    return None


def set_timer(seconds: float, label: str = "", device_id: str = "", room: str = "") -> dict:
    _schema()
    seconds = max(1, min(float(seconds), 24 * 3600))
    t = {"id": "tm_" + secrets.token_hex(4), "kind": "timer", "label": (label or "").strip()[:40],
         "ends_at": time.time() + seconds, "device_id": device_id, "room": room, "status": "running"}
    store.x("INSERT INTO timers (id, kind, label, ends_at, device_id, room, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)", (t["id"], "timer", t["label"], t["ends_at"], device_id, room,
                                          "running", time.time()))
    _push(timers_snapshot())
    return t


def set_alarm(hhmm: str, label: str = "", days: str = "", device_id: str = "", room: str = "") -> dict | None:
    _schema()
    d = _parse_days(days)
    at = _next_alarm(hhmm, d)
    if at is None:
        return None
    tid = "al_" + secrets.token_hex(4)
    store.x("INSERT INTO timers (id, kind, label, ends_at, device_id, room, status, alarm_time, days, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", (tid, "alarm", (label or "").strip()[:40], at, device_id, room,
                                             "running", hhmm, d, time.time()))
    _push(timers_snapshot())
    return {"id": tid, "ends_at": at, "days": d}


def _find(match: str) -> list[dict]:
    m = str(match or "").strip().lower()
    live = timers()
    if m in ("", "all", "everything", "every"):
        return live
    hits = [t for t in live if t["id"] == m or (t["label"] and (m in t["label"].lower() or t["label"].lower() in m))]
    if not hits and m in ("timer", "the timer", "alarm", "the alarm"):
        hits = [t for t in live if t["kind"] == m.replace("the ", "")]
    return hits


def stop(timer_id: str) -> bool:
    """Dismiss a ringing timer (or cancel a running one). Repeating alarms move to their next day."""
    _schema()
    rows = store.q("SELECT * FROM timers WHERE id = ?", (timer_id,))
    if not rows:
        return False
    t = dict(rows[0])
    nxt = _next_alarm(t["alarm_time"], t["days"]) if t["kind"] == "alarm" and t["days"] else None
    if nxt:
        store.x("UPDATE timers SET status='running', ends_at=?, rings=0, rang_at=0 WHERE id=?", (nxt, timer_id))
    else:
        store.x("UPDATE timers SET status='done' WHERE id=?", (timer_id,))
    _push(timers_snapshot())
    _push({"type": "timer", "event": "stopped", "timer": {"id": timer_id}})
    return True


def cancel_alarm_forever(timer_id: str) -> None:
    _schema()
    store.x("UPDATE timers SET status='cancelled' WHERE id=?", (timer_id,))
    _push(timers_snapshot())
    _push({"type": "timer", "event": "stopped", "timer": {"id": timer_id}})


def add_time(timer_id: str, seconds: float) -> bool:
    _schema()
    rows = store.q("SELECT * FROM timers WHERE id=?", (timer_id,))
    if not rows:
        return False
    t = dict(rows[0])
    base = max(time.time(), t["ends_at"]) if t["status"] == "running" else time.time()
    store.x("UPDATE timers SET ends_at=?, status='running', rings=0, rang_at=0 WHERE id=?", (base + seconds, timer_id))
    _push(timers_snapshot())
    _push({"type": "timer", "event": "stopped", "timer": {"id": timer_id}})
    return True


def describe(t: dict) -> str:
    from .quiet import _clock_words, _local
    label = f"{t['label']} " if t["label"] else ""
    if t["status"] == "ringing":
        return f"the {label}{t['kind']} is ringing"
    if t["kind"] == "alarm":
        when = _local(t["ends_at"])
        rep = f" ({t['days'].replace(',', ', ')})" if t["days"] else ""
        return f"{label}alarm at {_clock_words(when.hour, when.minute)} {when.strftime('%a')}{rep}"
    return f"{label}timer: {_dur(t['remaining'])} left"


def _dur(s: float) -> str:
    s = int(round(s))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    parts = ([f"{h} hour{'s' * (h != 1)}"] if h else []) + ([f"{m} minute{'s' * (m != 1)}"] if m else [])
    if not h and sec and m < 5:
        parts.append(f"{sec} second{'s' * (sec != 1)}")
    return " ".join(parts) or "under a second"


def ring_text(t: dict) -> str:
    what = f"Your {t['label']} {t['kind']}" if t["label"] else f"Your {t['kind']}"
    return f"{what} is {'going off' if t['kind'] == 'alarm' else 'done'}."


RINGS_NATIVELY = {"tablet"}      # apps that ring a timer themselves (with a Stop button); others hear it spoken


def _spoken(t: dict) -> bool:
    """Room speakers, phones and PCs announce a timer out loud (three times) instead of ringing."""
    from .hub import hub
    did = t.get("device_id") or ""
    if "~" in did:
        return True
    c = hub.conns.get(did)
    # the tablet always rings itself; a phone does when its app says so (Stop / +1 min on the notification)
    return c is not None and c.kind not in RINGS_NATIVELY and not c.info.get("rings_timers")


async def _ring(t: dict) -> None:
    from .hub import hub
    did = t["device_id"] or ""
    say = {"type": "say", "text": ring_text(t), "urgent": True, "from": "timer"}
    if "~" in did:                       # a room speaker behind Home Assistant
        await hub.send(did.split("~", 1)[0], {**say, "room": t["room"]})
    elif did and hub.is_online(did):
        if _spoken(t):
            await hub.send(did, say)
        else:
            await hub.send(did, {"type": "timer", "event": "ring", "timer": t, "text": ring_text(t)})
    else:                                # the device is gone: ring everywhere
        await hub.broadcast({"type": "timer", "event": "ring", "timer": t, "text": ring_text(t)})
        await hub.broadcast(say)


async def timer_loop(every: float = 1.0) -> None:
    _schema()
    while True:
        try:
            now = time.time()
            for r in store.q("SELECT * FROM timers WHERE status='running' AND ends_at <= ?", (now,)):
                store.x("UPDATE timers SET status='ringing', rang_at=?, rings=1 WHERE id=?", (now, r["id"]))
                t = _row(store.q("SELECT * FROM timers WHERE id=?", (r["id"],))[0])
                await _ring(t)
                await _push_async(timers_snapshot())
            for r in store.q("SELECT * FROM timers WHERE status='ringing'"):
                t = dict(r)
                spoken = _spoken(t)
                if spoken and t["rings"] < 3 and now - t["rang_at"] >= ROOM_REPEAT * t["rings"]:
                    store.x("UPDATE timers SET rings=rings+1 WHERE id=?", (t["id"],))
                    await _ring(_row(r))
                elif now - t["rang_at"] > (ROOM_REPEAT * 3 if spoken else RING_FOR):
                    await asyncio.to_thread(stop, t["id"])
        except Exception as e:  # noqa: BLE001 — keep the clock running
            print(f"[Ghost Server] timer loop: {e}")
        await asyncio.sleep(every)


async def _push_async(msg: dict) -> None:
    from .hub import hub
    await hub.broadcast(msg)


# ─────────────────────────────────────────────────────────────────────────────
#  Lists
# ─────────────────────────────────────────────────────────────────────────────
def _list_name(s: str) -> str:
    s = str(s or "shopping").strip().lower()
    s = re.sub(r"\b(the|my|our|list)\b", "", s).strip() or "shopping"
    return {"grocery": "shopping", "groceries": "shopping", "to-do": "to do", "todo": "to do",
            "to dos": "to do", "todos": "to do", "jobs": "to do"}.get(s, s)[:30]


def lists() -> dict[str, list[dict]]:
    _schema()
    out: dict[str, list[dict]] = {"shopping": [], "to do": []}
    for r in store.q("SELECT * FROM list_items ORDER BY done, id"):
        out.setdefault(r["list"], []).append(dict(r))
    return out


def lists_snapshot() -> dict:
    return {"type": "lists", "lists": lists()}


def add_items(list_name: str, text: str | list, by: str = "") -> list[str]:
    """Text is split on commas / "and"; a list is taken as-is ("salt and pepper" stays one item)."""
    _schema()
    name = _list_name(list_name)
    have = {r["text"].lower() for r in store.q("SELECT text FROM list_items WHERE list=? AND done=0", (name,))}
    added = []
    parts = text if isinstance(text, list) else re.split(r",|\band\b|\n", str(text or ""))
    for part in parts:
        item = " ".join(part.split())[:80]
        if item and item.lower() not in have:
            store.x("INSERT INTO list_items (list, text, added_by, at) VALUES (?,?,?,?)",
                    (name, item[0].upper() + item[1:], by, time.time()))
            have.add(item.lower())
            added.append(item)
    if added:
        _push(lists_snapshot())
    return added


def _match_items(list_name: str, text: str) -> list[dict]:
    name = _list_name(list_name)
    words = [w.strip().lower() for w in re.split(r",|\band\b", str(text or "")) if w.strip()]
    rows = [dict(r) for r in store.q("SELECT * FROM list_items WHERE list=?", (name,))]
    return [r for r in rows if any(w in r["text"].lower() or r["text"].lower() in w for w in words)]


def set_item(item_id: int, done: bool | None = None, delete: bool = False) -> bool:
    _schema()
    if delete:
        n = store.q("SELECT id FROM list_items WHERE id=?", (item_id,))
        store.x("DELETE FROM list_items WHERE id=?", (item_id,))
    else:
        n = store.q("SELECT id FROM list_items WHERE id=?", (item_id,))
        store.x("UPDATE list_items SET done=? WHERE id=?", (1 if done else 0, item_id))
    _push(lists_snapshot())
    return bool(n)


def clear_done(list_name: str) -> int:
    _schema()
    name = _list_name(list_name)
    n = len(store.q("SELECT id FROM list_items WHERE list=? AND done=1", (name,)))
    store.x("DELETE FROM list_items WHERE list=? AND done=1", (name,))
    _push(lists_snapshot())
    return n


# ─────────────────────────────────────────────────────────────────────────────
#  Notes for each other
# ─────────────────────────────────────────────────────────────────────────────
def notes(for_name: str = "", include_done: bool = False) -> list[dict]:
    _schema()
    rows = [dict(r) for r in store.q("SELECT * FROM notes" + ("" if include_done else " WHERE done=0")
                                     + " ORDER BY id DESC LIMIT 50")]
    if for_name:
        f = for_name.strip().lower()
        rows = [r for r in rows if not r["to_name"] or r["to_name"].lower() == f
                or r["to_name"].lower().split()[0] == f.split()[0]]
    return rows


def notes_snapshot() -> dict:
    return {"type": "notes", "items": notes()}


def leave_note(text: str, to_name: str = "", from_name: str = "") -> dict:
    _schema()
    to = "" if str(to_name or "").lower() in ("", "everyone", "all", "everybody", "the family", "family") \
        else " ".join(str(to_name).split())[:40]
    nid = store.x("INSERT INTO notes (text, from_name, to_name, at) VALUES (?,?,?,?)",
                  (" ".join(str(text).split())[:400], str(from_name or "")[:40], to, time.time()))
    _push(notes_snapshot())
    return {"id": nid, "to": to or "everyone"}


def done_note(note_id: int) -> bool:
    _schema()
    found = store.q("SELECT id FROM notes WHERE id=?", (note_id,))
    store.x("UPDATE notes SET done=1 WHERE id=?", (note_id,))
    _push(notes_snapshot())
    return bool(found)
