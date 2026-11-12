"""End-to-end tests: a real server on a random port, real SDK agents talking to it."""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sdk" / "python"))


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import os
    data = tmp_path_factory.mktemp("data")
    os.environ["GHOST_DATA"] = str(data)
    import importlib
    from ghost_server import config
    importlib.reload(config)
    from ghost_server import store
    store.init(data / "ghost.db")
    config.FILES_DIR = data / "files"
    import uvicorn
    from ghost_server.app import app
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/v1/health").status_code == 200:
                break
        except Exception:
            time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True


@pytest.fixture(scope="module")
def admin(server):
    c = httpx.Client(base_url=server, headers={"X-Ghost-Admin": "1"})
    assert c.get("/admin/state").json()["needs_setup"] is True
    assert c.post("/admin/login", json={"password": "abc"}).status_code == 400      # too short
    assert c.post("/admin/login", json={"password": "ghost-pass"}).status_code == 200
    assert httpx.post(server + "/admin/login", json={"password": "wrong"}).status_code == 401
    from ghost_server import security
    security.reset_all()
    # most tests pair devices directly; approval has its own test
    assert c.put("/admin/api/settings", json={"approve_new_devices": False}).status_code == 200
    return c


def _agent(server, admin, name, kind, tmp_path):
    from ghost_agent import GhostAgent
    code = admin.post("/admin/api/pair-code").json()["code"]
    a = GhostAgent(server, name=name, kind=kind, state_file=tmp_path / f"{name}.json")
    a.pair(code)
    return a


def test_admin_requires_login(server):
    assert httpx.get(server + "/admin/api/overview").status_code == 401


def test_pairing_and_session(server, admin, tmp_path):
    from ghost_agent import GhostAgent, GhostError
    with pytest.raises(GhostError):
        GhostAgent(server, state_file=tmp_path / "x.json").pair("000000")
    a = _agent(server, admin, "Kitchen Tablet", "tablet", tmp_path)
    assert a.key.startswith("gk_")
    # the key survives a restart through the state file
    from ghost_agent import GhostAgent as G2
    assert G2(server, state_file=tmp_path / "Kitchen Tablet.json").key == a.key
    admin.put("/admin/api/settings", json={"owner_name": "Adam", "city": "Melbourne"})
    s = a.session()
    assert "Kitchen Tablet" in s["system_instruction"] and "Adam" in s["system_instruction"]
    names = {t["name"] for t in s["server_tools"]}
    assert {"remember", "home_assistant", "marquee", "think_deeply", "device_action", "announce",
            "create_document", "run_task", "briefing", "news", "weather"} <= names
    assert "error" in s["gemini"]                       # no Gemini key configured yet
    assert httpx.get(server + "/api/v1/session", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_shared_memory_across_devices(server, admin, tmp_path):
    a = _agent(server, admin, "Office PC", "desktop", tmp_path)
    b = _agent(server, admin, "Phone", "phone", tmp_path)
    assert "Saved" in a.call_tool("remember", {"fact": "Zoey's birthday is 12 May", "section": "People"})
    assert "12 May" in b.call_tool("recall", {"query": "zoey birthday"})
    assert "12 May" in b.session()["system_instruction"]
    b.call_tool("standing_rule", {"action": "add", "rule": "Always use Celsius"})
    assert "Always use Celsius" in a.session()["system_instruction"]
    a.log_turns([{"role": "user", "text": "remind me to call the plumber"}])
    assert "plumber" not in b.session()["system_instruction"]
    from ghost_server import store
    did=json.loads(a.state_file.read_text())["device_id"]
    assert any("plumber" in t["text"] for t in store.recent_turns(8,did))


def test_device_to_device_routing_and_announce(server, admin, tmp_path):
    desk = _agent(server, admin, "Study PC", "desktop", tmp_path)
    phone = _agent(server, admin, "Adam's Phone", "phone", tmp_path)
    said, rang = [], []

    @phone.capability("ring", "Ring the phone loudly")
    def ring(args):
        rang.append(args)
        return "ringing"

    phone.on_say = lambda t: said.append(t)

    async def scenario():
        stop = asyncio.Event()
        tasks = [asyncio.create_task(phone.run_link(stop)), asyncio.create_task(desk.run_link(stop))]
        for _ in range(100):
            if phone.connected and desk.connected:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)
        devs = await asyncio.to_thread(desk.call_tool, "list_devices", {})
        r1 = await asyncio.to_thread(desk.call_tool, "device_action", {"device": "phone", "action": "ring",
                                                                       "args": {"loud": True}})
        r2 = await asyncio.to_thread(desk.call_tool, "device_action", {"device": "phone", "action": "fly"})
        r3 = await asyncio.to_thread(desk.call_tool, "announce", {"text": "Dinner's ready", "device": "all"})
        await asyncio.sleep(0.3)
        stop.set()
        for t in tasks:
            t.cancel()
        return devs, r1, r2, r3

    devs, r1, r2, r3 = asyncio.run(scenario())
    assert "Adam's Phone" in devs and "online" in devs and "ring" in devs
    assert r1 == "Adam's Phone: ringing" and rang == [{"loud": True}]
    assert "can't 'fly'" in r2
    assert "Announced on" in r3 and said == ["Dinner's ready"]
    over = admin.get("/admin/api/overview").json()
    assert any(d["name"] == "Adam's Phone" for d in over["devices"])


def test_chat_uses_tools_with_claude(server, admin, tmp_path, monkeypatch):
    from ghost_server.claude import FunctionCall
    from ghost_server.tools import brain

    class FakeClaude:
        def __init__(self):
            self.n = 0

        def complete(self, history, tools=None, **kw):
            self.n += 1
            names = {t["name"] for t in tools or []}
            assert "remember" in names
            if self.n == 1:
                return "", [FunctionCall(id="t1", name="remember", args={"fact": "Prefers dark mode"})]
            return "Noted — dark mode it is.", []
    fake = FakeClaude()
    monkeypatch.setattr(brain, "claude", lambda *a, **k: fake)
    a = _agent(server, admin, "Watch", "wearable", tmp_path)
    r = a.chat("I like dark mode, remember that")
    assert r["text"] == "Noted — dark mode it is." and r["tools"] == ["remember"]
    assert any(f["text"] == "Prefers dark mode" for f in admin.get("/admin/api/overview").json()["facts"])


def test_documents_download(server, admin, tmp_path):
    a = _agent(server, admin, "Laptop", "desktop", tmp_path)
    out = a.call_tool("create_document", {"kind": "word", "title": "Shopping",
                                          "content": "# Shopping\n- milk\n- bread"})
    fid = out.split("/files/")[1].split()[0]
    r = httpx.get(a.file_url(fid))
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert httpx.get(f"{server}/files/{fid}").status_code == 401


def test_revoked_device_is_locked_out(server, admin, tmp_path):
    from ghost_agent import GhostError
    a = _agent(server, admin, "Old Tablet", "tablet", tmp_path)
    did = json.loads((tmp_path / "Old Tablet.json").read_text())["device_id"]
    admin.post(f"/admin/api/devices/{did}", json={"revoke": True})
    with pytest.raises(GhostError):
        a.session()


def test_settings_masking(server, admin):
    admin.put("/admin/api/settings", json={"anthropic_api_key": "sk-ant-secret-1234"})
    s = admin.get("/admin/api/overview").json()["settings"]
    assert s["anthropic_api_key"] == "•••1234"
    admin.put("/admin/api/settings", json={"anthropic_api_key": "•••1234"})     # untouched mask is ignored
    from ghost_server import store
    assert store.get("anthropic_api_key") == "sk-ant-secret-1234"


def test_calendar_and_ha_matching():
    import datetime as dt
    from ghost_server.tools.web import events_on
    from ghost_server.tools.home import match_entities
    day = dt.date(2026, 11, 3)
    ics = ("BEGIN:VEVENT\nDTSTART:20261103T090000\nSUMMARY:Dentist\nEND:VEVENT\n"
           "BEGIN:VEVENT\nDTSTART:20200107T070000\nRRULE:FREQ=WEEKLY;BYDAY=TU\nSUMMARY:Gym\nEND:VEVENT")
    assert events_on(ics, day) == [("07:00", "Gym"), ("09:00", "Dentist")]
    states = [{"entity_id": "light.lounge_lamp", "state": "on", "attributes": {"friendly_name": "Lounge Lamp"}},
              {"entity_id": "light.kitchen", "state": "off", "attributes": {"friendly_name": "Kitchen"}}]
    assert match_entities(states, "kitchen light")[0]["entity_id"] == "light.kitchen"


def test_llm_endpoint(server, admin, tmp_path, monkeypatch):
    from ghost_server.tools import brain
    seen = {}

    class Fake:
        def complete(self, messages, **kw):
            seen["messages"], seen["kw"] = messages, kw
            return "a cat on a keyboard", []
    monkeypatch.setattr(brain, "claude", lambda *a, **k: Fake())
    a = _agent(server, admin, "Vision PC", "desktop", tmp_path)
    r = httpx.post(server + "/api/v1/llm", headers={"Authorization": f"Bearer {a.key}"}, timeout=10,
                   json={"messages": [{"role": "user", "content": "what is this?"}], "tier": "fast", "max_tokens": 50})
    assert r.json() == {"text": "a cat on a keyboard"} and seen["kw"]["max_tokens"] == 50
    assert httpx.post(server + "/api/v1/llm", json={"messages": []}).status_code == 401


def test_away_mode_shares_key_with_phones_only(server, admin, tmp_path):
    phone = _agent(server, admin, "Adam Phone", "phone", tmp_path)
    pc = _agent(server, admin, "Study PC", "desktop", tmp_path)
    admin.put("/admin/api/settings", json={"gemini_api_key": "AIza-test-key"})
    assert "offline" not in phone.session()               # off by default
    admin.put("/admin/api/settings", json={"away_mode": True})
    off = phone.session()["offline"]
    assert off["api_key"] == "AIza-test-key" and off["model"]
    assert "offline" not in pc.session()                   # desktops stay at home
    admin.put("/admin/api/settings", json={"away_mode": False, "gemini_api_key": ""})


def test_presence_automation_runs_instruction(server, admin, tmp_path, monkeypatch):
    import time as _t
    from ghost_server import app as app_mod
    seen = []

    def fake_reply(dev, text):
        seen.append((dev["name"], text))
        return {"text": "Turned off the lights.", "tools": ["home_assistant"]}
    monkeypatch.setattr(app_mod.chat, "reply", fake_reply)
    phone = _agent(server, admin, "Presence Phone", "phone", tmp_path)
    # nothing configured → just logged
    r = httpx.post(server + "/api/v1/events", headers={"Authorization": "Bearer " + phone.key},
                   json={"type": "presence", "data": {"state": "left_home"}}).json()
    assert r == {"ok": True} and not seen
    admin.put("/admin/api/settings", json={"on_left_home": "turn off all the lights"})
    r = httpx.post(server + "/api/v1/events", headers={"Authorization": "Bearer " + phone.key},
                   json={"type": "presence", "data": {"state": "left_home"}}).json()
    assert r.get("automation") is True
    for _ in range(50):
        if seen:
            break
        _t.sleep(0.05)
    assert seen and seen[0][0] == "Presence Phone"
    assert "turn off all the lights" in seen[0][1] and "Never unlock doors" in seen[0][1]
    admin.put("/admin/api/settings", json={"on_left_home": ""})


def test_upload_file_and_download_from_another_device(server, admin, tmp_path):
    phone = _agent(server, admin, "Upload Phone", "phone", tmp_path)
    pc = _agent(server, admin, "Upload PC", "desktop", tmp_path)
    jpg = b"\xff\xd8\xff\xe0" + b"x" * 1000
    r = httpx.post(server + "/api/v1/files?name=../../IMG 1.jpg", content=jpg,
                   headers={"Authorization": "Bearer " + phone.key}).json()
    assert r["path"].startswith("/files/") and r["name"] == "IMG 1.jpg" and r["bytes"] == len(jpg)
    got = httpx.get(server + r["path"], headers={"Authorization": "Bearer " + pc.key})
    assert got.status_code == 200 and got.content == jpg
    assert httpx.get(server + r["path"]).status_code in (401, 403)
    assert httpx.post(server + "/api/v1/files", content=b"x").status_code == 401


def test_dashboard_and_home_switch(server, admin, tmp_path, monkeypatch):
    from ghost_server import dashboard
    from ghost_server.tools import home
    tab = _agent(server, admin, "Kitchen Hub", "tablet", tmp_path)
    H = {"Authorization": "Bearer " + tab.key}
    monkeypatch.setattr(dashboard, "weather_data", lambda city: {"city": city, "temp": 18, "desc": "clear", "days": []})
    monkeypatch.setattr(dashboard, "headlines", lambda: [{"title": "Big news", "source": "ABC", "url": ""}])
    dashboard._cache.clear()
    d = httpx.get(server + "/api/v1/dashboard", headers=H).json()
    assert d["weather"]["temp"] == 18 and d["headlines"][0]["title"] == "Big news"
    assert d["home"] == {"connected": False, "tiles": []}          # no Home Assistant token yet
    states = [{"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen", "brightness": 255}},
              {"entity_id": "lock.front_door", "state": "locked", "attributes": {"friendly_name": "Front door"}},
              {"entity_id": "switch.kettle_led", "state": "on", "attributes": {}}]
    calls = []

    class R:
        def __init__(self, data, code=200):
            self.data, self.status_code = data, code

        def json(self):
            return self.data
    monkeypatch.setattr(home, "_req", lambda m, p, cfg=None, **kw:
                        R(next(s for s in states if p.endswith(s["entity_id"]))) if "/api/states/" in p else R(states))
    monkeypatch.setattr(home, "_call", lambda d_, s_, data: calls.append((d_, s_, data)) or 200)
    admin.put("/admin/api/settings", json={"ha_token": "t", "ha_url": "http://ha"})
    dashboard._cache.clear()
    tiles = httpx.get(server + "/api/v1/dashboard", headers=H).json()["home"]["tiles"]
    ids = [t["entity_id"] for t in tiles]
    assert "light.kitchen" in ids and "lock.front_door" in ids and "switch.kettle_led" not in ids
    assert next(t for t in tiles if t["entity_id"] == "lock.front_door")["switchable"] is False
    r = httpx.post(server + "/api/v1/home/switch", headers=H, json={"entity_id": "light.kitchen", "action": "off"}).json()
    assert r["ok"] and calls[-1] == ("light", "turn_off", {"entity_id": "light.kitchen"})
    r = httpx.post(server + "/api/v1/home/switch", headers=H, json={"entity_id": "lock.front_door", "action": "toggle"}).json()
    assert r["ok"] is False and len(calls) == 1                     # locks never operated
    assert httpx.post(server + "/api/v1/home/switch", headers=H, json={"entity_id": "x; rm"}).status_code == 400
    admin.put("/admin/api/settings", json={"ha_token": "", "tablet_entities": "lock.front_door"})


def test_cameras_rtsp_view_stream_and_show(server, admin, tmp_path, monkeypatch):
    import functools
    import http.server
    import shutil
    import subprocess
    import threading
    from ghost_server import cameras
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg isn't installed here (it is in the Docker image)")
    # a stand-in "camera": a test video made here, served over http, read by ffmpeg like an RTSP feed
    camdir = tmp_path / "cam"
    camdir.mkdir()
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=640x360:rate=10", "-t", "20", "-c:v", "mpeg2video", "-q:v", "5",
                    "-f", "mpegts", str(camdir / "test.ts")], check=True, timeout=60)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(camdir))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cam_url = f"http://127.0.0.1:{srv.server_port}/test.ts"
    admin.put("/admin/api/settings", json={"ha_token": "", "rtsp_cameras":
                                           f"Front Gate = {cam_url}\n# comment\nBackyard = {cam_url}\nbad line"})
    assert admin.get("/admin/api/overview").json()["settings"]["rtsp_cameras"].startswith("•••")   # passwords masked
    phone = _agent(server, admin, "Cam Phone", "phone", tmp_path)
    H = {"Authorization": "Bearer " + phone.key}
    names = [c["name"] for c in httpx.get(server + "/api/v1/cameras", headers=H).json()["cameras"]]
    assert names == ["Front Gate", "Backyard"]
    assert [c.name for c in cameras.find("the front gate camera")] == ["Front Gate"]
    assert len(cameras.find("all")) == 2 and cameras.find("garage") == []
    v = httpx.post(server + "/api/v1/cameras/view", headers=H, json={"camera": "backyard"}).json()
    assert v["title"] == "Backyard" and v["path"].startswith("/cam/")
    page = httpx.get(server + v["path"]).text
    assert "/stream/0" in page and "Backyard" in page and cam_url not in page             # no camera URL leaks
    with httpx.stream("GET", server + v["path"] + "/stream/0", timeout=30) as r:
        assert r.status_code == 200 and "multipart/x-mixed-replace" in r.headers["content-type"]
        got = b""
        for chunk in r.iter_bytes():
            got += chunk
            if got.count(b"\xff\xd8") >= 2:
                break
    assert got.count(b"\xff\xd8") >= 2                                                    # real JPEG frames
    snap = httpx.get(server + v["path"] + "/snap/0", timeout=30)
    assert snap.status_code == 200 and snap.content[:2] == b"\xff\xd8"
    assert httpx.get(server + "/cam/nope").status_code == 410
    assert httpx.post(server + "/api/v1/cameras/view", json={}).status_code == 401
    # Ghost: "show the front gate" → the phone gets a show_camera call with a view link
    from ghost_server import tools
    from ghost_server.hub import hub
    sent = []
    monkeypatch.setattr(hub, "call_sync", lambda dev_id, tool, args, timeout=20.0: sent.append((tool, args)) or {"ok": True, "result": "shown"})
    monkeypatch.setattr(hub, "is_online", lambda dev_id: True)

    class C:
        capabilities = [{"name": "open_camera_view"}]
    from ghost_server import store as st
    pid = st.device_by_key(phone.key)["id"]
    monkeypatch.setitem(hub.conns, pid, C())
    out = tools.run("show_camera", {"camera": "front gate"}, tools.Ctx(pid, "Cam Phone", "live"))
    assert "Showing Front Gate live" in out and sent[0][0] == "open_camera_view"
    assert sent[0][1]["path"].startswith("/cam/") and cameras.view(sent[0][1]["path"][5:])[0].name == "Front Gate"
    admin.put("/admin/api/settings", json={"rtsp_cameras": ""})


def test_cameras_home_assistant_stream(server, admin, tmp_path):
    import http.server
    import json as _json
    import threading

    class FakeHA(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.headers.get("Authorization") != "Bearer ha-tok":
                self.send_response(401); self.end_headers(); return
            if self.path == "/api/states":
                body = _json.dumps([{"entity_id": "camera.front_door", "state": "idle",
                                     "attributes": {"friendly_name": "Front Door"}},
                                    {"entity_id": "light.x", "state": "on", "attributes": {}}]).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/camera_proxy_stream/camera.front_door":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=frame"); self.end_headers()
                for _ in range(3):
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8fakejpeg\xff\xd9\r\n")
            elif self.path == "/api/camera_proxy/camera.front_door":
                self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.end_headers()
                self.wfile.write(b"\xff\xd8snap\xff\xd9")
            else:
                self.send_response(404); self.end_headers()
    ha = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeHA)
    threading.Thread(target=ha.serve_forever, daemon=True).start()
    admin.put("/admin/api/settings", json={"ha_url": f"http://127.0.0.1:{ha.server_port}", "ha_token": "ha-tok",
                                           "rtsp_cameras": ""})
    tab = _agent(server, admin, "Cam Tablet", "tablet", tmp_path)
    H = {"Authorization": "Bearer " + tab.key}
    assert httpx.get(server + "/api/v1/cameras", headers=H).json()["cameras"] == \
        [{"id": "camera.front_door", "name": "Front Door", "source": "ha"}]
    v = httpx.post(server + "/api/v1/cameras/view", headers=H, json={"camera": "door"}).json()
    body = httpx.get(server + v["path"] + "/stream/0", timeout=20).content
    assert body.count(b"\xff\xd8fakejpeg") == 3 and b"ha-tok" not in body
    assert httpx.get(server + v["path"] + "/snap/0").content == b"\xff\xd8snap\xff\xd9"
    admin.put("/admin/api/settings", json={"ha_token": ""})


def test_camera_watch_alerts_only_on_real_change(server, admin, tmp_path, monkeypatch):
    import io
    import time as _t
    from PIL import Image, ImageDraw
    from ghost_server import cameras, store as st, tools, watcher
    from ghost_server.hub import hub

    def frame(brightness=90, person=False):
        im = Image.new("RGB", (640, 360), (brightness, brightness, brightness))
        if person:
            ImageDraw.Draw(im).rectangle([250, 80, 390, 340], fill=(230, 40, 40))
        b = io.BytesIO(); im.save(b, "JPEG"); return b.getvalue()

    # the scene: still, then just brighter (a cloud), then someone walks in
    script = [frame()] * 4 + [frame(115)] * 4 + [frame(115, person=True)] * 30
    shots = {"n": 0}

    def fake_snapshot(cam, timeout=15):
        i = min(shots["n"], len(script) - 1); shots["n"] += 1
        return script[i]
    looks, sent = [], []
    monkeypatch.setattr(cameras, "snapshot", fake_snapshot)
    monkeypatch.setattr(watcher, "judge", lambda w, jpeg: looks.append(1) or
                        {"alert": True, "summary": "Someone's at the front gate."})
    monkeypatch.setattr(watcher, "LOOK_COOLDOWN", 0)
    monkeypatch.setattr(watcher, "ALERT_COOLDOWN", 3600)        # one alert, not a stream of them
    monkeypatch.setattr(hub, "is_online", lambda d: True)
    monkeypatch.setattr(hub, "send_sync", lambda d, m: sent.append((d, m)) or True)
    monkeypatch.setattr(hub, "call_sync", lambda *a, **k: {"ok": True})

    assert watcher.changed_fraction(watcher.tiny(frame()), watcher.tiny(frame(115))) < watcher.CHANGE_THRESHOLD
    assert watcher.changed_fraction(watcher.tiny(frame()), watcher.tiny(frame(90, True))) > watcher.CHANGE_THRESHOLD

    phone = _agent(server, admin, "Watch Phone", "phone", tmp_path)
    pid = st.device_by_key(phone.key)["id"]
    cam = cameras.Cam("rtsp:front-gate", "Front Gate", "rtsp", "rtsp://x")
    w = watcher.start(cam, 5, "people", [pid], pid, poll=0.02)
    for _ in range(200):
        if sent:
            break
        _t.sleep(0.02)
    _t.sleep(0.3)
    assert len(sent) == 1 and sent[0][1]["text"] == "Someone's at the front gate."
    assert sent[0][1]["picture"].startswith("/files/") and len(looks) == 1      # Claude asked once, alerted once
    assert shots["n"] > 8                                                        # brightness change alone: no alert
    assert any(e["type"] == "camera_alert" for e in st.events(pid)) if hasattr(st, "events") else True
    status = tools.run("camera_watch_status", {}, tools.Ctx(pid, "Watch Phone", "live"))
    assert "Front Gate" in status and "1 alert" in status
    assert watcher.stop("front gate") == ["Front Gate"] and not watcher.WATCHES

    # the tool: needs a camera list and Claude; parses 'until'
    monkeypatch.setattr(cameras, "all_cameras", lambda: [cam])
    admin.put("/admin/api/settings", json={"anthropic_api_key": "sk-test"})
    started = []
    monkeypatch.setattr(watcher, "start", lambda c, m, lf, n, by, poll=3: started.append((c.name, m, lf, n)) or
                        watcher.Watch(c.id, c, _t.time() + m * 60, lf, n, by))
    out = tools.run("watch_camera", {"camera": "front gate", "until": "7am", "look_for": "a parcel"},
                    tools.Ctx(pid, "Watch Phone", "live"))
    assert "Watching Front Gate until" in out and "a parcel" in out
    assert 0 < started[0][1] <= 24 * 60 and started[0][3][0] == pid
    assert watcher.minutes_from({"minutes": 30}) == 30 and watcher.minutes_from({}) == 60


def test_cameras_status_endpoint(server, admin, tmp_path):
    from ghost_server import store as st
    ph = _agent(server, admin, "Status Phone", "phone", tmp_path)
    pid = st.device_by_key(ph.key)["id"]
    st.add_event(pid, "camera_alert", {"camera": "Front Door", "summary": "A courier left a parcel.", "picture": "/files/x"})
    d = httpx.get(server + "/api/v1/cameras/status", headers={"Authorization": "Bearer " + ph.key}).json()
    assert d["alerts"][0]["summary"] == "A courier left a parcel." and d["watching"] == [] and "cameras" in d


def test_quiet_schedule_and_words():
    from ghost_server import quiet
    import datetime as dt
    from zoneinfo import ZoneInfo
    assert quiet.parse_schedule("10pm to 6:30am") == ((22, 0), (6, 30))
    assert quiet.parse_schedule("22:00-06:30") == ((22, 0), (6, 30)) and quiet.parse_schedule("off") is None
    assert quiet.parse_clock("7") == (7, 0) and quiet.parse_clock("12am") == (0, 0) and quiet.parse_clock("25:00") is None
    tz = ZoneInfo("Australia/Melbourne")
    at = lambda h, m=0: dt.datetime(2026, 11, 5, h, m, tzinfo=tz).timestamp()
    assert quiet._window("22:00-06:30", at(23))[0] and quiet._window("22:00-06:30", at(3))[0]
    assert not quiet._window("22:00-06:30", at(12))[0]
    end = quiet._window("22:00-06:30", at(23))[1]
    assert dt.datetime.fromtimestamp(end, tz).hour == 6 and dt.datetime.fromtimestamp(end, tz).day == 6
    assert quiet.has_emergency_words("Can you call me now? It's urgent")
    assert quiet.has_emergency_words("We're at the hospital") and not quiet.has_emergency_words("fireplace is cosy")
    assert not quiet.has_emergency_words("see you at dinner")


def test_quiet_mode_holds_lets_through_and_catches_up(server, admin, tmp_path):
    from ghost_server import quiet, store as st
    desk = _agent(server, admin, "Quiet PC", "desktop", tmp_path)
    tablet = _agent(server, admin, "Quiet Tablet", "tablet", tmp_path)
    phone = _agent(server, admin, "Hayley's phone", "phone", tmp_path)
    admin.put("/admin/api/settings", json={"quiet_favourites": "Hayley", "safety_alerts": False,
                                           "quiet_me_devices": "Quiet PC"})
    said_desk, said_tab, said_phone, states = [], [], [], []
    phone.on_say = lambda t: said_phone.append(t)
    desk.on_say = lambda t: said_desk.append(t)
    tablet.on_say = lambda t: said_tab.append(t)
    desk.on_message = lambda m: states.append(m) if m.get("type") == "quiet" else None
    H = {"Authorization": "Bearer " + desk.key}

    async def scenario():
        stop = asyncio.Event()
        tasks = [asyncio.create_task(a.run_link(stop)) for a in (desk, tablet, phone)]
        for _ in range(100):
            if desk.connected and tablet.connected and phone.connected:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)
        # personal Do Not Disturb by voice: the PC goes quiet, the shared tablet doesn't
        r = await asyncio.to_thread(desk.call_tool, "quiet_mode", {"action": "on", "minutes": 60})
        await asyncio.sleep(0.3)
        await asyncio.to_thread(phone.call_tool, "announce", {"text": "Bring milk", "device": "all"})       # favourite
        await asyncio.to_thread(tablet.call_tool, "announce", {"text": "Dinner's ready", "device": "all"})  # held on PC
        await asyncio.to_thread(tablet.call_tool, "announce", {"text": "Emergency — come quick", "device": "Quiet PC"})
        await asyncio.sleep(0.3)
        during = list(said_desk)
        # switching off from the app gives a catch-up of what was held
        r2 = await asyncio.to_thread(lambda: httpx.post(server + "/api/v1/quiet", headers=H,
                                                       json={"scope": "me", "on": False}).json())
        await asyncio.sleep(0.4)
        stop.set()
        for t in tasks:
            t.cancel()
        return r, during, r2

    r, during, r2 = asyncio.run(scenario())
    assert "Do Not Disturb is on until" in r and "still get through" in r
    assert "Bring milk" in during and "Emergency — come quick" in during and "Dinner's ready" not in during
    assert "Dinner's ready" in said_phone                    # Hayley's phone isn't one of "my devices"
    assert "Bring milk" in said_tab                          # nor is the shared tablet
    assert r2["active"] is False and r2["me"]["active"] is False
    assert any("While Do Not Disturb was on" in s and "Dinner's ready" in s for s in said_desk)
    assert any(m.get("active") for m in states) and states[-1]["active"] is False
    # house quiet covers tablets too; a schedule turns it on by itself
    quiet.set_quiet("house", True, until="11:59pm")
    assert quiet.device_quiet("tablet") and quiet.device_quiet("desktop")
    quiet.set_quiet("house", False)
    assert not quiet.device_quiet("tablet")
    assert quiet.gets_through({"from": "Safety alert", "text": "Kitchen smoke", "urgent": True})
    st.put("quiet_favourites", ""); st.put("quiet_me_devices", "")


def test_safety_check_reports_new_problems_only():
    from ghost_server import quiet
    quiet._safety_seeded = False
    base = [{"entity_id": "binary_sensor.kitchen_smoke", "state": "off",
             "attributes": {"device_class": "smoke", "friendly_name": "Kitchen smoke"}},
            {"entity_id": "binary_sensor.laundry_leak", "state": "on",
             "attributes": {"device_class": "moisture", "friendly_name": "Laundry leak"}},
            {"entity_id": "binary_sensor.front_door", "state": "on", "attributes": {"device_class": "door"}}]
    assert quiet.safety_check(base) == []                    # first look only learns what's already on
    base[0]["state"] = "on"
    assert quiet.safety_check(base) == ["Kitchen smoke — smoke detected"]
    assert quiet.safety_check(base) == []                    # still on: not repeated
    alarm = base + [{"entity_id": "alarm_control_panel.house", "state": "triggered", "attributes": {"friendly_name": "House alarm"}}]
    assert quiet.safety_check(alarm) == ["House alarm — the alarm has gone off"]


def test_assist_room_speaker_voice_and_room_announce(server, admin, tmp_path, monkeypatch):
    """Home Assistant room speakers: spoken-style answers that know the room, their own
    history per room, and 'announce to the bedroom' reaching the hub with the room."""
    from ghost_server.tools import brain
    seen = []

    class FakeClaude:
        def complete(self, history, tools=None, **kw):
            seen.append(history[0]["content"])
            return "The office lights are off.", []
    monkeypatch.setattr(brain, "claude", lambda *a, **k: FakeClaude())
    ha = _agent(server, admin, "Home Assistant", "homeassistant", tmp_path)
    h = {"Authorization": f"Bearer {ha.key}"}
    r = httpx.post(server + "/api/v1/assist", headers=h, timeout=30,
                   json={"text": "turn the lights off", "room": "Office", "satellite": "Office Screen"}).json()
    assert r["text"] == "The office lights are off."
    assert "talking to you from the Office" in seen[-1] and "SPOKEN ALOUD" in seen[-1]
    assert r["conversation_id"].endswith("~office")
    # each room keeps its own history
    httpx.post(server + "/api/v1/assist", headers=h, timeout=30, json={"text": "hello", "room": "Bedroom"})
    assert "talking to you from the Bedroom" in seen[-1]
    # announce to a room → the hub gets a 'say' carrying the room
    got = []

    async def run():
        import websockets
        async with websockets.connect(server.replace("http", "ws") + f"/api/v1/agent?key={ha.key}") as ws:
            await ws.send(json.dumps({"type": "hello", "capabilities": [],
                                      "info": {"rooms": ["Office", "Bedroom", "Lounge"],
                                               "speakers": [
                                                   {"id": "notify.kitchen_echo_dot_announce", "name": "Kitchen Echo Dot", "area": "Kitchen"},
                                                   {"id": "notify.zoey_echo_announce", "name": "Zoey's Echo", "area": "Bedroom"},
                                                   {"id": "assist_satellite.bedroom", "name": "Bedroom Screen", "area": "Bedroom"}]}}))
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if m["type"] == "ready":
                    break
            other = _agent(server, admin, "Office PC 2", "desktop", tmp_path)
            res = await asyncio.to_thread(other.call_tool, "announce", {"text": "Bedtime!", "device": "the bedroom"})
            assert "Bedroom" in res
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if m["type"] == "say":
                    got.append(m)
                    break
            # one named speaker: only that Echo, not the whole room
            for target in ("Zoey's Echo", "the kitchen echo"):
                res = await asyncio.to_thread(other.call_tool, "announce", {"text": "Hi", "device": target})
                assert res.startswith("Said it on")
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if m["type"] == "say":
                        got.append(m)
                        break
            assert "Kitchen Echo Dot" in await asyncio.to_thread(other.call_tool, "list_devices", {})
    asyncio.run(run())
    assert got[0]["room"] == "Bedroom" and got[0]["text"] == "Bedtime!" and "speaker" not in got[0]
    assert got[1]["speaker"] == "notify.zoey_echo_announce"
    assert got[2]["speaker"] == "notify.kitchen_echo_dot_announce"


def _ws_until(server, key, pred, actions=None, timeout=8):
    """Open a live link, run `actions` once ready, return the first message where pred(msg)."""
    async def run():
        import websockets
        async with websockets.connect(server.replace("http", "ws") + f"/api/v1/agent?key={key}") as ws:
            await ws.send(json.dumps({"type": "hello", "capabilities": []}))
            end = time.time() + timeout
            fired = False
            while time.time() < end:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
                if m["type"] == "ready" and not fired and actions:
                    fired = True
                    await asyncio.to_thread(actions)
                    continue
                if pred(m) and (fired or not actions):
                    return m
        return None
    return asyncio.run(run())


def test_timers_ring_on_the_device_and_stop(server, admin, tmp_path):
    a = _agent(server, admin, "Timer Tablet", "tablet", tmp_path)
    h = {"Authorization": f"Bearer {a.key}"}
    box = {}

    def set_it():
        box["t"] = httpx.post(server + "/api/v1/timers", headers=h, json={"seconds": 1, "label": "pasta"}).json()
    m = _ws_until(server, a.key, lambda m: m["type"] == "timer" and m.get("event") == "ring", set_it)
    assert m and m["timer"]["label"] == "pasta" and "pasta timer is done" in m["text"]
    assert httpx.post(server + f"/api/v1/timers/{box['t']['id']}", headers=h, json={"action": "stop"}).json()["ok"]
    assert all(t["id"] != box["t"]["id"] for t in httpx.get(server + "/api/v1/timers", headers=h).json()["items"])
    # by voice: set, list, cancel
    say = lambda args: a.call_tool("timer", args)
    assert "set for 10 minutes" in say({"action": "set", "minutes": 10, "label": "eggs"})
    assert "eggs timer: 10 minutes left" in say({"action": "list"}) or "eggs timer: 9 minutes" in say({"action": "list"})
    assert "Alarm set for" in say({"action": "alarm", "time": "6:30am", "days": "weekdays"})
    assert "Stopped" in say({"action": "stop", "label": "eggs"})
    assert "Stopped" in say({"action": "stop", "label": "alarm"})


def test_lists_and_notes(server, admin, tmp_path):
    a = _agent(server, admin, "List Phone", "phone", tmp_path)
    h = {"Authorization": f"Bearer {a.key}"}
    assert "milk, eggs" in a.call_tool("lists", {"action": "add", "items": "milk, eggs"}).lower()
    assert "already" in a.call_tool("lists", {"action": "add", "items": "Milk"})
    assert "Ticked off Eggs" in a.call_tool("lists", {"action": "done", "items": "eggs"})
    shop = httpx.get(server + "/api/v1/lists", headers=h).json()["lists"]["shopping"]
    assert [i["text"] for i in shop if not i["done"]] == ["Milk"]
    assert "Cleared 1" in a.call_tool("lists", {"action": "clear"})
    a.call_tool("lists", {"action": "add", "list": "my to-do list", "items": "fix the gate"})
    assert "Fix the gate" in a.call_tool("lists", {"action": "show", "list": "todo"})
    # notes
    assert "Note left for Zoe" in a.call_tool("notes", {"action": "leave", "to": "Zoe", "text": "Feed the cat", "from": "Dad"})
    a.call_tool("notes", {"action": "leave", "to": "everyone", "text": "Pizza night Friday"})
    zoe = httpx.get(server + "/api/v1/notes", headers=h, params={"who": "Zoe"}).json()["items"]
    assert {n["text"] for n in zoe} == {"Feed the cat", "Pizza night Friday"}
    other = httpx.get(server + "/api/v1/notes", headers=h, params={"who": "Sam"}).json()["items"]
    assert [n["text"] for n in other] == ["Pizza night Friday"]
    assert "Marked 1" in a.call_tool("notes", {"action": "done", "text": "feed the cat"})


def test_visual_id_enroll_identify_greet_and_prompt(server, admin, tmp_path, monkeypatch):
    import base64
    from ghost_server import faces, prompt, store

    class FakeEngine:
        def faces(self, jpeg):
            v = {b"ADAM": [1, 0, 0], b"ADAM2": [0.95, 0.31, 0], b"GUEST": [0, 0, 1]}
            return [] if jpeg == b"NOFACE" else [([10, 10, 100, 100], v[jpeg])]
    monkeypatch.setattr(faces, "engine", FakeEngine())
    img = lambda b: base64.b64encode(b).decode()
    t = _agent(server, admin, "Kitchen Hub", "tablet", tmp_path)
    h = {"Authorization": f"Bearer {t.key}"}
    # adding someone needs the admin password
    bad = httpx.post(server + "/api/v1/faces/enroll", headers=h,
                     json={"name": "Adam", "image": img(b"ADAM"), "admin_password": "nope"})
    assert bad.status_code == 403
    r = httpx.post(server + "/api/v1/faces/enroll", headers=h,
                   json={"name": "Adam", "image": img(b"ADAM"), "admin_password": "ghost-pass"}).json()
    assert r["ok"] and r["samples"] == 1
    tk = r["ticket"]                       # the rest of the photos ride on the ticket (no password again)
    r2 = httpx.post(server + "/api/v1/faces/enroll", headers=h,
                    json={"name": "Adam", "image": img(b"ADAM"), "ticket": tk}).json()
    assert r2["ok"] and r2["samples"] == 2
    assert httpx.post(server + "/api/v1/faces/enroll", headers=h,
                      json={"name": "Adam", "image": img(b"ADAM"), "ticket": "forged"}).status_code == 403
    r = httpx.post(server + "/api/v1/faces/enroll", headers=h,
                   json={"name": "adam", "image": img(b"NOFACE"), "admin_password": "ghost-pass"}).json()
    assert not r["ok"] and "couldn't see a face" in r["error"]
    # recognised (a slightly different shot), greeted once, with personal notes
    t.call_tool("notes", {"action": "leave", "to": "Adam", "text": "Bins go out tonight"})
    r = httpx.post(server + "/api/v1/faces/identify", headers=h, json={"image": img(b"ADAM2")}).json()
    assert r["people"][0]["name"] == "Adam" and r["greet"].endswith(", Adam")
    assert any(n["text"] == "Bins go out tonight" for n in r["notes"])
    again = httpx.post(server + "/api/v1/faces/identify", headers=h, json={"image": img(b"ADAM2")}).json()
    assert "greet" not in again
    # Ghost on this tablet now knows who it's talking to
    sysmsg = prompt.build(dict(store.device_by_key(t.key)))
    assert "recognises Adam" in sysmsg
    assert "Adam at the Kitchen Hub" in t.call_tool("who_is_here", {})
    # a stranger is a guest, and nothing about them is stored
    r = httpx.post(server + "/api/v1/faces/identify", headers=h, json={"image": img(b"GUEST")}).json()
    assert r["people"] == [] and r["guests"] == 1
    assert "don't recognise" in prompt.build(dict(store.device_by_key(t.key)))
    people = admin.get("/admin/api/people").json()["people"]
    assert [(p["name"], p["samples"]) for p in people] == [("Adam", 2)]
    # delete removes every print
    admin.post(f"/admin/api/people/{people[0]['id']}", json={"delete": True})
    r = httpx.post(server + "/api/v1/faces/identify", headers=h, json={"image": img(b"ADAM")}).json()
    assert r["people"] == []


def test_photo_frame(server, admin, tmp_path):
    from PIL import Image
    from ghost_server import photos
    pics = tmp_path / "pics"
    (pics / "2025").mkdir(parents=True)
    Image.new("RGB", (3000, 2000), (200, 30, 30)).save(pics / "2025" / "beach.jpg")
    Image.new("RGB", (800, 600), (30, 30, 200)).save(pics / "cat.png")
    (pics / "notes.txt").write_text("not a photo")
    admin.put("/admin/api/settings", json={"photo_folder": str(pics)})
    photos._scan["at"] = 0
    t = _agent(server, admin, "Photo Hub", "tablet", tmp_path)
    lst = httpx.get(server + "/api/v1/photos", headers={"Authorization": f"Bearer {t.key}"}).json()
    assert lst["found"] and lst["count"] == 2 and sorted(lst["photos"]) == [0, 1]
    r = httpx.get(server + "/photos/0", params={"k": t.key, "w": 1200})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    import io
    assert max(Image.open(io.BytesIO(r.content)).size) == 1200
    assert httpx.get(server + "/photos/0", params={"k": "wrong"}).status_code == 401


def test_timer_on_a_phone_is_spoken(server, admin, tmp_path):
    p = _agent(server, admin, "Timer Phone", "phone", tmp_path)
    h = {"Authorization": f"Bearer {p.key}"}
    m = _ws_until(server, p.key, lambda m: m["type"] == "say" and m.get("from") == "timer",
                  lambda: httpx.post(server + "/api/v1/timers", headers=h, json={"seconds": 1, "label": "tea"}))
    assert m and m["urgent"] and "tea timer is done" in m["text"]


# ─────────────────────────────────────────────────────────────────────────────
#  Security
# ─────────────────────────────────────────────────────────────────────────────
def test_admin_needs_csrf_header(server, admin):
    bare = httpx.Client(base_url=server, cookies=admin.cookies)
    assert bare.get("/admin/api/overview").status_code == 200                # reading is fine
    assert bare.put("/admin/api/settings", json={"city": "Paris"}).status_code == 403
    assert admin.put("/admin/api/settings", json={"city": "Melbourne"}).status_code == 200


def test_admin_lockout(server, admin):
    from ghost_server import security, store
    security.reset_all()
    for _ in range(5):
        assert httpx.post(server + "/admin/login", json={"password": "nope"}).status_code == 401
    r = httpx.post(server + "/admin/login", json={"password": "ghost-pass"})
    assert r.status_code == 429 and "locked" in r.json()["detail"]
    assert any("locked" in e["what"] for e in store.security_events(20))
    security.reset_all()
    assert httpx.post(server + "/admin/login", json={"password": "ghost-pass"}).status_code == 200


def test_pairing_lockout(server, admin):
    from ghost_server import security
    security.reset_all()
    for _ in range(5):
        assert httpx.post(server + "/api/v1/pair", json={"code": "000000", "kind": "phone"}).status_code == 400
    good = admin.post("/admin/api/pair-code").json()["code"]
    r = httpx.post(server + "/api/v1/pair", json={"code": good, "kind": "phone"})
    assert r.status_code == 429 and "paused" in r.json()["detail"]
    security.reset_all()
    assert httpx.post(server + "/api/v1/pair", json={"code": good, "kind": "phone"}).status_code == 200


def test_two_step_login(server, admin):
    from ghost_server import security
    security.reset_all()
    start = admin.post("/admin/api/2fa/start").json()
    assert start["uri"].startswith("otpauth://totp/") and start["secret"]
    assert "<svg" in start["qr"]
    assert admin.post("/admin/api/2fa/enable", json={"code": "123456"}).status_code == 400
    r = admin.post("/admin/api/2fa/enable", json={"code": security.totp_now(start["secret"])}).json()
    codes = r["backup_codes"]
    assert len(codes) == 8 and admin.get("/admin/state").json()["two_step"] is True
    assert admin.get("/admin/api/security").json()["two_step"]["backup_left"] == 8

    # password alone isn't enough any more
    c = httpx.Client(base_url=server, headers={"X-Ghost-Admin": "1"})
    assert c.post("/admin/login", json={"password": "ghost-pass"}).json() == {"ok": False, "need_code": True}
    assert c.post("/admin/login", json={"password": "ghost-pass", "code": "000000"}).status_code == 401
    security._last_step["n"] = 0                      # the code used to switch it on can be used again here
    ok = c.post("/admin/login", json={"password": "ghost-pass", "code": security.totp_now(start["secret"])})
    assert ok.status_code == 200 and c.get("/admin/api/overview").status_code == 200
    # the same code can't be replayed
    c2 = httpx.Client(base_url=server, headers={"X-Ghost-Admin": "1"})
    assert c2.post("/admin/login", json={"password": "ghost-pass",
                                         "code": security.totp_now(start["secret"])}).status_code == 401
    # a backup code works once
    assert c2.post("/admin/login", json={"password": "ghost-pass", "code": codes[0]}).status_code == 200
    c3 = httpx.Client(base_url=server, headers={"X-Ghost-Admin": "1"})
    assert c3.post("/admin/login", json={"password": "ghost-pass", "code": codes[0]}).status_code == 401
    assert admin.get("/admin/api/security").json()["two_step"]["backup_left"] == 7

    # sessions: three browsers signed in; sign out the others
    sess = admin.get("/admin/api/security").json()["sessions"]
    assert len(sess) >= 3 and sum(s["this"] for s in sess) == 1
    assert admin.post("/admin/api/sessions/others").json()["signed_out"] >= 2
    assert c.get("/admin/api/overview").status_code == 401
    assert admin.get("/admin/api/overview").status_code == 200

    # turning it off needs the password and a code
    assert admin.post("/admin/api/2fa/disable", json={"password": "ghost-pass"}).status_code == 403
    assert admin.post("/admin/api/2fa/disable", json={"password": "ghost-pass", "code": codes[1]}).status_code == 200
    assert admin.get("/admin/state").json()["two_step"] is False
    security.reset_all()


def test_device_approval_and_key_rotation(server, admin, tmp_path):
    from ghost_server import store
    admin.put("/admin/api/settings", json={"approve_new_devices": True})
    try:
        code = admin.post("/admin/api/pair-code").json()["code"]
        p = httpx.post(server + "/api/v1/pair", json={"code": code, "name": "Bedroom Screen", "kind": "room"}).json()
        assert p["approved"] is False
        hdr = {"Authorization": "Bearer " + p["device_key"]}
        r = httpx.get(server + "/api/v1/session", headers=hdr)
        assert r.status_code == 403 and "approval" in r.json()["detail"]

        async def ws_code():
            import websockets
            url = server.replace("http", "ws") + "/api/v1/agent?key=" + p["device_key"]
            async with websockets.connect(url) as ws:
                try:
                    await ws.recv()
                except websockets.ConnectionClosed as e:
                    return e.rcvd.code if e.rcvd else None
        assert asyncio.run(ws_code()) == 4403

        sec = admin.get("/admin/api/security").json()
        assert any(d["id"] == p["device_id"] for d in sec["pending"])
        assert admin.post(f"/admin/api/devices/{p['device_id']}", json={"approve": True}).status_code == 200
        s = httpx.get(server + "/api/v1/session", headers=hdr).json()
        assert s["key"]["rotate_days"] == 30 and s["key"]["age_days"] < 1
        assert httpx.get(server + "/api/v1/key", headers=hdr).json()["rotate_days"] == 30

        # renew the key: new one works, old one still works for a grace period
        new = httpx.post(server + "/api/v1/key/rotate", headers=hdr).json()["device_key"]
        assert new != p["device_key"]
        assert httpx.get(server + "/api/v1/session", headers={"Authorization": "Bearer " + new}).status_code == 200
        assert httpx.get(server + "/api/v1/session", headers=hdr).status_code == 200
        store.x("UPDATE devices SET prev_until = 0 WHERE id = ?", (p["device_id"],))
        assert httpx.get(server + "/api/v1/session", headers=hdr).status_code == 401

        # rejecting another waiting device
        code = admin.post("/admin/api/pair-code").json()["code"]
        q = httpx.post(server + "/api/v1/pair", json={"code": code, "name": "Stranger", "kind": "phone"}).json()
        admin.post(f"/admin/api/devices/{q['device_id']}", json={"revoke": True})
        assert httpx.get(server + "/api/v1/session",
                         headers={"Authorization": "Bearer " + q["device_key"]}).status_code == 401
        log = [e["what"] for e in admin.get("/admin/api/security").json()["log"]]
        assert any("asked to join" in w for w in log) and any("approved" in w for w in log)
        assert any("renewed its key" in w for w in log) and any("rejected" in w for w in log)
    finally:
        admin.put("/admin/api/settings", json={"approve_new_devices": False})


def test_security_alert_reaches_owner_phone(server, admin, tmp_path):
    from ghost_server import security
    security.reset_all()
    security._alerted.clear()
    phone = _agent(server, admin, "Adam Alert Phone", "phone", tmp_path)
    m = _ws_until(server, phone.key, lambda m: m.get("type") == "notify",
                  actions=lambda: httpx.post(server + "/admin/login", json={"password": "bad"}))
    assert m and "wrong" in m["text"].lower() and m["level"] == "security"
    security.reset_all()


def test_settings_log_names_not_values(server, admin):
    from ghost_server import store
    admin.put("/admin/api/settings", json={"anthropic_api_key": "sk-ant-very-secret-9999"})
    e = store.security_events(5)
    assert any("anthropic_api_key" in x["what"] for x in e)
    assert not any("very-secret" in x["what"] for x in e)
    assert "admin_totp_secret" not in admin.get("/admin/api/overview").json()["settings"]
    assert admin.put("/admin/api/settings", json={"admin_totp_secret": "AAAA"}).status_code == 200
    assert store.get("admin_totp_secret") in ("", None)


def test_front_door_bell_names_and_rooms(server, admin, tmp_path, monkeypatch):
    """A doorbell press: Ghost looks, names a known face (or describes the visitor), the owner's phone
    gets it with the picture (through Do Not Disturb), and Home Assistant speakers get it 'near' you."""
    from ghost_server import cameras, door, store
    cam = cameras.Cam("camera.front_door", "Front door", "ha")
    monkeypatch.setattr(cameras, "all_cameras", lambda: [cam])
    monkeypatch.setattr(cameras, "snapshot", lambda c, timeout=15: b"JPEGDATA")
    monkeypatch.setattr(door, "describe_visitor", lambda jpeg, c: "A courier with a parcel.")
    admin.put("/admin/api/settings", json={"door_cameras": "front door", "door_watch": False,
                                           "door_bell_entities": "binary_sensor.front_door_visitor"})
    ha = _agent(server, admin, "Door HA", "homeassistant", tmp_path)
    phone = _agent(server, admin, "Adam Door Phone", "phone", tmp_path)
    got = {"ha": [], "phone": []}

    async def run():
        import websockets
        base = server.replace("http", "ws") + "/api/v1/agent?key="
        async with websockets.connect(base + ha.key) as h, websockets.connect(base + phone.key) as p:
            await h.send(json.dumps({"type": "hello", "capabilities": [], "info": {"rooms": ["Lounge", "Kitchen"]}}))
            await p.send(json.dumps({"type": "hello", "capabilities": []}))
            for ws in (h, p):
                while json.loads(await asyncio.wait_for(ws.recv(), 5))["type"] != "ready":
                    pass
            e = "binary_sensor.front_door_visitor"
            assert await asyncio.to_thread(door.check_bells, {e: "off"}) == []      # first look: just remembers
            out = await asyncio.to_thread(door.check_bells, {e: "on"})
            assert out == ["Someone's at the front door. A courier with a parcel."]
            assert await asyncio.to_thread(door.check_bells, {e: "off"}) == []
            assert await asyncio.to_thread(door.check_bells, {e: "on"}) == []       # pressed twice: one ring
            # a face Visual ID knows
            monkeypatch.setattr(door, "known_faces", lambda jpeg, c: ["Hayley"])
            assert door.ring(cam, source="test") == "Hayley is at the front door."
            for name, ws in (("ha", h), ("phone", p)):
                while len(got[name]) < 2:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if m["type"] == "say":
                        got[name].append(m)
    asyncio.run(run())
    h1, p1 = got["ha"][0], got["phone"][0]
    assert h1["room"] == "@near" and "camera" not in h1 and h1["urgent"] is True
    assert p1["camera"] == "Front door" and p1["picture"].startswith("/files/") and p1["urgent"] is True
    assert got["phone"][1]["text"] == "Hayley is at the front door."
    items = httpx.get(server + "/api/v1/door", headers={"Authorization": f"Bearer {phone.key}"}).json()["items"]
    assert items[0]["summary"] == "Hayley is at the front door." and items[0]["picture"].startswith("/files/")
    # "who was at the door?"
    hist = phone.call_tool("front_door", {})
    assert "courier" in hist and "Hayley" in hist
    admin.put("/admin/api/settings", json={"door_announce": "everywhere"})
    assert door.status()["announce"] == "everywhere"
    admin.put("/admin/api/settings", json={"door_cameras": "", "door_bell_entities": "", "door_announce": "near"})


def test_meal_planner_and_shopping(server, admin, tmp_path, monkeypatch):
    """Plan dinners, set one by voice (ingredients worked out), push to the tablet, shop for the week
    without pantry staples or duplicates."""
    import datetime as dt
    from ghost_server import meals
    from ghost_server.tools import brain

    class FakeClaude:
        def complete(self, history, tools=None, **kw):
            p = history[-1]["content"]
            if "Plan family dinners" in p:
                days = p.split("for: ")[1].split(".")[0].split(", ")
                return json.dumps([{"day": d, "title": f"Chicken stir-fry {i}",
                                    "ingredients": ["500 g chicken thigh", "1 broccoli", "salt", "2 tbsp olive oil"]}
                                   for i, d in enumerate(days)]), []
            if "List the ingredients" in p:
                return '["8 taco shells", "500 g beef mince", "1 lettuce", "salt and pepper"]', []
            return "1. Brown the mince. 2. Fill the shells.", []
    monkeypatch.setattr(brain, "claude", lambda *a, **k: FakeClaude())
    admin.put("/admin/api/settings", json={"meal_pantry": "salt, pepper, olive oil"})
    t = _agent(server, admin, "Meal Tablet", "tablet", tmp_path)
    h = {"Authorization": f"Bearer {t.key}"}

    base = dt.date(2026, 11, 9)                       # a Monday
    assert meals.parse_day("thursday", base) == dt.date(2026, 11, 12)
    assert meals.parse_day("monday", base) == base and meals.parse_day("next monday", base) == dt.date(2026, 11, 16)
    assert meals.parse_day("tomorrow", base) == dt.date(2026, 11, 10)
    assert meals.parse_day("14/11", base) == dt.date(2026, 11, 14)

    # the tablet hears about changes
    m = _ws_until(server, t.key, lambda m: m.get("type") == "meals" and m["days"][0]["title"],
                  actions=lambda: t.call_tool("meals", {"action": "set", "day": "tonight", "dish": "beef tacos"}))
    assert m["days"][0]["title"] == "Beef tacos" or m["days"][0]["title"].lower() == "beef tacos"
    assert "taco shells" in " ".join(m["days"][0]["ingredients"])
    assert "it's beef tacos" in t.call_tool("meals", {"action": "show", "day": "tonight"}).lower()

    r = t.call_tool("meals", {"action": "plan"})
    assert r.startswith("Planned:") and "Chicken stir-fry" in r
    week = httpx.get(server + "/api/v1/meals", headers=h).json()["days"]
    assert len(week) == 7 and all(d["title"] for d in week)

    r = t.call_tool("meals", {"action": "shop", "day": "week"})
    assert "Added" in r and "salt" in r.lower()           # pantry staples are named as skipped
    shop = [i["text"].lower() for i in httpx.get(server + "/api/v1/lists", headers=h).json()["lists"]["shopping"]]
    assert "500 g chicken thigh" in shop and shop.count("500 g chicken thigh") == 1
    assert "salt" not in shop and "2 tbsp olive oil" not in shop and "salt and pepper" not in shop
    assert "8 taco shells" in shop
    assert "Brown the mince" in t.call_tool("meals", {"action": "recipe"})
    assert "Took" in t.call_tool("meals", {"action": "clear", "day": "tonight"})
    assert "Nothing's planned" in t.call_tool("meals", {"action": "show"})


def test_https_certificate_pinning_and_https_only(server, admin, tmp_path):
    """The server makes its own certificate, serves the same API over HTTPS with it, and in
    'HTTPS only' mode refuses the plain-HTTP API (but still hands out the certificate)."""
    import ssl
    import uvicorn
    from ghost_server import tls
    assert tls.ensure() and tls.pem().startswith("-----BEGIN CERTIFICATE")
    info = httpx.get(server + "/api/v1/tls").json()
    assert info["enabled"] and len(info["sha256"]) == 64 and info["pem"] == tls.pem()
    assert httpx.get(server + "/ghost-server.crt").text == tls.pem()

    from ghost_server.app import app
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off",
                                        ssl_certfile=str(tls.cert_path()), ssl_keyfile=str(tls.key_path())))
    threading.Thread(target=srv.run, daemon=True).start()
    ctx = ssl.create_default_context(cadata=tls.pem())         # trust exactly this certificate
    url = f"https://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(url + "/api/v1/health", verify=ctx).status_code == 200:
                break
        except Exception:
            time.sleep(0.05)
    t = _agent(server, admin, "TLS Phone", "phone", tmp_path)
    h = {"Authorization": f"Bearer {t.key}"}
    assert httpx.get(url + "/api/v1/key", headers=h, verify=ctx).status_code == 200
    try:
        httpx.get(url + "/api/v1/health")                       # an ordinary client doesn't trust it
        assert False, "should not be trusted without pinning"
    except httpx.ConnectError:
        pass

    admin.put("/admin/api/settings", json={"https_only": True})
    try:
        r = httpx.get(server + "/api/v1/key", headers=h)
        assert r.status_code == 426 and r.json()["https_port"] == tls.PORT
        assert httpx.get(server + "/api/v1/tls").status_code == 200
        assert httpx.get(server + "/api/v1/health").status_code == 200
        assert httpx.get(server + "/", follow_redirects=False).status_code == 307
        assert httpx.get(url + "/api/v1/key", headers=h, verify=ctx).status_code == 200
        adm = httpx.Client(base_url=url, verify=ctx, headers={"X-Ghost-Admin": "1"})
        assert adm.post("/admin/login", json={"password": "ghost-pass"}).status_code == 200
        assert adm.put("/admin/api/settings", json={"https_only": False}).status_code == 200
    finally:
        from ghost_server import store
        store.put("https_only", False)
        srv.should_exit = True


def test_find_phone_from_another_device(server, admin, tmp_path):
    """'Where's my phone?' from the tablet rings the owner's phone; an offline phone says when it was last seen."""
    phone = _agent(server, admin, "Adam Find Phone", "phone", tmp_path)
    tablet = _agent(server, admin, "Find Tablet", "tablet", tmp_path)
    got = []

    async def run():
        import websockets
        url = server.replace("http", "ws") + "/api/v1/agent?key=" + phone.key
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"type": "hello", "capabilities": [{"name": "ring_phone", "description": "ring"}]}))
            while json.loads(await asyncio.wait_for(ws.recv(), 5))["type"] != "ready":
                pass
            task = asyncio.create_task(asyncio.to_thread(tablet.call_tool, "find_phone", {"whose": "Adam"}))
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if m.get("type") == "call":
                    got.append(m)
                    await ws.send(json.dumps({"type": "result", "id": m["id"], "ok": True,
                                              "result": "Ringing — ringing loudly for 60 seconds. It's at home."}))
                    break
            return await task
    res = asyncio.run(run())
    assert got[0]["tool"] == "ring_phone" and got[0]["args"]["from"] == "Find Tablet"
    assert "It's at home" in res and res.startswith("Adam Find Phone")
    # the phone has gone offline now
    off = tablet.call_tool("find_phone", {"whose": "adam find"})
    assert "isn't connected" in off and "last seen" in off


def test_phone_that_rings_timers_gets_a_native_ring(server, admin, tmp_path):
    """A phone app that says rings_timers gets {"type":"timer","event":"ring"} (alarm + Stop / +1 min),
    not three spoken announcements; Stop from its notification ends it for everyone."""
    p = _agent(server, admin, "Ringing Phone", "phone", tmp_path)
    h = {"Authorization": f"Bearer {p.key}"}
    seen = []

    async def run():
        import websockets
        url = server.replace("http", "ws") + "/api/v1/agent?key=" + p.key
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"type": "hello", "capabilities": [], "info": {"rings_timers": True}}))
            while json.loads(await asyncio.wait_for(ws.recv(), 5))["type"] != "ready":
                pass
            await asyncio.to_thread(httpx.post, server + "/api/v1/timers", headers=h, json={"seconds": 1, "label": "eggs"})
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), 6))
                seen.append(m)
                if m.get("type") == "timer" and m.get("event") == "ring":
                    tid = m["timer"]["id"]
                    await asyncio.to_thread(httpx.post, server + f"/api/v1/timers/{tid}", headers=h, json={"action": "stop"})
                if m.get("type") == "timer" and m.get("event") == "stopped":
                    break
    asyncio.run(run())
    assert any(m.get("type") == "timer" and m.get("event") == "ring" for m in seen)
    assert not any(m.get("type") == "say" and m.get("from") == "timer" for m in seen)


def test_approve_new_device_from_owner_phone(server, admin, tmp_path):
    """The 'asked to join' alert carries the device; the owner's phone can approve or reject it."""
    from ghost_server import security
    security._alerted.clear()
    owner = _agent(server, admin, "Adam Approver Phone", "phone", tmp_path)
    tab = _agent(server, admin, "Approver Tablet", "tablet", tmp_path)
    admin.put("/admin/api/settings", json={"approve_new_devices": True, "approve_from": "Adam Approver"})
    try:
        def join(name):
            code = admin.post("/admin/api/pair-code").json()["code"]
            return httpx.post(server + "/api/v1/pair", json={"code": code, "name": name, "kind": "phone"}).json()
        holder = {}
        m = _ws_until(server, owner.key, lambda m: m.get("type") == "notify" and m.get("action") == "approve_device",
                      actions=lambda: holder.setdefault("p", join("Visitor Phone")))
        p = holder["p"]
        assert m["device_id"] == p["device_id"] and m["device_name"] == "Visitor Phone"
        h = {"Authorization": f"Bearer {owner.key}"}
        # a tablet (not an approver) can't
        r = httpx.post(server + f"/api/v1/devices/{p['device_id']}/approve",
                       headers={"Authorization": f"Bearer {tab.key}"}, json={"approve": True})
        assert r.status_code == 403
        r = httpx.post(server + f"/api/v1/devices/{p['device_id']}/approve", headers=h, json={"approve": True}).json()
        assert r["ok"] and r["approved"]
        assert httpx.get(server + "/api/v1/key", headers={"Authorization": "Bearer " + p["device_key"]}).status_code == 200
        q = join("Stranger Two")
        assert httpx.post(server + f"/api/v1/devices/{q['device_id']}/approve", headers=h, json={"approve": False}).json()["ok"]
        assert httpx.get(server + "/api/v1/key", headers={"Authorization": "Bearer " + q["device_key"]}).status_code == 401
        # can't resurrect a rejected one
        assert httpx.post(server + f"/api/v1/devices/{q['device_id']}/approve", headers=h, json={"approve": True}).status_code == 404
    finally:
        admin.put("/admin/api/settings", json={"approve_new_devices": False, "approve_from": ""})


def test_phone_home_favourites_dim_and_all_off(server, admin, tmp_path, monkeypatch):
    """Phone home controls: pick favourites, dim a light, all lights off — never locks."""
    from ghost_server import store
    from ghost_server.tools import home
    states = {
        "light.kitchen": {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen", "brightness": 128}},
        "light.lounge": {"entity_id": "light.lounge", "state": "on", "attributes": {"friendly_name": "Lounge"}},
        "switch.fridge": {"entity_id": "switch.fridge", "state": "on", "attributes": {"friendly_name": "Fridge"}},
        "lock.front": {"entity_id": "lock.front", "state": "locked", "attributes": {"friendly_name": "Front door"}},
        "scene.movie": {"entity_id": "scene.movie", "state": "scening", "attributes": {"friendly_name": "Movie night"}},
    }
    calls = []

    class R:
        def __init__(self, data, code=200): self._d, self.status_code = data, code
        def json(self): return self._d

    def fake_req(method, path, **kw):
        if path == "/api/states":
            return R(list(states.values()))
        if path.startswith("/api/states/"):
            return R(states[path.split("/")[-1]])
        calls.append((path, kw.get("json")))
        return R({}, 200)
    monkeypatch.setattr(home, "_req", fake_req)
    store.put("ha_token", "x"); store.put("ha_url", "http://ha")
    try:
        t = _agent(server, admin, "Home Phone", "phone", tmp_path)
        h = {"Authorization": f"Bearer {t.key}"}
        ents = [e["entity_id"] for e in httpx.get(server + "/api/v1/home/entities", headers=h).json()["entities"]]
        assert "light.kitchen" in ents and "scene.movie" in ents and "lock.front" not in ents
        tiles = httpx.get(server + "/api/v1/home/tiles?ids=scene.movie,light.kitchen", headers=h).json()["tiles"]
        assert [x["entity_id"] for x in tiles] == ["scene.movie", "light.kitchen"]
        assert tiles[1]["brightness"] == 50 and tiles[1]["dimmable"]
        assert httpx.post(server + "/api/v1/home/switch", headers=h,
                          json={"entity_id": "light.kitchen", "action": "dim:30"}).json()["ok"]
        assert calls[-1] == ("/api/services/light/turn_on", {"entity_id": "light.kitchen", "brightness_pct": 30})
        r = httpx.post(server + "/api/v1/home/lights-off", headers=h).json()
        assert r["ok"] and r["turned_off"] == 2
        assert calls[-1] == ("/api/services/light/turn_off", {"entity_id": ["light.kitchen", "light.lounge"]})
        bad = httpx.post(server + "/api/v1/home/switch", headers=h, json={"entity_id": "lock.front", "action": "off"}).json()
        assert bad["ok"] is False
    finally:
        store.put("ha_token", ""); store.put("ha_url", "")


def test_admin_auto_lock(server, admin, monkeypatch):
    """The admin session ends after the auto-lock time without use; the page's own background
    refresh (X-Ghost-Passive) doesn't keep it alive, but real use does."""
    import time as _t
    from ghost_server import security, store
    assert admin.get("/admin/state").json()["lock_minutes"] == 15
    store.put("admin_lock_minutes", 1)
    try:
        c = httpx.Client(base_url=server, headers={"X-Ghost-Admin": "1"})
        assert c.post("/admin/login", json={"password": "ghost-pass"}).status_code == 200
        real = _t.time
        start = real()
        monkeypatch.setattr(security.time, "time", lambda: start + 40)
        assert c.get("/admin/api/overview", headers={"X-Ghost-Passive": "1"}).status_code == 200   # passive: no touch
        monkeypatch.setattr(security.time, "time", lambda: start + 70)
        assert c.get("/admin/api/overview").status_code == 401                                     # locked
        monkeypatch.setattr(security.time, "time", real)
        # real use keeps it going
        c2 = httpx.Client(base_url=server, headers={"X-Ghost-Admin": "1"})
        assert c2.post("/admin/login", json={"password": "ghost-pass"}).status_code == 200
        start = real()
        monkeypatch.setattr(security.time, "time", lambda: start + 40)
        assert c2.get("/admin/api/overview").status_code == 200
        monkeypatch.setattr(security.time, "time", lambda: start + 90)
        assert c2.get("/admin/api/overview").status_code == 200
    finally:
        monkeypatch.setattr(security.time, "time", _t.time)
        store.put("admin_lock_minutes", 15)

