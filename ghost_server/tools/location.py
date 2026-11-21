"""Where is everyone? — family locations from Home Assistant, with a live map."""
from __future__ import annotations

from . import Ctx, tool
from .. import store, tracking
from ..hub import find_device, hub


def _show(path: str, title: str, want: str, ctx: Ctx) -> str:
    """Open the live map on a screen. Returns a short note for Ghost to say (or '')."""
    dev = find_device(want) if want else {"id": ctx.device_id, "name": ctx.device_name}
    if not dev or not dev.get("id") or not hub.is_online(dev["id"]):
        return f" ({want or 'This device'} isn't connected, so I couldn't open the map.)" if want else ""
    caps = {c.get("name") for c in hub.conns[dev["id"]].capabilities}
    if "open_camera_view" in caps:
        res = hub.call_sync(dev["id"], "open_camera_view", {"path": path, "title": title}, timeout=8)
    elif "open_link" in caps:
        res = hub.call_sync(dev["id"], "open_link", {"url": path}, timeout=8)
    else:
        return ""                     # e.g. a room speaker: the spoken answer is enough
    return f" The live map is open on {dev['name']}." if res.get("ok") else ""


@tool("where_is", """Where a family member is right now, from their phone's location in Home Assistant —
"where's Adam?", "is Hayley still at work?", "how far away is Adam?", "show me where Adam is". Says the place,
whether they're moving, distance and driving time home, and opens a LIVE map on the screen (their dot moves in
real time, with an Open in Google Maps button). person = a name, or "everyone". device = another screen to show
the map on (empty = this device). show_map = false for a quick spoken answer only.""",
      {"type": "OBJECT", "properties": {
          "person": {"type": "STRING", "description": "Name, e.g. Adam, or 'everyone'"},
          "device": {"type": "STRING", "description": "Optional: which screen shows the map"},
          "show_map": {"type": "BOOLEAN", "description": "Open the live map (default true)"}},
       "required": ["person"]})
def where_is(args: dict, ctx: Ctx) -> str:
    if not store.get("tracking_enabled"):
        return "Location sharing is turned off in Ghost's settings."
    states = tracking._ha("GET", "/api/states").json()
    want = str(args.get("person") or "").strip()
    if want.lower() in ("", "everyone", "everybody", "all", "the family", "family"):
        ps = tracking.people(states)
        if not ps:
            return "Nobody is set up in Home Assistant yet — add people (Settings → People) and the Home Assistant app on their phones."
        return "\n".join(tracking.describe(tracking.snapshot(p, states)) for p in ps)
    p = tracking.find_person(want, states)
    if not p:
        names = ", ".join(str(x["attributes"].get("friendly_name") or x["entity_id"]) for x in tracking.people(states))
        return f"I don't know anyone called {want}." + (f" People I can find: {names}." if names else "")
    snap = tracking.snapshot(p, states)
    store.add_event(ctx.device_id, "location_viewed", {"person": snap["name"], "by": ctx.device_name})
    text = tracking.describe(snap)
    if args.get("show_map", True) is not False and snap["lat"] is not None:
        tracking.ping(p, every=0)                # ask the phone for a fresh fix straight away
        path = "/track/" + tracking.issue(p["entity_id"], ctx.device_name)
        text += _show(path, f"{snap['name']} · live", str(args.get("device") or "").strip(), ctx)
    return text
