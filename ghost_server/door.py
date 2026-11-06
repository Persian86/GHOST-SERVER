"""Front door — doorbell presses, visitors and deliveries, spoken where you are.

  • Doorbell press: any Home Assistant entity that changes when the bell is pressed
    (binary_sensor.front_door_visitor, event.doorbell_ring, a Reolink/UniFi/Ring "ding" sensor…).
    Ghost grabs a picture, says who it is if Visual ID knows them ("It's Hayley at the front door"),
    otherwise describes them ("a courier with a parcel"). A press always gets through Do Not Disturb.
  • Visitors and deliveries without a press: the front-door camera is watched all the time with the
    same cheap motion check as camera watches (watcher.py); Claude only looks when the picture changes,
    and only people arriving, deliveries and parcels left or taken are reported.

Where it's said:
  • phones and PCs that are "mine" + the hub tablets get it with the picture (the tablet pops up the
    live view, the phone shows a notification you can tap);
  • Home Assistant speakers (Echos, room screens) get it in the room you're in: Home Assistant picks
    rooms where a motion/occupancy/presence sensor saw someone in the last few minutes, plus the room
    you last spoke to Ghost in; nobody detected anywhere → every room. "everywhere" in settings skips that.
"""
from __future__ import annotations

import base64
import re
import threading
import time

from . import cameras, store

BELL_COOLDOWN = 20          # s — a held / double press is one ring
RECENT_ROOM_FOR = 20 * 60   # s — "the room you last talked to Ghost in" counts for this long
LOOK_FOR = ("people arriving at or standing at the front door, a courier or delivery, a parcel being left "
            "or picked up. NOT people or cars just passing on the street, and not someone already reported "
            "who is still there")

_bell_state: dict[str, str] = {}
_last_ring: dict[str, float] = {}
_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
#  Settings
# ─────────────────────────────────────────────────────────────────────────────
def _split(s) -> list[str]:
    return [x.strip() for x in re.split(r"[,\n]", str(s or "")) if x.strip()]


def door_cameras() -> list[cameras.Cam]:
    names = _split(store.get("door_cameras"))
    if not names:
        return []
    cams = cameras.all_cameras()
    out: list[cameras.Cam] = []
    for n in names:
        for c in cameras.find(n, cams):
            if c not in out and n.lower() not in ("all",):
                out.append(c)
                break
    return out


def bell_entities() -> list[str]:
    return [e for e in _split(store.get("door_bell_entities")) if "." in e]


# ─────────────────────────────────────────────────────────────────────────────
#  Who's at the door?
# ─────────────────────────────────────────────────────────────────────────────
def known_faces(jpeg: bytes, cam: cameras.Cam) -> list[str]:
    """Names Visual ID recognises in the door picture (only if Visual ID is on)."""
    if not store.get("faces_enabled") or not jpeg:
        return []
    try:
        from . import faces
        r = faces.identify(jpeg, {"id": "door:" + cam.id, "name": cam.name})
        return [p["name"] for p in r.get("people", [])]
    except Exception:  # noqa: BLE001
        return []


def describe_visitor(jpeg: bytes, cam: cameras.Cam) -> str:
    """One short spoken sentence about who's at the door, or '' if Claude isn't available."""
    if not jpeg:
        return ""
    try:
        from .tools.brain import claude
        img = base64.b64encode(jpeg).decode()
        text, _ = claude(fast=True).complete([{"role": "user", "content": [
            {"type": "text", "text": f"Someone just rang the doorbell. This is the \"{cam.name}\" camera right now. "
                                     "In ONE short spoken sentence for the household, say who is at the door, e.g. "
                                     "'It's a courier with a parcel.', 'A man in a high-vis shirt.', "
                                     "'Two kids — looks like they're selling something.', 'I can't see anyone.' "
                                     "Never guess names. Reply with the sentence only."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}]}], max_tokens=80)
        return (text or "").strip().strip('"')[:200]
    except Exception:  # noqa: BLE001
        return ""


def _names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def place(cam: cameras.Cam) -> str:
    n = re.sub(r"\b(camera|cam|doorbell)\b", "", cam.name, flags=re.I).strip() or "front door"
    return n.lower() if n.lower().startswith(("front", "back", "side")) else n


# ─────────────────────────────────────────────────────────────────────────────
#  Telling the house
# ─────────────────────────────────────────────────────────────────────────────
def recent_room() -> str:
    """The room you last spoke to Ghost in (a Home Assistant speaker), if it was recent."""
    from .hub import hub
    cutoff = time.time() - RECENT_ROOM_FOR
    for t in reversed(store.recent_turns(40)):          # newest first
        did = str(t.get("device_id") or "")
        if "~" not in did or float(t.get("at") or 0) < cutoff:
            continue
        hub_id, slug = did.split("~", 1)
        c = hub.conns.get(hub_id)
        for r in (c.info.get("rooms") or []) if c else []:
            if re.sub(r"[^a-z0-9]+", "-", str(r).lower()).strip("-") == slug:
                return str(r)
    return ""


def deliver(text: str, cam: cameras.Cam, jpeg: bytes = b"", urgent: bool = False, kind: str = "visitor") -> dict:
    """Say it on my devices + tablets (with the picture) and on the speakers in the room I'm in."""
    from . import quiet
    from .hub import hub
    picture = ""
    if jpeg:
        from .watcher import save_picture
        picture = save_picture(cam.name, jpeg, prefix="door")
    store.add_event("", "camera_alert", {"camera": cam.name, "summary": text, "picture": picture,
                                        "urgent": urgent, "door": kind})
    base = {"type": "say", "from": f"{cam.name} camera", "text": text, "at": time.time(),
            "camera": cam.name, "picture": picture, "urgent": urgent, "door": kind}
    where = str(store.get("door_announce") or "near")
    sent = {"devices": [], "rooms": ""}
    live_path = ""
    for c in hub.online():
        if c.info.get("rooms") is not None or c.kind == "homeassistant":
            # speakers behind Home Assistant: "@near" lets HA pick the occupied rooms
            msg = {**base, "room": "@near" if where == "near" else "", "near_room": recent_room()}
            msg.pop("camera", None)                # an Echo can't show a picture
            if hub.send_sync(c.device_id, msg):
                sent["rooms"] = msg["room"] or "everywhere"
            continue
        if c.kind == "tablet" or quiet.is_mine(c.kind, c.name):
            if hub.send_sync(c.device_id, base):
                sent["devices"].append(c.name)
            caps = {x.get("name") for x in c.capabilities}
            if c.kind == "tablet" and "open_camera_view" in caps and (urgent or not quiet.device_quiet("tablet")):
                live_path = live_path or "/cam/" + cameras.issue([cam])
                hub.call_sync(c.device_id, "open_camera_view", {"path": live_path, "title": cam.name}, timeout=8)
    return sent


def ring(cam: cameras.Cam, source: str = "doorbell") -> str:
    """The doorbell was pressed (or "test the doorbell"): look, recognise, tell."""
    jpeg = b""
    try:
        jpeg = cameras.snapshot(cam, timeout=8)
    except Exception:  # noqa: BLE001
        pass
    names = known_faces(jpeg, cam)
    where = place(cam)
    if names:
        text = f"{_names(names)} {'is' if len(names) == 1 else 'are'} at the {where}."
    else:
        seen = describe_visitor(jpeg, cam)
        text = f"Someone's at the {where}." + (f" {seen}" if seen and "can't see" not in seen.lower() else "")
    deliver(text, cam, jpeg, urgent=True, kind="bell" if source == "doorbell" else "test")
    return text


def on_motion(w, summary: str, jpeg: bytes, urgent: bool) -> None:
    """The always-on door watch saw something worth saying (called from watcher.alert)."""
    names = known_faces(jpeg, w.cam)
    if names:
        summary = f"{_names(names)} {'is' if len(names) == 1 else 'are'} at the {place(w.cam)}."
    kind = "delivery" if re.search(r"parcel|package|deliver|courier|box", summary, re.I) else "visitor"
    deliver(summary, w.cam, jpeg, urgent=urgent, kind=kind)


# ─────────────────────────────────────────────────────────────────────────────
#  Doorbell presses + keeping the door watch running
# ─────────────────────────────────────────────────────────────────────────────
def pressed(entity_id: str, state: str, last: str | None) -> bool:
    """Did this state change mean "the bell was pressed"?"""
    if state in ("unavailable", "unknown", "") or last is None or state == last:
        return False
    if entity_id.startswith("binary_sensor."):
        return state == "on"
    return True                      # event.*, sensor "last ding" timestamps, input_button, button…


def check_bells(states: dict[str, str]) -> list[str]:
    """Feed the latest states of the bell entities; returns the ring texts that went out."""
    out = []
    cams = None
    for eid in bell_entities():
        if eid not in states:
            continue
        st = str(states[eid])
        with _lock:
            last = _bell_state.get(eid)
            _bell_state[eid] = st
            fire = pressed(eid, st, last) and time.time() - _last_ring.get(eid, 0) > BELL_COOLDOWN
            if fire:
                _last_ring[eid] = time.time()
        if fire:
            cams = cams if cams is not None else door_cameras()
            if cams:
                out.append(ring(cams[0]))
            else:
                deliver("Someone's at the front door.", cameras.Cam("door", "Front door", "ha"), urgent=True,
                        kind="bell")
                out.append("Someone's at the front door.")
    return out


def sync_watch() -> list[str]:
    """Keep exactly the configured door cameras watched (motion → visitors/deliveries)."""
    from . import watcher
    want = {c.id: c for c in door_cameras()} if store.get("door_watch") else {}
    running = {w.cam.id: w for w in list(watcher.WATCHES.values()) if w.door}
    for cid, w in running.items():
        if cid not in want:
            w.stop.set()
            watcher.WATCHES.pop(w.id, None)
    for cid, cam in want.items():
        if cid not in running:
            watcher.start(cam, 0, LOOK_FOR, [], "door", door=True)
    return [c.name for c in want.values()]


async def door_loop(every: float = 2.0) -> None:
    import asyncio
    n = 0
    while True:
        try:
            if n % 15 == 0:                              # every ~30 s: start/stop the camera watch
                await asyncio.to_thread(sync_watch)
            ents = bell_entities()
            if ents and store.get("ha_url") and store.get("ha_token"):
                def fetch():
                    from .tools.home import _req
                    got = {}
                    for e in ents:
                        r = _req("GET", f"/api/states/{e}")
                        if r.status_code == 200:
                            got[e] = r.json().get("state", "")
                    return got
                states = await asyncio.to_thread(fetch)
                await asyncio.to_thread(check_bells, states)
        except Exception as e:  # noqa: BLE001
            if n % 30 == 0:
                print(f"[Ghost Server] front door check skipped: {e}")
        n += 1
        await asyncio.sleep(every)


def history(limit: int = 8) -> list[dict]:
    return [{"at": e["at"], "camera": e["data"].get("camera", ""), "summary": e["data"].get("summary", ""),
             "picture": e["data"].get("picture", ""), "kind": e["data"].get("door", "")}
            for e in store.recent_events(60, "camera_alert") if e["data"].get("door")][:limit]


def status() -> dict:
    from . import watcher
    return {"cameras": [c.name for c in door_cameras()], "bells": bell_entities(),
            "watching": [w.cam.name for w in watcher.WATCHES.values() if w.door],
            "announce": store.get("door_announce"), "recent": history(5)}

