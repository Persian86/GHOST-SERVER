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
        lines.append(f"- {d['name']} ({d['kind']}, {'online' if on else 'offline'})"
                     + (f": {', '.join(c for c in caps if c)}" if caps else ""))
    return "\n".join(lines) or "No other devices are paired yet."


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


@tool("announce", """Say something out loud on another device, or on every device ('all'): 'tell the
kitchen tablet dinner's ready', 'announce to the house that we're leaving in five minutes'.""",
      {"type": "OBJECT", "properties": {
          "text": {"type": "STRING"},
          "device": {"type": "STRING", "description": "Device name, or 'all' (default)"}},
       "required": ["text"]})
def announce(args: dict, ctx: Ctx) -> str:
    text = str(args.get("text", "")).strip()
    target = str(args.get("device") or "all").strip().lower()
    msg = {"type": "say", "text": text, "from": ctx.device_name, "at": time.time()}
    if target in ("all", "everyone", "house", "everywhere"):
        n = hub.broadcast_sync(msg, except_id=ctx.device_id)
        return f"Announced on {n} device(s)." if n else "No other devices are online."
    d = find_device(target)
    if d is None:
        return f"No online device matches '{target}'."
    return f"Said it on {d['name']}." if hub.send_sync(d["id"], msg) else f"{d['name']} is offline."


@tool("device_events", "Recent things devices reported: phone notifications, battery levels, alerts.",
      {"type": "OBJECT", "properties": {"type": {"type": "STRING", "description": "Optional: notification | battery | alert"}}})
def device_events(args: dict, ctx: Ctx) -> str:
    rows = store.recent_events(15, args.get("type") or None)
    names = {d["id"]: d["name"] for d in store.devices(include_revoked=True)}
    out = [f"{time.strftime('%H:%M', time.localtime(e['at']))} {names.get(e['device_id'], '?')} "
           f"{e['type']}: {json.dumps(e['data'])[:160]}" for e in rows]
    return "\n".join(out) or "Nothing reported recently."
