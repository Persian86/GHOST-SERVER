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
