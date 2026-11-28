import asyncio
import json

import numpy as np
import pytest

from ghost_server import local_voice, store
from ghost_server.tools import Ctx, TOOLS, load


async def wyoming_server(handler):
    async def on_conn(reader, writer):
        try:
            await handler(reader, writer)
        finally:
            writer.close()
    srv = await asyncio.start_server(on_conn, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


@pytest.fixture
def env(tmp_path):
    store.init(tmp_path / "u.db")


def test_unplugged_tool_and_brain_mode(env, monkeypatch):
    load()
    store.put("local_llm_url", "http://10.0.0.5:11434")
    assert "unplugged now" in TOOLS["unplugged_mode"].handler({"action": "on"}, Ctx())
    assert store.get("unplugged") is True
    seen = {}
    import ghost_server.local_llm as ll
    monkeypatch.setattr(ll, "ResilientLLM", lambda primary, local, mode: seen.update(mode=mode, local=local) or "llm")
    from ghost_server.tools.brain import claude
    store.put("local_llm_mode", "off")          # even "off" can't bring the cloud back while unplugged
    claude()
    assert seen["mode"] == "only" and seen["local"] is not None
    from ghost_server import live
    c = live.credentials()
    assert c["unplugged"] and "token" not in c and "api_key" not in c
    assert local_voice.stt_endpoint() == ("10.0.0.5", 10300) and local_voice.tts_endpoint() == ("10.0.0.5", 10200)
    store.put("wyoming_tts", "tts.local:10555")
    assert local_voice.tts_endpoint() == ("tts.local", 10555)
    assert "Plugged back in" in TOOLS["unplugged_mode"].handler({"action": "off"}, Ctx())


def test_wyoming_transcribe_and_speak(env):
    async def run():
        got = {}

        async def stt(reader, writer):
            audio = b""
            while True:
                kind, data, payload = await local_voice.read_event(reader)
                if kind == "transcribe":
                    got["lang"] = data["language"]
                if kind == "audio-chunk":
                    audio += payload
                if kind == "audio-stop":
                    break
            got["bytes"] = len(audio)
            writer.write(local_voice.encode("transcript", {"text": " turn on the lights "}))
            await writer.drain()

        async def tts(reader, writer):
            kind, data, _ = await local_voice.read_event(reader)
            got["say"] = data["text"]
            fmt = {"rate": 22050, "width": 2, "channels": 1}
            writer.write(local_voice.encode("audio-start", fmt))
            writer.write(local_voice.encode("audio-chunk", fmt, np.zeros(22050, "<i2").tobytes()))
            writer.write(local_voice.encode("audio-stop"))
            await writer.drain()

        s1, p1 = await wyoming_server(stt)
        s2, p2 = await wyoming_server(tts)
        store.put("wyoming_stt", f"127.0.0.1:{p1}")
        store.put("wyoming_tts", f"127.0.0.1:{p2}")
        text = await local_voice.transcribe(bytes(32000))
        chunks = [c async for c in local_voice.speak("Done")]
        s1.close(); s2.close()
        return text, chunks, got

    text, chunks, got = asyncio.run(run())
    assert text == "turn on the lights" and got["bytes"] == 32000 and got["lang"] == "en"
    assert got["say"] == "Done" and sum(map(len, chunks)) == 32000      # 1 s of 22.05 kHz → 1 s of 16 kHz


class Socket:
    def __init__(self, incoming):
        self.out = []
        self.incoming = list(incoming)

    async def send_bytes(self, b):
        self.out.append(b)

    async def receive(self):
        return self.incoming.pop(0)


def test_room_voice_unplugged_round_trip(env, monkeypatch):
    events = []

    async def event(kind, **extra):
        events.append(kind)

    async def fake_transcribe(pcm, language="en", timeout=30):
        return "what's the time"

    async def fake_speak(text, timeout=60):
        assert text == "It's 10 o'clock."
        yield bytes(5000)

    from ghost_server import chat
    monkeypatch.setattr(local_voice, "transcribe", fake_transcribe)
    monkeypatch.setattr(local_voice, "speak", fake_speak)
    monkeypatch.setattr(chat, "reply", lambda device, text, voice=False: {"text": "It's 10 o'clock."})
    ws = Socket([{"type": "websocket.receive", "bytes": bytes(640)},
                 {"type": "websocket.receive", "text": json.dumps({"type": "stop"})}])
    row = {"id": "room", "name": "Kitchen"}
    asyncio.run(local_voice.serve(ws, row, lambda: row, event, asyncio.Lock()))
    assert events == ["ready", "thinking", "speaking", "done"]
    assert sum(map(len, ws.out)) == 5000 and all(len(b) <= 2048 for b in ws.out)


def test_modes_snapshot_and_broadcast(env, monkeypatch):
    from ghost_server import guard, modes
    from ghost_server.hub import hub
    sent = []
    monkeypatch.setattr(hub, "conns", {"phone": object(), "kid": object()})
    monkeypatch.setattr(hub, "send_sync", lambda did, msg: sent.append((did, msg)))
    from ghost_server import access
    monkeypatch.setattr(access, "limited", lambda did: did == "kid")
    store.put("unplugged", True)
    guard._status["on"] = False
    assert modes.snapshot() == {"type": "modes", "guard": False, "unplugged": True}
    modes.broadcast()
    assert sent == [("phone", {"type": "modes", "guard": False, "unplugged": True})]


def test_cloud_is_blocked_and_counted_while_unplugged(env):
    from ghost_server import cloudlog
    from ghost_server.claude import AnthropicLLM
    store.put("unplugged", False)
    cloudlog.reset()
    cloudlog.cloud("claude", "complete")
    assert cloudlog.status()["last_answer_by"] == "claude"
    store.put("unplugged", True)
    cloudlog.reset()
    llm = AnthropicLLM({"api_key": "sk-test", "model": "claude-x"})
    with pytest.raises(cloudlog.Unplugged):
        llm.complete([{"role": "user", "content": "hi"}])
    cloudlog.local("qwen3.5")
    s = cloudlog.status()
    assert s["cloud_calls"] == 0 and s["blocked"] == 1 and s["local_answers"] == 1 and s["last_answer_by"] == "local"
    assert "1 answer from the local AI, 0 from the cloud" in cloudlog.summary() and "1 cloud request was blocked" in cloudlog.summary()
    load()
    assert "Unplugged:" in TOOLS["unplugged_mode"].handler({"action": "status"}, Ctx())


def test_family_map_is_google_and_key_is_sanitised(env):
    from ghost_server import tracking
    page = tracking.page("tok123")
    assert "maps.google.com/maps?q=" in page and "openstreetmap" not in page and 'GKEY = ""' in page
    store.put("google_maps_key", 'AIza-abc_123"</script><x>')
    page = tracking.page("tok123")
    assert 'GKEY = "AIza-abc_123scriptx"' in page and "</script><x>" not in page
    assert "maps.googleapis.com/maps/api/js" in page


def test_fire_radius_default_is_5_km(env):
    from ghost_server import firewatch
    assert firewatch.radius() == 5.0
