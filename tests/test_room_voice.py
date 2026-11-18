import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ghost_server import room_voice as voice


def test_resampler_is_invariant_to_cloud_packet_boundaries():
    source = (np.sin(np.arange(24001) * 0.04) * 20000).astype("<i2").tobytes()
    expected = voice.PCM16k().feed(source)
    for packet_samples in (1, 17, 320, 1024):
        resampler = voice.PCM16k()
        result = b"".join(resampler.feed(source[i:i+packet_samples*2])
                          for i in range(0, len(source), packet_samples*2))
        assert result == expected
    assert len(expected) == 16000 * 2


@pytest.mark.parametrize("data,total,elapsed", [(b"a",0,1), (b"a"*4098,0,1),
    (b"",0,1), (b"aa",640000,30), (b"a"*4096,8192,0)])
def test_invalid_and_unbounded_microphone_frames_are_rejected(data,total,elapsed):
    with pytest.raises(ValueError): voice.check_pcm(data,total,elapsed)


@pytest.mark.parametrize("row", [None, {"id":"room","revoked":1,"approved":1},
    {"id":"room","revoked":0,"approved":0}])
def test_unpaired_revoked_and_unapproved_cannot_open_cloud_session(row):
    with pytest.raises(PermissionError): voice.policy(row)


def setup(monkeypatch):
    row = {"id":"room", "name":"Kitchen Ghost", "kind":"room", "revoked":0, "approved":1}
    settings = {"gemini_api_key":"private-cloud-secret", "gemini_live_model":"test-model", "gemini_voice":"Fenrir"}
    monkeypatch.setattr(voice.store,"get",lambda key:settings.get(key))
    monkeypatch.setattr(voice.store,"device",lambda did:row)
    monkeypatch.setattr(voice.access,"role",lambda did:"adult")
    monkeypatch.setattr(voice.access,"check_route",lambda *args:None)
    monkeypatch.setattr(voice.tools,"declarations",lambda *args:[])
    monkeypatch.setattr(voice.prompt,"build",lambda row:"Ghost in the kitchen")
    return row, settings


class Socket:
    def __init__(self):
        self.messages=[];self.queue=asyncio.Queue();self.closed=[]
    async def send_json(self, message): self.messages.append(message)
    async def send_bytes(self, data): self.messages.append(data)
    async def close(self, **kwargs): self.closed.append(kwargs)
    async def receive(self): return await self.queue.get()


class Cloud:
    def __init__(self, socket):
        self.socket=socket;self.end=asyncio.Event();self.closed=False;self.input=[];self.config=None
        self.aio=NS(live=NS(connect=self.connect),aclose=self.close)
    async def close(self): self.closed=True
    def connect(self, **kwargs): self.config=kwargs;return self
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    async def send_realtime_input(self, **kwargs):
        self.input.append(kwargs)
        if "activity_start" in kwargs:
            await self.socket.queue.put({"type":"websocket.receive","bytes":bytes(640)})
            await self.socket.queue.put({"type":"websocket.receive","text":'{"type":"stop"}'})
        if "activity_end" in kwargs: self.end.set()
    async def receive(self):
        await self.end.wait()
        yield NS(data=bytes(960),tool_call=None,server_content=None)
        yield NS(data=None,tool_call=None,server_content=NS(input_transcription=NS(text="Hello"),
            output_transcription=NS(text="Hi there"),turn_complete=True))


def test_real_protocol_order_pcm_and_credentials_stay_server_side(monkeypatch):
    row, settings=setup(monkeypatch);turns=[]
    monkeypatch.setattr(voice.store,"add_turn",lambda *args:turns.append(args))
    async def run():
        ws=Socket();cloud=Cloud(ws);credentials=[]
        def factory(**kwargs): credentials.append(kwargs);return cloud
        await voice.serve(ws,row,lambda row:None,client_factory=factory)
        assert [m["type"] for m in ws.messages if isinstance(m,dict)] == ["ready","thinking","speaking","done"]
        assert any(isinstance(m,bytes) and len(m)>0 for m in ws.messages)
        assert "private-cloud-secret" not in repr(ws.messages)
        assert credentials[0]["api_key"] == settings["gemini_api_key"]
        assert cloud.closed and row["id"] not in voice._active
        assert cloud.config["config"].realtime_input_config.automatic_activity_detection.disabled
    asyncio.run(run())
    assert turns == [("room","user","Hello"),("room","assistant","Hi there")]


def test_disconnect_cancels_cloud_session_and_releases_slot(monkeypatch):
    row,_=setup(monkeypatch)
    async def run():
        ws=Socket();cloud=Cloud(ws)
        async def start(**kwargs):
            await ws.queue.put({"type":"websocket.disconnect"})
        cloud.send_realtime_input=start
        await voice.serve(ws,row,lambda row:None,client_factory=lambda **kwargs:cloud)
        assert cloud.closed and "room" not in voice._active
    asyncio.run(run())


def test_provider_exception_is_redacted_and_slot_released(monkeypatch):
    row,_=setup(monkeypatch)
    async def run():
        ws=Socket()
        def factory(**kwargs): raise RuntimeError("private-cloud-secret")
        await voice.serve(ws,row,lambda row:None,client_factory=factory)
        assert "private-cloud-secret" not in repr(ws.messages)
        assert ws.messages[-1]["type"]=="error" and "room" not in voice._active
    asyncio.run(run())


def test_duplicate_session_does_not_contact_cloud(monkeypatch):
    row,_=setup(monkeypatch)
    async def run():
        ws=Socket();voice._active.add("room")
        try:
            await voice.serve(ws,row,lambda row:None,client_factory=lambda **kwargs:pytest.fail("cloud opened"))
            assert ws.closed[0]["code"]==4429
        finally: voice._active.discard("room")
    asyncio.run(run())


def test_revocation_during_reply_closes_before_audio_or_transcript(monkeypatch):
    row,_=setup(monkeypatch)
    monkeypatch.setattr(voice.store,"add_turn",lambda *args:pytest.fail("revoked transcript saved"))
    async def run():
        ws=Socket();cloud=Cloud(ws)
        async def receive():
            await cloud.end.wait()
            row["revoked"]=1
            yield NS(data=bytes(960),tool_call=None,server_content=None)
        cloud.receive=receive
        await voice.serve(ws,row,lambda row:None,client_factory=lambda **kwargs:cloud)
        assert not any(isinstance(m,bytes) for m in ws.messages)
        assert any(c.get("code")==4403 for c in ws.closed)
        assert cloud.closed and "room" not in voice._active
    asyncio.run(run())


def test_model_cannot_invoke_a_tool_not_offered_to_this_device(monkeypatch):
    row,_=setup(monkeypatch)
    monkeypatch.setattr(voice.tools,"run",lambda *args:pytest.fail("unoffered tool executed"))
    async def run():
        ws=Socket();cloud=Cloud(ws)
        async def receive():
            await cloud.end.wait()
            yield NS(data=None,tool_call=NS(function_calls=[NS(id="1",name="delete_private_files",args={})]),server_content=None)
        cloud.receive=receive
        await voice.serve(ws,row,lambda row:None,client_factory=lambda **kwargs:cloud)
        assert any(c.get("code")==4403 for c in ws.closed)
        assert cloud.closed and "room" not in voice._active
    asyncio.run(run())


def test_timeout_closes_cloud_and_releases_capacity(monkeypatch):
    row,_=setup(monkeypatch)
    monkeypatch.setattr(voice,"MAX_SESSION_SECONDS",0.02)
    async def run():
        ws=Socket();cloud=Cloud(ws)
        async def send(**kwargs): pass
        cloud.send_realtime_input=send
        await voice.serve(ws,row,lambda row:None,client_factory=lambda **kwargs:cloud)
        assert ws.messages[-1]["type"]=="error"
        assert cloud.closed and "room" not in voice._active
    asyncio.run(run())


def test_websocket_does_not_accept_a_device_key_in_the_url(monkeypatch):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from ghost_server.app import app
    _,_=setup(monkeypatch)
    seen=[]
    monkeypatch.setattr(voice.store,"device_by_key",lambda key:seen.append(key))
    with pytest.raises(WebSocketDisconnect) as err:
        with TestClient(app).websocket_connect("/api/v1/room-voice?key=private-device-key") as ws:
            ws.receive_json()
    assert seen==[""] and err.value.code==4401
