"""Security camera tools: show a live feed on a device, or look and answer."""
from __future__ import annotations

from . import Ctx, tool
from .. import cameras, store, watcher
from ..hub import find_device, hub


def _names(cams) -> str:
    return ", ".join(c.name for c in cams) or "none"


@tool("list_cameras", "The household's security cameras (from Home Assistant and the camera system).")
def list_cameras(args: dict, ctx: Ctx) -> str:
    cams = cameras.all_cameras()
    if not cams:
        return ("No cameras are set up. Add them in Home Assistant, or on the Ghost Server admin page "
                "under Security cameras.")
    return f"{len(cams)} cameras: {_names(cams)}"


@tool("show_camera", """Show a LIVE security camera feed on a screen — 'show me the front door', 'pull up the
cameras', 'put the driveway on the kitchen tablet'. camera = a camera name, or 'all' for a grid of every camera.
device = where to show it (a device name like 'kitchen tablet', 'office PC'); leave empty for the device the user
is talking to.""",
      {"type": "OBJECT", "properties": {
          "camera": {"type": "STRING", "description": "Camera name, or 'all'"},
          "device": {"type": "STRING", "description": "Optional: which screen; empty = this device"}}})
def show_camera(args: dict, ctx: Ctx) -> str:
    all_cams = cameras.all_cameras()
    if not all_cams:
        return "No cameras are set up yet — add them in Home Assistant or on the Ghost Server admin page."
    cams = cameras.find(str(args.get("camera") or "all"), all_cams)
    if not cams:
        return f"No camera matches '{args.get('camera')}'. Cameras: {_names(all_cams)}."
    want = str(args.get("device") or "").strip()
    dev = find_device(want) if want else {"id": ctx.device_id, "name": ctx.device_name}
    if not dev or not dev.get("id"):
        return f"No online device matches '{want}'."
    if not hub.is_online(dev["id"]):
        return f"{dev['name']} isn't connected right now."
    caps = {c.get("name") for c in hub.conns[dev["id"]].capabilities}
    path = "/cam/" + cameras.issue(cams)
    title = cams[0].name if len(cams) == 1 else "Cameras"
    if "open_camera_view" in caps:
        res = hub.call_sync(dev["id"], "open_camera_view", {"path": path, "title": title})
    elif "open_link" in caps:
        res = hub.call_sync(dev["id"], "open_link", {"url": path})
    else:
        return f"{dev['name']} can't show camera feeds yet — update its Ghost app."
    if not res.get("ok"):
        return f"{dev['name']} couldn't open it: {res.get('error', 'no answer')}"
    what = cams[0].name if len(cams) == 1 else f"all {len(cams)} cameras"
    return f"Showing {what} live on {dev['name']}. ({res.get('result', 'opened')})"


@tool("check_camera", """Look through a security camera right now and answer a question about what it sees —
'is anyone at the front door?', 'is the car in the driveway?', 'did a parcel arrive?'. This is a ONE-OFF look at
this moment. To keep watching and be told later ('let me know if someone comes to the door'), use watch_camera.""",
      {"type": "OBJECT", "properties": {
          "camera": {"type": "STRING", "description": "Camera name"},
          "question": {"type": "STRING", "description": "What to look for"}},
       "required": ["camera"]})
def check_camera(args: dict, ctx: Ctx) -> str:
    all_cams = cameras.all_cameras()
    cams = cameras.find(str(args.get("camera") or ""), all_cams)
    if not cams:
        return f"No camera matches '{args.get('camera')}'. Cameras: {_names(all_cams)}."
    out = []
    for cam in cams[:4]:
        try:
            out.append(f"{cam.name}: {cameras.describe(cam, str(args.get('question') or ''))}")
        except Exception as e:  # noqa: BLE001
            out.append(f"{cam.name}: couldn't get a picture ({e})")
    return "\n".join(out)


def _notify_list(who: str, ctx: Ctx) -> list[str]:
    """me (default) = the asking device + every tablet; everyone = all devices; or one device by name."""
    w = (who or "me").strip().lower()
    devs = store.devices()
    if w in ("everyone", "all", "house", "everywhere"):
        return [d["id"] for d in devs]
    if w not in ("me", "", "here", "this device"):
        d = find_device(w, online_only=False)
        if d:
            return [d["id"]]
    ids = [ctx.device_id] if ctx.device_id else []
    return ids + [d["id"] for d in devs if d["kind"] == "tablet" and d["id"] not in ids]


@tool("watch_camera", """Keep watching a security camera and speak up when something happens — 'watch the front
door tonight', 'let me know if a car pulls into the driveway in the next hour', 'tell me when the parcel arrives'.
Ghost checks for movement every few seconds and only alerts for things worth knowing (people, cars, parcels,
animals — or what look_for says), on the asking device and the tablets. camera = name or 'all'.""",
      {"type": "OBJECT", "properties": {
          "camera": {"type": "STRING", "description": "Camera name, or 'all'"},
          "minutes": {"type": "NUMBER", "description": "How long to watch (default 60, max 720)"},
          "until": {"type": "STRING", "description": "Or watch until a time: '7am', '23:30', 'morning'"},
          "look_for": {"type": "STRING", "description": "Optional: what matters, e.g. 'a parcel', 'cars', 'the kids'"},
          "notify": {"type": "STRING", "description": "me (default: this device + tablets) | everyone | a device name"}},
       "required": ["camera"]})
def watch_camera(args: dict, ctx: Ctx) -> str:
    all_cams = cameras.all_cameras()
    if not all_cams:
        return "No cameras are set up yet — add them in Home Assistant or on the Ghost Server admin page."
    cams = cameras.find(str(args.get("camera") or ""), all_cams)
    if not cams:
        return f"No camera matches '{args.get('camera')}'. Cameras: {_names(all_cams)}."
    if not store.get("anthropic_api_key"):
        return "Watching needs Claude to look at the pictures — add the Anthropic key on the admin page first."
    mins = watcher.minutes_from(args)
    notify = _notify_list(str(args.get("notify") or "me"), ctx)
    started = [watcher.start(c, mins, str(args.get("look_for") or ""), notify, ctx.device_id) for c in cams[:6]]
    who = store.device(notify[0])["name"] if notify and store.device(notify[0]) else "this device"
    extra = f" and {len(notify) - 1} other device(s)" if len(notify) > 1 else ""
    names = ", ".join(w.cam.name for w in started)
    return (f"Watching {names} until {watcher.until_text(started[0].until)}"
            + (f", looking for {started[0].look_for}" if started[0].look_for else "")
            + f". Alerts go to {who}{extra}. Say 'stop watching' to end it.")


@tool("stop_watching", "Stop watching a camera (or 'all').",
      {"type": "OBJECT", "properties": {"camera": {"type": "STRING", "description": "Camera name, or empty/'all'"}}})
def stop_watching(args: dict, ctx: Ctx) -> str:
    stopped = watcher.stop(str(args.get("camera") or ""))
    return f"Stopped watching {', '.join(stopped)}." if stopped else "I wasn't watching any camera."


@tool("camera_watch_status", "Which cameras Ghost is watching right now, until when, and what it has seen.")
def camera_watch_status(args: dict, ctx: Ctx) -> str:
    ws = list(watcher.WATCHES.values())
    if not ws:
        return "Not watching any cameras."
    lines = []
    for w in ws:
        seen = "; ".join(s for _, s in w.recent[-3:]) or "nothing to report yet"
        lines.append(f"{w.cam.name}: until {watcher.until_text(w.until)}, {w.alerts} alert(s) — {seen}"
                     + (f" (problem: {w.error})" if w.error else ""))
    return "\n".join(lines)
