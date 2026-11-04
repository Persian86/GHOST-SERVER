"""Camera watch — Ghost keeps an eye on a camera and speaks up when something happens.

"Watch the front door tonight", "tell me if a car pulls into the driveway in the next hour".

How it stays cheap and quiet:
  1. Every few seconds the server grabs a small frame and compares it with a slowly
     updating picture of the empty scene (pure pixel maths, no AI, runs on the NAS).
  2. Only when enough of the picture changes does Claude look at the frame, with what
     the user wants to know about and what was already reported.
  3. Claude decides whether it's worth telling the household (a person, a car, a parcel,
     an animal…) or just wind, a shadow or headlights. Only then does an alert go out:
     spoken + shown on the phone and tablets, and the tablet pops up the live view.
Each camera has a cool-down so a car parked in the drive isn't reported every minute.
"""
from __future__ import annotations

import base64
import io
import json
import re
import threading
import time
from dataclasses import dataclass, field

from . import cameras, store

POLL_SECONDS = 3.0
MAX_HOURS = 12
LOOK_COOLDOWN = 20          # s between Claude looks at the same camera
ALERT_COOLDOWN = 120        # s after an alert before the same camera alerts again
CHANGE_THRESHOLD = 0.035    # fraction of the (small, grey) picture that must change
PIXEL_DELTA = 28            # how different a pixel must be (0-255) to count as changed


@dataclass
class Watch:
    id: str
    cam: cameras.Cam
    until: float
    look_for: str
    notify: list[str]            # device ids to tell
    started_by: str
    started_at: float = field(default_factory=time.time)
    background: list[float] | None = None
    last_look: float = 0.0
    last_alert: float = 0.0
    recent: list[tuple[float, str]] = field(default_factory=list)   # (time, what was reported)
    looks: int = 0
    alerts: int = 0
    error: str = ""
    stop: threading.Event = field(default_factory=threading.Event)


WATCHES: dict[str, Watch] = {}
_lock = threading.Lock()


# ── the cheap part: did the picture change? ──────────────────────────────────
def tiny(jpeg: bytes, w: int = 64, h: int = 36) -> list[float]:
    """A 64×36 colour thumbnail as a flat list r,g,b,r,g,b… (colour matters: a red shirt on a grey path
    can be almost the same brightness)."""
    from PIL import Image
    im = Image.open(io.BytesIO(jpeg)).convert("RGB").resize((w, h))
    return [float(c) for c in im.tobytes()]          # works on every Pillow version


def changed_fraction(bg: list[float], cur: list[float]) -> float:
    """Share of the picture that's really different — ignoring the whole scene getting brighter or
    darker (clouds, auto-exposure, dusk)."""
    if not bg or len(bg) != len(cur) or len(cur) % 3:
        return 0.0
    n = len(cur) // 3
    shift = [(sum(cur[c::3]) - sum(bg[c::3])) / n for c in range(3)]
    changed = 0
    for i in range(0, len(cur), 3):
        if max(abs(cur[i + c] - shift[c] - bg[i + c]) for c in range(3)) > PIXEL_DELTA:
            changed += 1
    return changed / n


def blend(bg: list[float], cur: list[float], rate: float = 0.08) -> list[float]:
    return [a + (b - a) * rate for a, b in zip(bg, cur)]


# ── the smart part: is it worth telling anyone? ──────────────────────────────
def judge(w: Watch, jpeg: bytes) -> dict:
    """Claude looks at the frame. Returns {"alert": bool, "summary": str}."""
    from .tools.brain import claude
    earlier = "; ".join(f"{time.strftime('%I:%M %p', time.localtime(t))}: {s}" for t, s in w.recent[-4:]) or "nothing yet"
    focus = w.look_for or "people, vehicles arriving or leaving, deliveries/parcels, animals, anything unusual"
    prompt = (f"You are watching the home security camera \"{w.cam.name}\" for the household. Something in the picture "
              f"just changed. They asked to be told about: {focus}.\n"
              f"Already reported in the last while: {earlier}.\n"
              "Look at this frame. Alert ONLY for something new that matches what they asked about. Do NOT alert for "
              "wind, rain, moving shadows, headlights, insects, a tree, or something already reported that's still there.\n"
              'Reply with JSON only: {"alert": true|false, "summary": "one short spoken sentence, e.g. '
              "'Someone's at the front door — looks like a courier with a parcel.'\"}")
    img = base64.b64encode(jpeg).decode()
    text, _ = claude(fast=False).complete([{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}]}], max_tokens=200)
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except Exception:  # noqa: BLE001
        d = {}
    return {"alert": bool(d.get("alert")), "summary": str(d.get("summary") or "").strip()}


def alert(w: Watch, summary: str, jpeg: bytes) -> None:
    from .hub import hub
    w.alerts += 1
    w.last_alert = time.time()
    w.recent.append((w.last_alert, summary))
    # keep the picture, so "show me what you saw" works and the phone can open it
    path = ""
    try:
        from . import config
        folder = config.FILES_DIR / time.strftime("%Y-%m")
        folder.mkdir(parents=True, exist_ok=True)
        f = folder / f"watch-{cameras._slug(w.cam.name)}-{time.strftime('%Y%m%d-%H%M%S')}.jpg"
        f.write_bytes(jpeg)
        path = "/files/" + store.add_file(f.name, f, w.started_by)
    except Exception:  # noqa: BLE001
        pass
    store.add_event(w.started_by, "camera_alert", {"camera": w.cam.name, "summary": summary, "picture": path})
    msg = {"type": "say", "from": f"{w.cam.name} camera", "text": summary, "at": time.time(),
           "camera": w.cam.name, "picture": path}
    live_path = "/cam/" + cameras.issue([w.cam])
    for dev_id in w.notify:
        if not hub.is_online(dev_id):
            continue
        hub.send_sync(dev_id, msg)
        row = store.device(dev_id)
        d = dict(row) if row else {}
        caps = {c.get("name") for c in hub.conns[dev_id].capabilities} if dev_id in hub.conns else set()
        if d.get("kind") == "tablet" and "open_camera_view" in caps:      # the hub pops up the live view
            hub.call_sync(dev_id, "open_camera_view", {"path": live_path, "title": w.cam.name}, timeout=8)


# ── the loop ─────────────────────────────────────────────────────────────────
def _run(w: Watch, poll: float = POLL_SECONDS) -> None:
    fails = 0
    while not w.stop.is_set() and time.time() < w.until:
        try:
            jpeg = cameras.snapshot(w.cam, timeout=12)
            cur = tiny(jpeg)
            fails = 0
            w.error = ""
            if w.background is None:
                w.background = cur
            else:
                frac = changed_fraction(w.background, cur)
                now = time.time()
                busy = frac >= CHANGE_THRESHOLD
                if busy and now - w.last_look >= LOOK_COOLDOWN and now - w.last_alert >= ALERT_COOLDOWN:
                    w.last_look = now
                    w.looks += 1
                    verdict = judge(w, jpeg)
                    if verdict["alert"] and verdict["summary"]:
                        alert(w, verdict["summary"], jpeg)
                # the empty scene adapts slowly (fast when nothing's happening)
                w.background = blend(w.background, cur, 0.04 if busy else 0.15)
        except Exception as e:  # noqa: BLE001
            fails += 1
            w.error = str(e)[:200]
            if fails == 10:
                from .hub import hub
                for dev_id in w.notify[:1]:
                    hub.send_sync(dev_id, {"type": "say", "from": "Ghost cameras",
                                           "text": f"I can't see the {w.cam.name} camera any more, so I've stopped watching it."})
                break
        w.stop.wait(poll if fails == 0 else min(30, poll * (fails + 1)))
    with _lock:
        if WATCHES.get(w.id) is w:
            WATCHES.pop(w.id, None)


def start(cam: cameras.Cam, minutes: float, look_for: str, notify: list[str], started_by: str,
          poll: float = POLL_SECONDS) -> Watch:
    minutes = max(1.0, min(float(minutes or 60), MAX_HOURS * 60))
    wid = cam.id
    with _lock:
        old = WATCHES.get(wid)
        if old:
            old.stop.set()
        w = Watch(wid, cam, time.time() + minutes * 60, look_for.strip(), notify, started_by)
        WATCHES[wid] = w
    threading.Thread(target=_run, args=(w, poll), daemon=True, name=f"watch-{wid}").start()
    return w


def stop(name: str = "") -> list[str]:
    n = name.strip().lower()
    stopped = []
    with _lock:
        for wid, w in list(WATCHES.items()):
            if not n or n in ("all", "everything", "cameras") or n in w.cam.name.lower() or w.cam.name.lower() in n:
                w.stop.set()
                WATCHES.pop(wid, None)
                stopped.append(w.cam.name)
    return stopped


def until_text(t: float) -> str:
    return time.strftime("%I:%M %p", time.localtime(t)).lstrip("0")


def minutes_from(args: dict) -> float:
    """'minutes' or 'until' (e.g. '7am', '06:30', 'morning', 'tonight')."""
    if args.get("minutes"):
        try:
            return float(args["minutes"])
        except (TypeError, ValueError):
            pass
    u = str(args.get("until") or "").strip().lower()
    if not u:
        return 60
    import datetime as dt
    now = dt.datetime.now()
    if u in ("tonight", "overnight", "morning", "the morning", "all night"):
        target = now.replace(hour=7, minute=0, second=0, microsecond=0)
    else:
        m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", u)
        if not m:
            return 60
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        target = now.replace(hour=h % 24, minute=mi, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds() / 60
