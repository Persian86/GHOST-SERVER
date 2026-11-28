"""Security Guard mode — Ghost watches every camera for people who aren't family.

    "Ghost, guard mode on"  ·  the Guard button on a phone or tablet  ·  automatically when everyone leaves

WHAT IT DOES
    Every camera is checked every couple of seconds. A cheap pixel comparison (the same
    one the camera watch uses) decides whether anything moved; only then does a local
    person detector (OpenCV's YOLOX) look for people. Each person found gets:

      • a face check against the household's face prints (faces.py) — family are skipped;
      • an appearance print (OpenCV's person re-identification model, clothes and build)
        so the same person can be followed from one camera to the next.

    A person seen twice in a row who isn't family becomes a "sighting": the household gets a
    notification with the picture (and Home Assistant's phone notifications, which reach the
    phones away from home). When that same person turns up on another camera, Ghost says so:
    "the same person is now at the Backyard camera — first seen at the Driveway 10:02 pm".

    Everything here runs on the AI PC. The optional one-line description ("a man in a dark
    hoodie") comes from the brain — Claude, or the local model when Ghost is unplugged.

HONEST LIMITS
    Outdoor cameras often can't see a face clearly. A family member whose face isn't visible
    is reported as "someone I don't recognise" — better a false alarm than a missed stranger.
    When a face is later recognised, that person's track is marked family and goes quiet.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import cameras, config, store, watcher

# ── tuning ───────────────────────────────────────────────────────────────────
POLL_SECONDS = 2.0
RECHECK_SECONDS = 20        # look for people now and then even without movement (someone standing still)
PERSON_SCORE = 0.55         # YOLOX person confidence
MIN_PERSON_PX = 48          # ignore people shorter than this (far away, a few pixels)
SAME_PERSON = 0.60          # appearance-print cosine similarity for "the same person"
TRACK_FORGET = 15 * 60      # a track not seen for this long is closed
CONFIRM_HITS = 2            # frames a stranger must be seen in before an alert (no one-frame ghosts)
REALERT_SAME_CAMERA = 10 * 60
AUTO_CHECK_SECONDS = 60

MODELS = {
    "people": ("object_detection_yolox_2022nov.onnx",
               "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063", "object_detection_yolox/"),
    "reid": ("person_reid_youtu_2021nov.onnx",
             "0579683334d4b9440221606dcb461656dd0dc64143b18f48faedaced9b4f580d", "person_reid_youtureid/"),
}
ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models/"


def _model_path(kind: str) -> Path:
    name, sha, folder = MODELS[kind]
    for d in (Path(os.environ.get("GHOST_MODELS", "/app/models")), config.DATA_DIR / "models"):
        p = d / name
        if p.exists() and p.stat().st_size > 1000:
            return p
    import httpx
    d = config.DATA_DIR / "models"
    d.mkdir(parents=True, exist_ok=True)
    data = httpx.get(ZOO + folder + name, follow_redirects=True, timeout=600).content
    if hashlib.sha256(data).hexdigest() != sha:
        raise RuntimeError(f"the downloaded model {name} failed its checksum")
    p = d / name
    p.write_bytes(data)
    return p


# ── the eyes: people + appearance prints (tests swap `engine` for a stand-in) ─
class Engine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._det = self._reid = None
        self._grid = None

    def _load(self):
        import cv2
        import numpy as np
        if self._det is None:
            self._det = cv2.dnn.readNet(str(_model_path("people")))
            self._reid = cv2.dnn.readNet(str(_model_path("reid")))
            g, s = [], []
            for st in (8, 16, 32):
                n = 640 // st
                yv, xv = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
                g.append(np.stack((xv, yv), 2).reshape(-1, 2))
                s.append(np.full((n * n, 1), st))
            self._grid = (np.concatenate(g).astype(np.float32), np.concatenate(s).astype(np.float32))

    def people(self, jpeg: bytes) -> list[dict]:
        """[{box: [x, y, w, h], score, emb: [768 floats, unit length], crop: jpeg bytes}]"""
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return []
        with self._lock:
            self._load()
            h, w = img.shape[:2]
            r = min(640 / h, 640 / w)
            pad = np.full((640, 640, 3), 114, np.uint8)
            pad[:int(h * r), :int(w * r)] = cv2.resize(img, (int(w * r), int(h * r)))
            self._det.setInput(cv2.dnn.blobFromImage(pad, 1.0, (640, 640), swapRB=False))
            o = self._det.forward()[0]
            grid, stride = self._grid
            xy = (o[:, :2] + grid) * stride
            wh = np.exp(o[:, 2:4]) * stride
            score = o[:, 4] * o[:, 5]                     # objectness × "person" (COCO class 0)
            keep = score > PERSON_SCORE
            boxes = (np.concatenate([xy - wh / 2, wh], 1)[keep] / r)
            scores = score[keep]
            idx = cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(), PERSON_SCORE, 0.45)
            out = []
            for i in np.array(idx).flatten()[:8]:
                x, y, bw, bh = [int(v) for v in boxes[i]]
                x, y = max(0, x), max(0, y)
                bw, bh = min(bw, w - x), min(bh, h - y)
                if bh < MIN_PERSON_PX or bw < 8:
                    continue
                crop = img[y:y + bh, x:x + bw]
                blob = cv2.dnn.blobFromImage(crop, 1 / 255.0, (128, 256), swapRB=True)
                blob = (blob - np.array([0.485, 0.456, 0.406]).reshape(1, 3, 1, 1)) / \
                    np.array([0.229, 0.224, 0.225]).reshape(1, 3, 1, 1)
                self._reid.setInput(blob.astype(np.float32))
                v = self._reid.forward().flatten()
                v = v / (np.linalg.norm(v) or 1.0)
                ok, enc = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                out.append({"box": [x, y, bw, bh], "score": float(scores[i]), "emb": v.tolist(),
                            "crop": enc.tobytes() if ok else b""})
            return out


engine = Engine()


def _cos(a: list[float], b: list[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b)))


def family_name(crop: bytes) -> str:
    """The household member whose face is in this person picture, or ''."""
    try:
        from . import faces
        for _box, feat in faces.engine.faces(crop)[:2]:
            m = faces.match(feat)
            if m:
                return m[0]
    except Exception:  # noqa: BLE001 — no face models / no prints: nobody is "family" by face
        pass
    return ""


# ── who's been seen ──────────────────────────────────────────────────────────
@dataclass
class Track:
    id: int
    emb: list[float]
    first_seen: float
    last_seen: float
    first_cam: str
    last_cam: str
    hits: int = 1
    family: str = ""
    alerted_at: dict = field(default_factory=dict)      # camera → when we last told the household
    route: list = field(default_factory=list)           # [(time, camera)]
    description: str = ""

    def blend(self, emb: list[float]) -> None:
        m = [0.8 * a + 0.2 * b for a, b in zip(self.emb, emb)]
        n = sum(x * x for x in m) ** 0.5 or 1.0
        self.emb = [x / n for x in m]


class Tracks:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.items: list[Track] = []
        self._next = 1

    def clear(self) -> None:
        with self._lock:
            self.items.clear()

    def see(self, cam: str, emb: list[float], now: float) -> tuple[Track, bool, bool]:
        """The track this person belongs to → (track, is_new, moved_camera)."""
        with self._lock:
            self.items = [t for t in self.items if now - t.last_seen < TRACK_FORGET]
            best, score = None, SAME_PERSON
            for t in self.items:
                s = _cos(t.emb, emb)
                if s >= score:
                    best, score = t, s
            if best is None:
                t = Track(self._next, list(emb), now, now, cam, cam, route=[(now, cam)])
                self._next += 1
                self.items.append(t)
                return t, True, False
            moved = best.last_cam != cam
            best.blend(emb)
            best.last_seen, best.hits = now, best.hits + 1
            if moved:
                best.route.append((now, cam))
                best.last_cam = cam
            return best, False, moved


tracks = Tracks()


# ── settings & state ─────────────────────────────────────────────────────────
DEFAULTS = {"auto_away": True, "cameras": [], "describe": True, "ha_notify": []}


def settings() -> dict:
    s = store.get("guard_settings") or {}
    if not isinstance(s, dict):
        s = {}
    return {**DEFAULTS, **{k: v for k, v in s.items() if k in DEFAULTS}}


def save_settings(changes: dict) -> dict:
    s = settings()
    if "auto_away" in changes:
        s["auto_away"] = bool(changes["auto_away"])
    if "describe" in changes:
        s["describe"] = bool(changes["describe"])
    if "cameras" in changes:
        s["cameras"] = [str(c)[:80] for c in (changes["cameras"] or [])][:32]
    if "ha_notify" in changes:
        s["ha_notify"] = [str(c)[:80] for c in (changes["ha_notify"] or []) if str(c).startswith("mobile_app_")][:8]
    store.put("guard_settings", s)
    return s


_state_lock = threading.Lock()
_stop = threading.Event()
_threads: list[threading.Thread] = []
_status: dict = {"on": False, "since": 0.0, "by": "", "auto": False, "cameras": [], "errors": {}, "sightings": 0}


def state() -> dict:
    with _state_lock:
        out = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in _status.items()}
    out["people"] = [{"id": t.id, "family": t.family, "first_seen": t.first_seen, "last_seen": t.last_seen,
                      "route": [c for _, c in t.route], "description": t.description}
                     for t in tracks.items if not t.family and t.hits >= CONFIRM_HITS]
    return out


def _cams() -> list[cameras.Cam]:
    want = [c.lower() for c in settings()["cameras"]]
    cams = cameras.all_cameras()
    if want:
        cams = [c for c in cams if c.name.lower() in want or c.id.lower() in want]
    return [c for c in cams if "map" not in (c.name + " " + c.id).lower()]


def start(by: str = "", auto: bool = False) -> dict:
    """Turn Guard mode on (idempotent). Returns the state."""
    with _state_lock:
        if _status["on"]:
            return dict(_status)
        cams = _cams()
        _stop.clear()
        tracks.clear()
        _status.update(on=True, since=time.time(), by=by, auto=auto, cameras=[c.name for c in cams], errors={},
                       sightings=0)
        store.put("guard_on", {"by": by, "auto": auto, "since": _status["since"]})
        _threads.clear()
        for c in cams:
            t = threading.Thread(target=_watch, args=(c,), daemon=True, name=f"guard-{c.id}")
            _threads.append(t)
            t.start()
    from . import modes
    modes.broadcast()                                    # Ghost puts on the guard's cap
    _announce(f"Guard mode is on. I'm watching {len(cams)} camera{'s' if len(cams) != 1 else ''} "
              "and I'll tell you about anyone I don't recognise.", quiet=auto)
    return state()


def stop(by: str = "") -> dict:
    with _state_lock:
        was = _status["on"]
        _stop.set()
        _status.update(on=False, auto=False)
        store.put("guard_on", None)
    from . import modes
    modes.broadcast()
    if was:
        n = _status.get("sightings", 0)
        _announce("Guard mode is off." + (f" I reported {n} sighting{'s' if n != 1 else ''} while it was on." if n else
                                          " Nobody I didn't recognise came by."), quiet=True)
    return state()


def restore() -> None:
    """After a restart: Guard comes back on if it was on."""
    saved = store.get("guard_on")
    if isinstance(saved, dict):
        start(saved.get("by", ""), bool(saved.get("auto")))


# ── telling the household ────────────────────────────────────────────────────
def _devices() -> list[str]:
    from .hub import hub
    return [d["id"] for d in store.devices() if d["kind"] in ("phone", "tablet", "desktop", "agent")
            and hub.is_online(d["id"])]


def _announce(text: str, quiet: bool = False, extra: dict | None = None) -> None:
    from .hub import hub
    msg = {"type": "notify" if quiet else "say", "from": "Ghost guard", "title": "Ghost guard", "text": text,
           "at": time.time(), **(extra or {})}
    for d in _devices():
        try:
            hub.send_sync(d, msg)
        except Exception:  # noqa: BLE001
            pass


def _ha_notify(title: str, text: str) -> None:
    """Home Assistant's phone notifications reach the phones away from home."""
    try:
        from .tracking import _ha
        services = settings()["ha_notify"]
        if not services:                       # not chosen: every phone with the Home Assistant app
            for dom in _ha("GET", "/api/services").json():
                if dom.get("domain") == "notify":
                    services = sorted(k for k in dom.get("services", {}) if k.startswith("mobile_app_"))[:8]
        for svc in services:
            _ha("POST", f"/api/services/notify/{svc}", json={"title": title, "message": text,
                                                              "data": {"tag": "ghost-guard", "priority": "high"}})
    except Exception:  # noqa: BLE001
        pass


def describe(jpeg: bytes) -> str:
    """One short line about the person ('a man in a dark hoodie carrying a bag'), or ''."""
    if not settings()["describe"]:
        return ""
    try:
        import base64
        from .tools.brain import claude
        img = base64.b64encode(jpeg).decode()
        text, _ = claude(fast=True).complete([{"role": "user", "content": [
            {"type": "text", "text": "Describe the person in this security-camera picture in at most 12 words "
                                     "(clothing, build, anything they carry). No names, no guesses about who it is. "
                                     "Reply with the description only."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}]}], max_tokens=60)
        return " ".join((text or "").split())[:120].rstrip(".")
    except Exception:  # noqa: BLE001
        return ""


def _when(t: float) -> str:
    return time.strftime("%I:%M %p", time.localtime(t)).lstrip("0")


def report(t: Track, cam: cameras.Cam, frame: bytes, crop: bytes, moved: bool, now: float) -> None:
    if not t.description:
        t.description = describe(crop or frame)
    who = t.description or "someone I don't recognise"
    if moved and len(t.route) > 1:
        text = (f"The same person is now at the {cam.name} camera — first seen at the {t.first_cam} "
                f"camera at {_when(t.first_seen)}. {who[0].upper() + who[1:]}.")
    else:
        text = f"Someone I don't recognise is at the {cam.name} camera: {who}."
    picture = watcher.save_picture(cam.name, frame, "", prefix="guard")
    t.alerted_at[cam.id] = now
    with _state_lock:
        _status["sightings"] = _status.get("sightings", 0) + 1
    store.add_event("", "guard_sighting", {"camera": cam.name, "text": text, "picture": picture, "track": t.id,
                                           "route": [c for _, c in t.route]})
    live_path = "/cam/" + cameras.issue([cam])
    _announce(text, extra={"camera": cam.name, "picture": picture, "urgent": False, "guard": True,
                           "track": t.id, "live": live_path})
    _ha_notify("Ghost guard", text)
    from .hub import hub
    for d in _devices():
        row = store.device(d)
        caps = {c.get("name") for c in hub.conns[d].capabilities} if d in hub.conns else set()
        if row and row["kind"] == "tablet" and "open_camera_view" in caps:
            try:
                hub.call_sync(d, "open_camera_view", {"path": live_path, "title": cam.name}, timeout=8)
            except Exception:  # noqa: BLE001
                pass


def consider(cam: cameras.Cam, frame: bytes, now: float | None = None) -> list[Track]:
    """Look for people in one frame; report strangers. Returns the tracks touched (for tests)."""
    now = now or time.time()
    touched = []
    for p in engine.people(frame):
        t, new, moved = tracks.see(cam.name, p["emb"], now)
        touched.append(t)
        if not t.family:
            name = family_name(p["crop"]) if p.get("crop") else ""
            if name:
                t.family = name                       # family from now on: no more reports about this track
                continue
        else:
            continue
        if t.hits < CONFIRM_HITS:
            continue
        if not t.alerted_at:                                          # first time: a stranger
            report(t, cam, frame, p.get("crop", b""), False, now)
        elif moved:                                                   # the same stranger, another camera
            report(t, cam, frame, p.get("crop", b""), True, now)
        elif now - t.alerted_at.get(cam.id, 0) > REALERT_SAME_CAMERA:  # still hanging around
            report(t, cam, frame, p.get("crop", b""), False, now)
    return touched


def _watch(cam: cameras.Cam) -> None:
    background, last_look, fails = None, 0.0, 0
    while not _stop.is_set():
        try:
            frame = cameras.snapshot(cam, timeout=12)
            cur = watcher.tiny(frame)
            fails = 0
            now = time.time()
            busy = background is None or watcher.changed_fraction(background, cur) >= watcher.CHANGE_THRESHOLD
            if busy or now - last_look >= RECHECK_SECONDS:
                last_look = now
                consider(cam, frame, now)
            background = cur if background is None else watcher.blend(background, cur, 0.04 if busy else 0.15)
            with _state_lock:
                _status["errors"].pop(cam.name, None)
        except Exception as e:  # noqa: BLE001
            fails += 1
            with _state_lock:
                _status["errors"][cam.name] = cameras.redact(str(e))[:160]
        _stop.wait(POLL_SECONDS if fails == 0 else min(60, POLL_SECONDS * (fails + 1)))


# ── on by itself when everyone leaves ────────────────────────────────────────
def everyone_away(states: list[dict] | None = None) -> bool | None:
    """True when every Home Assistant person is away, False when anyone's home, None if unknown."""
    try:
        from . import tracking
        ppl = tracking.people(states)
    except Exception:  # noqa: BLE001
        return None
    known = [p for p in ppl if p.get("state") not in ("unknown", "unavailable", None)]
    if not known:
        return None
    return all(p.get("state") != "home" for p in known)


def auto_tick(states: list[dict] | None = None) -> str:
    """One check of the 'everyone left' rule. Returns what it did ('' for nothing)."""
    if not settings()["auto_away"]:
        return ""
    away = everyone_away(states)
    if away is None:
        return ""
    if away and not _status["on"]:
        start("Everyone left home", auto=True)
        _ha_notify("Ghost guard", "Everyone's left home, so Guard mode is on.")
        return "on"
    if not away and _status["on"] and _status.get("auto"):
        stop("Someone came home")
        return "off"
    return ""


def _auto_loop() -> None:
    while True:
        try:
            auto_tick()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(AUTO_CHECK_SECONDS)


def boot() -> None:
    restore()
    threading.Thread(target=_auto_loop, daemon=True, name="guard-auto").start()
