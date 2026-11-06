"""Seeing and using the OTHER Ghost devices in the house."""
from __future__ import annotations

import json
import time

from . import Ctx, tool
from .. import store
from ..hub import find_device, hub


def describe_devices(exclude: str = "") -> str:
    lines = []
    for d in store.devices():
        if d["id"] == exclude:
            continue
        on = hub.is_online(d["id"])
        caps = [c.get("name") for c in (hub.conns[d["id"]].capabilities if on else d["capabilities"])]
        rooms = _rooms(d["id"]) if on else []
        spk = [f"{x['name']}" + (f" ({x['area']})" if x.get("area") else "") for x in _speakers(d["id"])] if on else []
        lines.append(f"- {d['name']} ({d['kind']}, {'online' if on else 'offline'})"
                     + (f": {', '.join(c for c in caps if c)}" if caps else "")
                     + (f" - room speakers in: {', '.join(rooms)} (announce to a room by its name)" if rooms else "")
                     + (f" - speakers you can name: {', '.join(spk)}" if spk else ""))
    return "\n".join(lines) or "No other devices are paired yet."


def _rooms(device_id: str) -> list[str]:
    c = hub.conns.get(device_id)
    return [str(r) for r in (c.info.get("rooms") or [])][:30] if c else []


def _speakers(device_id: str) -> list[dict]:
    """Individual speakers behind a hub (Home Assistant): [{"id": entity_id, "name": "Kitchen Echo", "area": "Kitchen"}]."""
    c = hub.conns.get(device_id)
    return [x for x in (c.info.get("speakers") or []) if isinstance(x, dict) and x.get("id")][:60] if c else []


def _norm(s: str) -> str:
    s = " " + str(s or "").lower().replace("'s", "").replace("’s", "") + " "
    for word in (" the ", " my ", " speaker ", " device "):
        s = s.replace(word, " ")
    return " ".join(s.split())


def find_speaker(name: str) -> tuple[str, dict] | None:
    """(hub device id, speaker) for ONE named speaker, e.g. 'kitchen echo dot', 'Zoey's echo'.
    A bare room name ('kitchen') is left to find_room, which speaks once per room."""
    n = _norm(name)
    if not n:
        return None
    rooms = {r.lower() for did in list(hub.conns) for r in _rooms(did)}
    best = None
    for did in list(hub.conns):
        for sp in _speakers(did):
            sn = _norm(sp.get("name", ""))
            if not sn:
                continue
            if sn == n:
                return did, sp
            if n in rooms:
                continue                    # "kitchen" means the room, not one speaker in it
            words = set(n.split())
            if (n in sn or sn in n or words <= set(sn.split())) and (best is None or len(sn) < len(best[2])):
                best = (did, sp, sn)
    return (best[0], best[1]) if best else None


def find_room(name: str) -> tuple[str, str] | None:
    """(hub device id, room) for a room speaker by room name, e.g. 'bedroom'."""
    n = str(name or "").strip().lower()
    for word in ("the ", "speaker", "screen"):
        n = n.replace(word, " ")
    n = " ".join(n.split())
    if not n:
        return None
    for did in list(hub.conns):
        for r in _rooms(did):
            if r.lower() == n or n in r.lower() or r.lower() in n:
                return did, r
    return None


@tool("list_devices", "List the user's Ghost devices (desktops, phones, tablets, room speakers), whether "
                      "they are online, and what each can do.")
def list_devices(args: dict, ctx: Ctx) -> str:
    return describe_devices(exclude=ctx.device_id)


@tool("device_action", """Run an action on ANOTHER Ghost device — e.g. lock the office PC, ring the
phone, show something on the kitchen tablet, open an app on the desktop. `device` is its name (or
'phone', 'desktop', 'tablet'); `action` is one of that device's capabilities from list_devices.""",
      {"type": "OBJECT", "properties": {
          "device": {"type": "STRING"}, "action": {"type": "STRING"},
          "args": {"type": "OBJECT", "description": "Arguments for the action", "properties": {}}},
       "required": ["device", "action"]})
def device_action(args: dict, ctx: Ctx) -> str:
    d = find_device(args.get("device", ""))
    if d is None:
        return f"No online device matches '{args.get('device')}'. Online: " + \
            (", ".join(c.name for c in hub.online()) or "none")
    if d["id"] == ctx.device_id:
        return "That's this device — call the action directly instead."
    caps = {c.get("name"): c for c in hub.conns[d["id"]].capabilities}
    action = str(args.get("action", ""))
    if action not in caps:
        return f"{d['name']} can't '{action}'. It can: {', '.join(caps) or 'nothing yet'}."
    raw = args.get("args") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {"value": raw}
    res = hub.call_sync(d["id"], action, raw)
    if res.get("ok"):
        return f"{d['name']}: {res.get('result') or 'done'}"
    return f"{d['name']} couldn't do it: {res.get('error', 'unknown error')}"


@tool("announce", """Say something out loud on another device, on ONE named speaker ('kitchen echo dot'),
in a room ('bedroom', 'office' = every speaker there, once), or on every device ('all'): 'tell the
kitchen tablet dinner's ready', 'play on Zoey's Echo: bedtime', 'tell the bedroom it's time to get up',
'announce to the house that we're leaving in five minutes'. Speaker names are in list_devices.""",
      {"type": "OBJECT", "properties": {
          "text": {"type": "STRING"},
          "device": {"type": "STRING", "description": "Device name, room name, or 'all' (default)"}},
       "required": ["text"]})
def announce(args: dict, ctx: Ctx) -> str:
    text = str(args.get("text", "")).strip()
    target = str(args.get("device") or "all").strip().lower()
    msg = {"type": "say", "text": text, "from": ctx.device_name, "at": time.time()}
    if target in ("all", "everyone", "house", "everywhere"):
        n = hub.broadcast_sync(msg, except_id=ctx.device_id)
        return f"Announced on {n} device(s)." if n else "No other devices are online."
    sp = find_speaker(target)
    if sp:                                  # one Echo / screen by its own name
        did, s = sp
        ok = hub.send_sync(did, {**msg, "speaker": s["id"], "room": s.get("area", "")})
        return f"Said it on {s['name']}." if ok else f"{s['name']} is offline."
    d = find_device(target)
    if d is None:
        room = find_room(target)
        if room:
            did, r = room
            ok = hub.send_sync(did, {**msg, "room": r})
            return f"Said it in the {r}." if ok else f"The {r} speaker is offline."
        names = [x["name"] for did in list(hub.conns) for x in _speakers(did)]
        return (f"No online device, speaker or room matches '{target}'."
                + (f" Speakers: {', '.join(names[:20])}." if names else ""))
    return f"Said it on {d['name']}." if hub.send_sync(d["id"], msg) else f"{d['name']} is offline."


@tool("device_events", "Recent things devices reported: phone notifications, battery levels, alerts.",
      {"type": "OBJECT", "properties": {"type": {"type": "STRING", "description": "Optional: notification | battery | alert"}}})
def device_events(args: dict, ctx: Ctx) -> str:
    rows = store.recent_events(15, args.get("type") or None)
    names = {d["id"]: d["name"] for d in store.devices(include_revoked=True)}
    out = [f"{time.strftime('%H:%M', time.localtime(e['at']))} {names.get(e['device_id'], '?')} "
           f"{e['type']}: {json.dumps(e['data'])[:160]}" for e in rows]
    return "\n".join(out) or "Nothing reported recently."


@tool("find_phone", """Find a phone: make it ring loudly (even on silent), vibrate and flash until someone presses
'Found it', and say whether it's at home. 'Where's my phone?', 'find Hayley's phone', 'ring my phone'.
`whose` is a name or 'my'/'' for the person asking. Use stop=true for 'stop ringing / found it'.""",
      {"type": "OBJECT", "properties": {
          "whose": {"type": "STRING", "description": "Name of the person, or empty for the person asking"},
          "stop": {"type": "BOOLEAN"}}})
def find_phone(args: dict, ctx: Ctx) -> str:
    import time as _t
    from .. import quiet, store
    whose = str(args.get("whose") or "").strip().lower()
    whose = "" if whose in ("my", "mine", "me", "i") else whose.replace("'s", "").replace("’s", "")
    phones = [d for d in store.devices() if d["kind"] == "phone" and d.get("approved", 1)]
    if not phones:
        return "There's no phone paired with Ghost yet."
    if whose:
        pick = [d for d in phones if whose in d["name"].lower() or d["name"].lower().split()[0] in whose]
    else:
        # "my phone": the asker's own if they're on a phone, else the owner's phones, else the only one
        pick = [d for d in phones if d["id"] == ctx.device_id] or \
            [d for d in phones if quiet.is_mine(d["kind"], d["name"])] or (phones if len(phones) == 1 else [])
    if not pick:
        return "Whose phone? Paired phones: " + ", ".join(d["name"] for d in phones)
    # the ones that can actually ring first (connected, newest)
    pick.sort(key=lambda d: (d["id"] not in hub.conns, -float(d.get("last_seen") or 0)))
    out = []
    room = (ctx.extra or {}).get("room") or ctx.device_name
    for d in pick[:2]:
        c = hub.conns.get(d["id"])
        if c is None or not any(x.get("name") == "ring_phone" for x in c.capabilities):
            seen = _t.time() - float(d.get("last_seen") or 0)
            ago = "just now" if seen < 120 else f"{int(seen // 60)} minutes ago" if seen < 3600 else \
                f"{int(seen // 3600)} hours ago" if seen < 86400 else f"{int(seen // 86400)} days ago"
            out.append(f"{d['name']} isn't connected to Ghost right now (last seen {ago}) — it may be off, "
                       "out of battery or away from home.")
            continue
        res = hub.call_sync(d["id"], "ring_phone", {"stop": bool(args.get("stop")), "from": room}, timeout=12)
        out.append(f"{d['name']}: {res.get('result') or 'ringing'}" if res.get("ok")
                   else f"{d['name']} couldn't ring: {res.get('error', 'no answer')}")
    return " ".join(out)
