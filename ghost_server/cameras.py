"""Security cameras — live view on any Ghost device, and "is anyone at the door?".

Two kinds of camera, shown together:
  • Home Assistant cameras (camera.* entities) — streamed through Home Assistant,
    so camera passwords stay in HA.
  • Other cameras / NVR channels over RTSP (Reolink, Hikvision, Dahua, UniFi,
    Tapo, Eufy…) — listed on the admin page as  Name = rtsp://user:pass@ip:554/…
    and turned into a browser-friendly MJPEG stream by ffmpeg on the server.

Devices never get camera passwords or the HA token. To show a feed, the server
issues a short-lived view link (/cam/<token>) and tells the device to open it.
"""
from __future__ import annotations

import asyncio
import base64
import html
import re
import secrets
import threading
import time
from dataclasses import dataclass

from . import store

VIEW_TTL = 30 * 60          # a view link works for 30 minutes
FFMPEG_FPS = 6


@dataclass
class Cam:
    id: str          # "camera.front_door" (Home Assistant) or "rtsp:front-gate"
    name: str
    source: str      # "ha" | "rtsp"
    url: str = ""    # rtsp only


# ── the camera list ──────────────────────────────────────────────────────────
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "camera"


def rtsp_cameras() -> list[Cam]:
    out = []
    for line in str(store.get("rtsp_cameras") or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, url = (p.strip() for p in line.split("=", 1))
        if url.lower().startswith(("rtsp://", "rtsps://", "http://", "https://")) and name:
            out.append(Cam("rtsp:" + _slug(name), name, "rtsp", url))
    return out


def ha_cameras() -> list[Cam]:
    if not store.get("ha_token"):
        return []
    from .tools.home import _req
    try:
        states = _req("GET", "/api/states").json()
    except Exception:  # noqa: BLE001
        return []
    return [Cam(s["entity_id"], s.get("attributes", {}).get("friendly_name") or s["entity_id"], "ha")
            for s in states if s["entity_id"].startswith("camera.") and s.get("state") != "unavailable"]


def all_cameras() -> list[Cam]:
    return ha_cameras() + rtsp_cameras()


def find(name: str, cams: list[Cam] | None = None) -> list[Cam]:
    """'front door', 'driveway', 'all' → matching cameras (all of them for 'all' / empty)."""
    cams = cams if cams is not None else all_cameras()
    n = re.sub(r"[^a-z0-9 ]", " ", str(name or "").lower()).strip()
    if not n or n in ("all", "all cameras", "cameras", "everything", "security", "the cameras"):
        return cams
    words = [w for w in n.split() if w not in ("the", "camera", "cam", "feed", "my")]
    def score(c: Cam) -> int:
        hay = (c.name + " " + c.id).lower().replace("_", " ").replace("-", " ")
        return sum(1 for w in words if w in hay)
    best = max((score(c) for c in cams), default=0)
    return [c for c in cams if best and score(c) == best]


# ── view links ───────────────────────────────────────────────────────────────
_views: dict[str, tuple[float, list[Cam]]] = {}
_vlock = threading.Lock()


def issue(cams: list[Cam]) -> str:
    tok = secrets.token_urlsafe(18)
    now = time.time()
    with _vlock:
        for k in [k for k, (exp, _) in _views.items() if exp < now]:
            _views.pop(k, None)
        _views[tok] = (now + VIEW_TTL, cams)
    return tok


def view(tok: str) -> list[Cam] | None:
    with _vlock:
        v = _views.get(tok)
    if not v or v[0] < time.time():
        return None
    return v[1]


# ── streams ──────────────────────────────────────────────────────────────────
async def mjpeg(cam: Cam):
    """(content_type, async byte iterator) for a live MJPEG stream of this camera."""
    if cam.source == "ha":
        import httpx
        url = str(store.get("ha_url") or "").rstrip("/") + f"/api/camera_proxy_stream/{cam.id}"
        client = httpx.AsyncClient(timeout=httpx.Timeout(10, read=30), trust_env=False)
        req = client.build_request("GET", url, headers={"Authorization": f"Bearer {store.get('ha_token')}"})
        resp = await client.send(req, stream=True)
        if resp.status_code >= 300:
            await resp.aclose(); await client.aclose()
            raise RuntimeError(f"Home Assistant said HTTP {resp.status_code}")

        async def gen():
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()
        return resp.headers.get("content-type", "multipart/x-mixed-replace;boundary=frame"), gen()

    args = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if cam.url.lower().startswith("rtsp"):
        args += ["-rtsp_transport", "tcp"]
    args += ["-i", cam.url, "-an", "-r", str(FFMPEG_FPS), "-vf", "scale='min(1280,iw)':-2",
             "-q:v", "6", "-f", "mpjpeg", "-boundary_tag", "ghostframe", "pipe:1"]
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL)

    async def gen():
        try:
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
    return "multipart/x-mixed-replace;boundary=ghostframe", gen()


def snapshot(cam: Cam, timeout: float = 15) -> bytes:
    """One JPEG from the camera, now."""
    if cam.source == "ha":
        from .tools.home import _req
        r = _req("GET", f"/api/camera_proxy/{cam.id}")
        if r.status_code >= 300:
            raise RuntimeError(f"Home Assistant said HTTP {r.status_code}")
        return r.content
    import subprocess
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if cam.url.lower().startswith("rtsp"):
        args += ["-rtsp_transport", "tcp"]
    args += ["-i", cam.url, "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", "-q:v", "4", "-f", "image2", "pipe:1"]
    out = subprocess.run(args, capture_output=True, timeout=timeout).stdout
    if not out:
        raise RuntimeError("the camera didn't send a picture")
    return out


def describe(cam: Cam, question: str) -> str:
    """Claude looks at a snapshot and answers the question about it."""
    from .tools.brain import claude
    img = base64.b64encode(snapshot(cam)).decode()
    q = question.strip() or "What's happening? Mention any people, vehicles, animals or deliveries."
    text, _ = claude(fast=False).complete([{"role": "user", "content": [
        {"type": "text", "text": f"This is a live snapshot from the home security camera \"{cam.name}\" "
                                 f"taken {time.strftime('%I:%M %p')}. {q} Answer in one or two short sentences "
                                 "for a voice assistant to say. If it's too dark or unclear to tell, say so."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}]}], max_tokens=300)
    return text.strip() or "I couldn't make anything out."


# ── the viewer page ──────────────────────────────────────────────────────────
def page(tok: str, cams: list[Cam]) -> str:
    n = len(cams)
    cols = 1 if n == 1 else 2 if n <= 4 else 3
    tiles = "\n".join(
        f'<figure><img src="/cam/{tok}/stream/{i}" data-src="/cam/{tok}/stream/{i}" alt="">'
        f'<figcaption>{html.escape(c.name)}</figcaption></figure>' for i, c in enumerate(cams))
    title = html.escape(cams[0].name if n == 1 else "Cameras")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>
 html,body{{margin:0;height:100%;background:#000;color:#E6E0FF;font-family:system-ui,sans-serif}}
 main{{display:grid;grid-template-columns:repeat({cols},1fr);gap:6px;height:100vh;padding:6px;box-sizing:border-box}}
 figure{{margin:0;position:relative;background:#07060D;border-radius:10px;overflow:hidden;display:flex}}
 img{{width:100%;height:100%;object-fit:contain}}
 figcaption{{position:absolute;left:10px;top:8px;background:#0009;padding:3px 10px;border-radius:12px;font-size:14px}}
 figcaption::before{{content:"● ";color:#ff4d6d}}
 #clock{{position:fixed;right:14px;top:10px;background:#0009;padding:3px 10px;border-radius:12px;font-size:14px}}
</style></head><body><main>{tiles}</main><div id="clock"></div>
<script>
 // reconnect a feed that drops; tap a tile to see just that camera
 document.querySelectorAll('img').forEach(function(im){{
   im.onerror=function(){{setTimeout(function(){{im.src=im.dataset.src+'?r='+Date.now()}},3000)}};
   im.parentElement.onclick=function(){{var all=document.querySelectorAll('figure');
     var solo=document.body.dataset.solo===im.dataset.src;
     all.forEach(function(f){{f.style.display=(solo||f===im.parentElement)?'flex':'none'}});
     document.querySelector('main').style.gridTemplateColumns=solo?'repeat({cols},1fr)':'1fr';
     document.body.dataset.solo=solo?'':im.dataset.src;}};
 }});
 setInterval(function(){{document.getElementById('clock').textContent=new Date().toLocaleTimeString()}},1000);
</script></body></html>"""
