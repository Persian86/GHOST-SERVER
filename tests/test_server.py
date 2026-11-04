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
    import threading
    from ghost_server import cameras
    # a stand-in "camera": a test video served over http, read by ffmpeg like an RTSP feed
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory="/tmp/camtest")
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
