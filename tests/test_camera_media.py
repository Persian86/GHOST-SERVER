"""Use isolated storage and fake cameras; never contact household cameras."""
import asyncio
import io
import json
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from ghost_server import cameras, camera_media as media


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(media.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(media, "_jobs", {})
    monkeypatch.setattr(media, "_lock", asyncio.Lock())
    cams = [cameras.Cam("front", "Front <door>", "rtsp", "rtsp://secret:password@camera")]
    token = cameras.issue(cams)
    app = FastAPI()
    app.include_router(media.router)
    jpg = io.BytesIO()
    Image.new("RGB", (64, 64), "blue").save(jpg, "JPEG")
    monkeypatch.setattr(cameras, "snapshot", lambda _: jpg.getvalue())
    return app, token, cams, jpg.getvalue()


async def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


HEAD = {"X-Ghost-Camera": "1"}


def test_capture_scope_gallery_range_and_delete(setup):
    app, tok, cams, jpg = setup
    async def run():
        async with await client(app) as c:
            base = "/cam/" + tok
            assert (await c.post(base + "/capture/0")).status_code == 403
            assert (await c.post(base + "/capture/0", headers={**HEAD, "Origin": "https://evil.test"})).status_code == 403
            assert (await c.post(base + "/capture/1", headers=HEAD)).status_code == 404
            r = await c.post(base + "/capture/0", headers=HEAD)
            assert r.status_code == 200
            ident = r.json()["id"]
            assert (await c.get(base + "/media")).json()[0]["id"] == ident
            image = await c.get(base + "/media/" + ident)
            assert image.content == jpg and image.headers["cache-control"] == "no-store"
            partial = await c.get(base + "/media/" + ident, headers={"Range": "bytes=0-15"})
            assert partial.status_code == 206 and partial.content == jpg[:16]
            other = cameras.issue([cameras.Cam("other", "Other", "rtsp")])
            assert (await c.get("/cam/" + other + "/media")).json() == []
            assert (await c.get("/cam/" + other + "/media/" + ident)).status_code == 404
            assert (await c.delete("/cam/" + other + "/media/" + ident, headers=HEAD)).status_code == 404
            assert (await c.get(base + "/media/not-a-file")).status_code == 404
            assert "password" not in json.dumps((await c.get(base + "/media")).json())
            assert (await c.delete(base + "/media/" + ident, headers=HEAD)).status_code == 200
            assert (await c.get(base + "/media")).json() == []
            assert not list(media.root().iterdir())
            cameras._views[tok] = (time.time() - 1, cams)
            assert (await c.get(base + "/media")).status_code == 410
    asyncio.run(run())


def test_capture_failure_and_quota(setup, monkeypatch):
    app, tok, _, _ = setup
    async def run():
        async with await client(app) as c:
            url = "/cam/" + tok + "/capture/0"
            monkeypatch.setattr(cameras, "snapshot", lambda _: b"not jpeg")
            assert (await c.post(url, headers=HEAD)).status_code == 502
            monkeypatch.setattr(media, "LIMIT", 1)
            assert (await c.post(url, headers=HEAD)).status_code == 507
            assert not list(media.root().iterdir())
    asyncio.run(run())


class FakeProc:
    def __init__(self, dest, fail=False):
        self.returncode = None
        self.dest = Path(dest)
        self.done = asyncio.Event()
        self.stdin = self
        self.fail = fail
    def write(self, data):
        assert data == b"q\n"
        self.dest.write_bytes(b"\x00\x00\x00\x0cmdatdata")
        self.returncode = 1 if self.fail else 0
        self.done.set()
    async def drain(self):
        pass
    def close(self):
        pass
    async def wait_closed(self):
        pass
    async def wait(self):
        await self.done.wait()
        return self.returncode
    def kill(self):
        self.returncode = -9
        self.done.set()


@pytest.mark.parametrize("fail", [False, True])
def test_record_stop_is_idempotent_and_cleans_partial(setup, monkeypatch, fail):
    app, tok, cams, _ = setup
    procs = []
    async def spawn(*args, **kwargs):
        procs.append(FakeProc(args[-1], fail))
        return procs[-1]
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    async def run():
        async with await client(app) as c:
            base = "/cam/" + tok
            r = await c.post(base + "/recordings/0", headers=HEAD)
            assert r.status_code == 200
            ident = r.json()["id"]
            assert (await c.post(base + "/recordings/0", headers=HEAD)).json()["id"] == ident
            assert len(procs) == 1
            other = cameras.issue(cams)
            assert (await c.post("/cam/" + other + "/recording/" + ident + "/stop", headers=HEAD)).json()["state"] == "failed"
            assert procs[0].returncode is None
            r = await c.post(base + "/recording/" + ident + "/stop", headers=HEAD)
            assert r.json()["state"] == ("failed" if fail else "saved")
            assert not media._jobs and not list(media.root().glob("*.part.mp4"))
            assert len((await c.get(base + "/media")).json()) == (0 if fail else 1)
            assert (await c.post(base + "/recording/" + ident + "/stop", headers=HEAD)).json() == r.json()
    asyncio.run(run())


def test_record_timeout_and_encoder_missing(setup, monkeypatch):
    app, tok, _, _ = setup
    async def missing(*a, **k):
        raise FileNotFoundError("ffmpeg")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing)
    async def run():
        async with await client(app) as c:
            assert (await c.post("/cam/" + tok + "/recordings/0", headers=HEAD)).status_code == 503
            assert not media._jobs
            proc = FakeProc(media.root() / "auto.part.mp4")
            item = media.new_item(cameras.Cam("test", "Test", "rtsp"), "video")
            job = {"proc": proc, "partial": proc.dest, "item": item, "stop": asyncio.Event(), "state": "recording"}
            media._jobs[item["id"]] = job
            await media.finish(job, .01)
            assert job["state"] == "saved" and not media._jobs
    asyncio.run(run())


def test_viewer_escapes_camera_names_and_record_bounds(setup, monkeypatch):
    _, tok, cams, _ = setup
    cams[0].name = '</script><script>alert("x")</script>'
    page = cameras.page(tok, cams)
    assert cams[0].name not in page and "\\u003c/script>" in page
    args = media.encoder_args(cams[0], "out.mp4", 30)
    assert args[args.index("-t") + 1] == "30"
    assert "-an" in args and args[args.index("-fs") + 1] == str(media.CLIP_LIMIT)
    assert cameras.remaining(tok) > 0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable on this machine")
def test_real_encoder_saves_playable_mp4(setup, monkeypatch, tmp_path):
    app, tok, cams, _ = setup
    source = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10",
                    "-t", "1", "-pix_fmt", "yuv420p", str(source)], check=True, timeout=15)
    cams[0].url = str(source)
    async def run():
        async with await client(app) as c:
            base = "/cam/" + tok
            r = await c.post(base + "/recordings/0", headers=HEAD)
            assert r.status_code == 200, r.text
            ident = r.json()["id"]
            task = media._jobs[ident]["task"]
            await asyncio.wait_for(asyncio.shield(task), 20)
            assert (await c.get(base + "/recording/" + ident)).json()["state"] == "saved"
            video = media.path_for(media.read_item(tok, ident))
            assert b"ftyp" in video.read_bytes()[:32]
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"], check=True, timeout=10)
    asyncio.run(run())
