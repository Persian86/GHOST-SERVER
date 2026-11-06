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
    c = httpx.Client(base_url=server)
    assert c.get("/admin/state").json()["needs_setup"] is True
    assert c.post("/admin/login", json={"password": "abc"}).status_code == 400      # too short
    assert c.post("/admin/login", json={"password": "ghost-pass"}).status_code == 200
    assert httpx.post(server + "/admin/login", json={"password": "wrong"}).status_code == 401
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
    assert "plumber" in b.session()["system_instruction"]


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
                                      "info": {"rooms": ["Office", "Bedroom", "Lounge"]}}))
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
    asyncio.run(run())
    assert got[0]["room"] == "Bedroom" and got[0]["text"] == "Bedtime!"


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
    assert [(p["name"], p["samples"]) for p in people] == [("Adam", 1)]
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
