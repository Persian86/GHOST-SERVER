"""Private NAS camera captures. Access is always scoped by a live camera-view token."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import shutil
import struct
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse

from . import cameras, config, store

def private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


router = APIRouter(prefix="/cam/{tok}", dependencies=[Depends(private_response)])
LIMIT = 1024 * 1024 * 1024
CLIP_LIMIT = 100 * 1024 * 1024
MAX_SECONDS = 300
_lock = asyncio.Lock()
_jobs: dict[str, dict] = {}


def root() -> Path:
    p = config.DATA_DIR / "camera-media"
    p.mkdir(parents=True, exist_ok=True)
    return p


def key(cam) -> str:
    return hashlib.sha256((cam.source + ":" + cam.id).encode()).hexdigest()


def scope(tok: str):
    cams = cameras.view(tok)
    if cams is None:
        raise HTTPException(410, "Camera link expired. Reopen Cameras.")
    return cams


def selected(tok: str, i: int):
    cams = scope(tok)
    if not 0 <= i < len(cams):
        raise HTTPException(404, "Camera not found")
    return cams[i]


def mutation(request: Request):
    # Custom header prevents cross-site form submissions using a leaked view URL.
    if request.headers.get("x-ghost-camera") != "1":
        raise HTTPException(403, "Use the camera viewer controls")
    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlsplit
        if urlsplit(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "Camera controls require the same server")


def capacity(extra: int):
    p = root()
    files = list(p.iterdir())
    reserved = len(_jobs) * CLIP_LIMIT
    if (sum(f.stat().st_size for f in files if f.is_file()) + reserved + extra > LIMIT
            or len(files) >= 2000 or shutil.disk_usage(p).free < extra + 64 * 1024 * 1024):
        raise HTTPException(507, "Camera storage is full. Remove saved items to make space.")


def write_meta(item):
    path = root() / (item["id"] + ".json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(item), encoding="utf-8")
    tmp.replace(path)


def read_item(tok: str, ident: str):
    cams = scope(tok)
    if not re.fullmatch(r"[0-9a-f]{32}", ident):
        raise HTTPException(404, "Saved item not found")
    try:
        item = json.loads((root() / (ident + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(404, "Saved item not found")
    if item.get("camera") not in {key(c) for c in cams}:
        raise HTTPException(404, "Saved item not found")
    return item


def path_for(item):
    return root() / (item["id"] + (".jpg" if item["kind"] == "photo" else ".mp4"))


def new_item(cam, kind):
    return {"id": secrets.token_hex(16), "camera": key(cam), "name": cam.name,
            "kind": kind, "created": time.time()}


def has_frames(path):
    """FFmpeg can exit successfully before receiving a first frame."""
    if not path.is_file():
        return False
    size = path.stat().st_size
    with path.open("rb") as f:
        while f.tell() + 8 <= size:
            start = f.tell()
            length, kind = struct.unpack(">I4s", f.read(8))
            header = 8
            if length == 1:
                if f.tell() + 8 > size:
                    return False
                length = struct.unpack(">Q", f.read(8))[0]
                header = 16
            elif length == 0:
                length = size - start
            if length < header or start + length > size:
                return False
            if kind == b"mdat" and length > header:
                return True
            f.seek(start + length)
    return False


@router.post("/capture/{i}")
async def capture(tok: str, i: int, request: Request):
    mutation(request)
    cam = selected(tok, i)
    # Serialize captures and launches so simultaneous requests cannot bypass quota.
    async with _lock:
        capacity(5 * 1024 * 1024)
        try:
            jpg = await asyncio.to_thread(cameras.snapshot, cam)
        except Exception:
            raise HTTPException(502, "Camera did not send a photo")
        if not jpg.startswith(b"\xff\xd8") or len(jpg) > 5 * 1024 * 1024:
            raise HTTPException(502, "Camera returned an invalid or oversized photo")
        scope(tok)
        item = new_item(cam, "photo")
        path_for(item).write_bytes(jpg)
        write_meta(item)
    return item


def encoder_args(cam, dest, seconds):
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if cam.source == "ha":
        url = str(store.get("ha_url") or "").rstrip("/") + "/api/camera_proxy_stream/" + cam.id
        args += ["-headers", "Authorization: Bearer " + str(store.get("ha_token") or "") + "\r\n"]
    else:
        url = cam.url
        if url.lower().startswith("rtsp"):
            args += ["-rtsp_transport", "tcp"]
    args += ["-rw_timeout", "15000000", "-i", url, "-an", "-t", str(seconds),
             "-vf", "scale='trunc(min(1280,iw)/2)*2':-2,fps=10", "-c:v", "libx264", "-preset", "veryfast",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-fs", str(CLIP_LIMIT), str(dest)]
    return args


async def finish(job, seconds):
    proc = job["proc"]
    partial = job["partial"]
    waiter = asyncio.create_task(proc.wait())
    stopper = asyncio.create_task(job["stop"].wait())
    try:
        await asyncio.wait({waiter, stopper}, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
        if proc.returncode is None:
            try:
                proc.stdin.write(b"q\n")
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            try:
                await asyncio.wait_for(asyncio.shield(waiter), 10)
            except asyncio.TimeoutError:
                proc.kill()
                await waiter
        if proc.returncode == 0 and has_frames(partial):
            partial.replace(path_for(job["item"]))
            job["state"] = "saved"
            write_meta(job["item"])
        else:
            job["state"] = "failed"
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        job["state"] = "failed"
        raise
    except Exception:
        job["state"] = "failed"
    finally:
        stopper.cancel()
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        await asyncio.gather(waiter, stopper, return_exceptions=True)
        if proc.stdin:
            proc.stdin.close()
            try:
                await proc.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
        partial.unlink(missing_ok=True)
        _jobs.pop(job["item"]["id"], None)


@router.post("/recordings/{i}")
async def record(tok: str, i: int, request: Request):
    mutation(request)
    cam = selected(tok, i)
    async with _lock:
        for job in _jobs.values():
            if job["tok"] == tok:
                return {**job["item"], "state": "recording"}
        if len(_jobs) >= 2:
            raise HTTPException(409, "Two cameras are already recording. Stop one first.")
        capacity(CLIP_LIMIT)
        item = new_item(cam, "video")
        partial = root() / (item["id"] + ".part.mp4")
        seconds = min(MAX_SECONDS, max(0, cameras.remaining(tok)))
        if seconds < 2:
            raise HTTPException(410, "Camera link expired. Reopen Cameras.")
        try:
            proc = await asyncio.create_subprocess_exec(*encoder_args(cam, partial, seconds),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            raise HTTPException(503, "Recording needs FFmpeg installed on Ghost Server")
        job = {"tok": tok, "item": item, "proc": proc, "partial": partial,
               "stop": asyncio.Event(), "state": "recording"}
        _jobs[item["id"]] = job
        job["task"] = asyncio.create_task(finish(job, seconds))
    return {**item, "state": "recording"}


@router.get("/recording/{ident}")
async def status(tok: str, ident: str):
    scope(tok)
    job = _jobs.get(ident)
    if job and job["tok"] == tok:
        return {"state": "recording", **job["item"]}
    try:
        item = read_item(tok, ident)
        return {"state": "saved", **item}
    except HTTPException as e:
        if e.status_code != 404:
            raise
        return {"state": "failed"}


@router.post("/recording/{ident}/stop")
async def stop(tok: str, ident: str, request: Request):
    mutation(request)
    scope(tok)
    job = _jobs.get(ident)
    if job and job["tok"] == tok:
        job["stop"].set()
        await asyncio.shield(job["task"])
    return await status(tok, ident)


@router.get("/media")
async def gallery(tok: str):
    allowed = {key(c) for c in scope(tok)}
    items = []
    for p in root().glob("*.json"):
        try:
            item = json.loads(p.read_text(encoding="utf-8"))
            if item["camera"] in allowed and path_for(item).is_file():
                items.append(item)
        except (OSError, ValueError, KeyError):
            continue
    return sorted(items, key=lambda x: x["created"], reverse=True)


@router.get("/media/{ident}")
async def media(tok: str, ident: str):
    item = read_item(tok, ident)
    path = path_for(item)
    if not path.is_file():
        raise HTTPException(404, "Saved file not found")
    return FileResponse(path, media_type="image/jpeg" if item["kind"] == "photo" else "video/mp4",
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.delete("/media/{ident}")
async def delete(tok: str, ident: str, request: Request):
    mutation(request)
    async with _lock:
        item = read_item(tok, ident)
        path_for(item).unlink(missing_ok=True)
        (root() / (ident + ".json")).unlink(missing_ok=True)
    return {"deleted": True}


async def shutdown():
    """Flush MP4 headers before the server exits."""
    jobs = list(_jobs.values())
    for job in jobs:
        job["stop"].set()
    await asyncio.gather(*(job["task"] for job in jobs), return_exceptions=True)


def recover():
    # A crashed encoder cannot finalize its MP4. Never show partial clips as saved.
    for pattern in ("*.part.mp4", "*.tmp"):
        for path in root().glob(pattern):
            path.unlink(missing_ok=True)
