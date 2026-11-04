"""Security camera tools: show a live feed on a device, or look and answer."""
from __future__ import annotations

from . import Ctx, tool
from .. import cameras
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
this moment. You can NOT keep watching a camera or alert the user later when something happens — if they ask for
that, say plainly it isn't set up yet, and offer to check now instead.""",
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
