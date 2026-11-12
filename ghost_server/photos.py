"""Photos for the tablet's photo-frame screen, from a folder on the NAS.

Map a photo share into the container (docker-compose: `/volume1/Photos:/photos:ro`) and
set the folder on the admin page. Photos are resized on the server (and cached), so
the tablet only downloads what fits its screen.
"""
from __future__ import annotations

import hashlib
import random
import threading
import time
from pathlib import Path

from . import config, store

EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_scan: dict = {"at": 0.0, "files": [], "folder": ""}
_lock = threading.Lock()


def folder() -> Path:
    return Path(str(store.get("photo_folder") or "/photos"))


def files() -> list[Path]:
    root = folder()
    with _lock:
        if _scan["folder"] == str(root) and time.time() - _scan["at"] < 600:
            return _scan["files"]
        found = []
        if root.is_dir():
            for p in root.rglob("*"):
                if p.suffix.lower() in EXTS and not any(part.startswith((".", "@", "#")) for part in p.relative_to(root).parts):
                    found.append(p)
                    if len(found) >= 5000:
                        break
        found.sort()
        _scan.update(at=time.time(), files=found, folder=str(root))
        return found


def listing(shuffle: bool = True, limit: int = 300) -> dict:
    fs = files()
    ids = list(range(len(fs)))
    if shuffle:
        random.shuffle(ids)
    return {"folder": str(folder()), "found": folder().is_dir(), "count": len(fs), "photos": ids[:limit]}


def image(index: int, width: int = 1600) -> bytes | None:
    fs = files()
    if not 0 <= index < len(fs):
        return None
    src = fs[index]
    width = max(320, min(int(width or 1600), 2560))
    key = hashlib.sha1(f"{src}|{src.stat().st_mtime}|{width}".encode()).hexdigest()
    cache = config.DATA_DIR / "cache" / "photos"
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"{key}.jpg"
    if not out.exists():
        from PIL import Image, ImageOps
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((width, width))
            im.save(out, "JPEG", quality=85)
    return out.read_bytes()

