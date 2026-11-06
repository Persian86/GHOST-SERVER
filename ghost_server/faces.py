"""
Visual ID: who is in front of a Ghost device.

A device (the kitchen tablet) notices a face itself, cheaply, and sends one small
photo here. The server finds the faces (OpenCV YuNet), turns each into a face print
(OpenCV SFace, 128 numbers), and compares it with the prints of the people the
household has set up. Everything stays on the NAS: photos are never kept, and
prints are only stored for people someone deliberately added. A face that matches
nobody is "a guest" and nothing about it is saved.

    POST /api/v1/faces/identify   {image}                       → who's there + a greeting
    POST /api/v1/faces/enroll     {name, image, admin_password}  → add a sample for a person
    admin page: list people, rename, set their calendar, delete (removes every print)
"""
from __future__ import annotations

import base64
import hashlib
import os
import threading
import time
from pathlib import Path

from . import config, store

MATCH = 0.40            # SFace cosine similarity; OpenCV's reference threshold is 0.363
MAX_PRINTS = 20         # samples kept per person (newest win)
GREET_EVERY = 3 * 3600  # don't re-greet the same person on the same device for 3 hours
PRESENCE_FOR = 300      # "in front of the tablet" for 5 minutes after being seen

MODELS = {
    "detector": ("face_detection_yunet_2023mar.onnx",
                 "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "recognizer": ("face_recognition_sface_2021dec.onnx",
                   "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}
ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models/"
ZOO_DIRS = {"detector": "face_detection_yunet/", "recognizer": "face_recognition_sface/"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE COLLATE NOCASE,
    calendar TEXT DEFAULT '', created_at REAL);
CREATE TABLE IF NOT EXISTS face_prints (
    id INTEGER PRIMARY KEY AUTOINCREMENT, person_id INTEGER, emb BLOB, created_at REAL);
"""


# ─────────────────────────────────────────────────────────────────────────────
#  The face engine (OpenCV). Tests swap `engine` for a stand-in.
# ─────────────────────────────────────────────────────────────────────────────
def _model_path(kind: str) -> Path:
    name, sha = MODELS[kind]
    for d in (Path(os.environ.get("GHOST_MODELS", "/app/models")), config.DATA_DIR / "models"):
        p = d / name
        if p.exists() and p.stat().st_size > 1000:
            return p
    # Not baked into the image: fetch it once into the data folder and check it
    import httpx
    d = config.DATA_DIR / "models"
    d.mkdir(parents=True, exist_ok=True)
    data = httpx.get(ZOO + ZOO_DIRS[kind] + name, follow_redirects=True, timeout=120).content
    if hashlib.sha256(data).hexdigest() != sha:
        raise RuntimeError(f"the downloaded face model {name} failed its checksum")
    p = d / name
    p.write_bytes(data)
    return p


class Engine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._det = self._rec = None

    def _load(self):
        if self._rec is None:
            import cv2
            self._det = cv2.FaceDetectorYN.create(str(_model_path("detector")), "", (320, 320), 0.8, 0.3, 50)
            self._rec = cv2.FaceRecognizerSF.create(str(_model_path("recognizer")), "")

    def faces(self, jpeg: bytes) -> list[tuple[list[int], list[float]]]:
        """[(box [x, y, w, h], unit-length face print)] for every face in the photo, biggest first."""
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("that isn't a photo I can read")
        scale = min(1.0, 960 / max(img.shape[:2]))
        if scale < 1:
            img = cv2.resize(img, None, fx=scale, fy=scale)
        out = []
        with self._lock:
            self._load()
            self._det.setInputSize((img.shape[1], img.shape[0]))
            _, found = self._det.detect(img)
            for f in (found if found is not None else []):
                feat = self._rec.feature(self._rec.alignCrop(img, f)).flatten().astype("float32")
                feat /= (float(np.linalg.norm(feat)) or 1.0)
                box = [int(v / scale) for v in f[:4]]
                out.append((box, feat.tolist()))
        out.sort(key=lambda b: -(b[0][2] * b[0][3]))
        return out


engine = Engine()


# ─────────────────────────────────────────────────────────────────────────────
#  People and their prints
# ─────────────────────────────────────────────────────────────────────────────
_ready = False
_cache: dict | None = None          # {"ids": [person ids], "names": {...}, "mat": ndarray}
_presence: dict[str, dict] = {}      # device id → {"names", "guests", "at"}
_greeted: dict[tuple[str, str], float] = {}


def _schema() -> None:
    global _ready
    if not _ready:
        store._con().executescript(SCHEMA)
        _ready = True


def _invalidate() -> None:
    global _cache
    _cache = None


def _prints():
    global _cache
    import numpy as np
    _schema()
    if _cache is None:
        rows = store.q("SELECT f.person_id, f.emb, p.name FROM face_prints f JOIN people p ON p.id = f.person_id")
        _cache = {"ids": [r["person_id"] for r in rows], "names": {r["person_id"]: r["name"] for r in rows},
                  "mat": np.array([np.frombuffer(r["emb"], dtype="float32") for r in rows]) if rows else None}
    return _cache


def people() -> list[dict]:
    _schema()
    rows = store.q("SELECT p.id, p.name, p.calendar, p.created_at, COUNT(f.id) AS samples FROM people p "
                   "LEFT JOIN face_prints f ON f.person_id = p.id GROUP BY p.id ORDER BY p.name")
    return [dict(r) for r in rows]


def person(name: str) -> dict | None:
    _schema()
    rows = store.q("SELECT * FROM people WHERE name = ?", (name.strip(),))
    return dict(rows[0]) if rows else None


def edit_person(pid: int, name: str | None = None, calendar: str | None = None) -> None:
    _schema()
    if name and name.strip():
        store.x("UPDATE people SET name = ? WHERE id = ?", (name.strip()[:40], pid))
    if calendar is not None:
        store.x("UPDATE people SET calendar = ? WHERE id = ?", (calendar.strip()[:500], pid))
    _invalidate()


def delete_person(pid: int) -> None:
    _schema()
    store.x("DELETE FROM face_prints WHERE person_id = ?", (pid,))
    store.x("DELETE FROM people WHERE id = ?", (pid,))
    _invalidate()


def _decode(image: str | bytes) -> bytes:
    if isinstance(image, bytes):
        return image
    s = str(image or "")
    if s.startswith("data:"):
        s = s.split(",", 1)[-1]
    try:
        return base64.b64decode(s, validate=False)
    except Exception as e:
        raise ValueError("the photo wasn't valid base64") from e


def enroll(name: str, image: str | bytes) -> dict:
    name = " ".join(str(name or "").split())[:40]
    if not name:
        return {"ok": False, "error": "Give the person a name first"}
    found = engine.faces(_decode(image))
    if not found:
        return {"ok": False, "error": "I couldn't see a face. Look straight at the camera in good light."}
    _schema()
    p = person(name)
    if p is None:
        store.x("INSERT INTO people (name, created_at) VALUES (?, ?)", (name, time.time()))
        p = person(name)
    import numpy as np
    store.x("INSERT INTO face_prints (person_id, emb, created_at) VALUES (?, ?, ?)",
            (p["id"], np.array(found[0][1], dtype="float32").tobytes(), time.time()))
    extra = store.q("SELECT id FROM face_prints WHERE person_id = ? ORDER BY id DESC LIMIT -1 OFFSET ?",
                    (p["id"], MAX_PRINTS))
    for r in extra:
        store.x("DELETE FROM face_prints WHERE id = ?", (r["id"],))
    _invalidate()
    n = store.q("SELECT COUNT(*) AS n FROM face_prints WHERE person_id = ?", (p["id"],))[0]["n"]
    return {"ok": True, "person": p["name"], "samples": n, "faces_in_photo": len(found)}


def match(feat) -> tuple[str, float] | None:
    import numpy as np
    c = _prints()
    if c["mat"] is None:
        return None
    sims = c["mat"] @ np.asarray(feat, dtype="float32")
    best: dict[int, float] = {}
    for pid, s in zip(c["ids"], sims.tolist()):
        best[pid] = max(best.get(pid, -1.0), s)
    pid, score = max(best.items(), key=lambda kv: kv[1])
    return (c["names"][pid], round(score, 3)) if score >= MATCH else None


def identify(image: str | bytes, device: dict) -> dict:
    found = engine.faces(_decode(image))
    seen, guests = [], 0
    for box, feat in found[:6]:
        m = match(feat)
        if m and m[0] not in [s["name"] for s in seen]:
            seen.append({"name": m[0], "score": m[1], "box": box})
        elif not m:
            guests += 1
    now = time.time()
    if found:
        _presence[device["id"]] = {"names": [s["name"] for s in seen], "guests": guests, "at": now,
                                   "device": device.get("name", "")}
    out = {"people": seen, "guests": guests, "faces": len(found)}
    if seen:
        who = seen[0]["name"]
        key = (device["id"], who.lower())
        if now - _greeted.get(key, 0) > GREET_EVERY:
            _greeted[key] = now
            out["greet"] = f"{_part_of_day()}, {who.split()[0]}"
        out["for"] = who
    return out


def _part_of_day() -> str:
    from .quiet import _local
    h = _local().hour
    return "Good morning" if 4 <= h < 12 else "Good afternoon" if h < 18 else "Good evening"


def here(device_id: str, within: float = PRESENCE_FOR) -> dict | None:
    p = _presence.get(device_id)
    return p if p and time.time() - p["at"] < within else None


def presence_text(device_id: str) -> str:
    """For the system prompt: who the camera on this device sees right now."""
    p = here(device_id)
    if not p:
        return ""
    if p["names"]:
        names = " and ".join(p["names"])
        extra = f", plus {p['guests']} person you don't recognise" if p["guests"] else ""
        return (f"[WHO IS HERE]\nThis device's camera recognises {names}{extra}. Address them by name "
                f"naturally (not in every sentence). Recognition can be wrong; if they say otherwise, believe them.")
    return ("[WHO IS HERE]\nSomeone you don't recognise is in front of this device. Treat them as a guest "
            "and don't share anything personal about the household.")


def last_seen() -> list[dict]:
    out = []
    for did, p in _presence.items():
        for n in p["names"]:
            out.append({"name": n, "device": p["device"], "at": p["at"]})
    best: dict[str, dict] = {}
    for e in out:
        if e["name"] not in best or e["at"] > best[e["name"]]["at"]:
            best[e["name"]] = e
    return sorted(best.values(), key=lambda e: -e["at"])
