"""Ghost Unplugged — talking with no cloud at all.

When "unplugged" is on, nothing goes to Claude or Gemini:

    thinking   the local model on the AI PC (Ollama), with Ghost's tools   (local_llm.py, mode "only")
    hearing    Whisper on the AI PC                                         (Wyoming speech-to-text)
    speaking   Piper on the AI PC                                           (Wyoming text-to-speech)

Whisper and Piper are the same small services Home Assistant's own voice uses
(wyoming-faster-whisper on port 10300, wyoming-piper on 10200), so Home Assistant
Assist can share them. Ghost talks the Wyoming protocol to them directly.

Room screens use the room-voice connection unchanged: the device still streams
16 kHz audio and plays 16 kHz audio back — only what's behind it changes.
"""
from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import urlparse

import numpy as np

from . import store

STT_PORT = 10300
TTS_PORT = 10200


# ── settings ─────────────────────────────────────────────────────────────────
def on() -> bool:
    return bool(store.get("unplugged"))


def _host_from_local_llm() -> str:
    try:
        return urlparse(str(store.get("local_llm_url") or "")).hostname or ""
    except Exception:  # noqa: BLE001
        return ""


def _endpoint(key: str, port: int) -> tuple[str, int]:
    """'tcp://host:port' from the settings, else the local model's host with the usual port."""
    raw = str(store.get(key) or "").strip()
    if raw:
        u = urlparse(raw if "://" in raw else "tcp://" + raw)
        return u.hostname or "", u.port or port
    return _host_from_local_llm(), port


def stt_endpoint() -> tuple[str, int]:
    return _endpoint("wyoming_stt", STT_PORT)


def tts_endpoint() -> tuple[str, int]:
    return _endpoint("wyoming_tts", TTS_PORT)


def status() -> dict:
    return {"unplugged": on(), "stt": "%s:%s" % stt_endpoint(), "tts": "%s:%s" % tts_endpoint(),
            "voice": str(store.get("piper_voice") or "")}


# ── Wyoming: one JSON header line, optional JSON data, optional audio payload ─
def encode(kind: str, data: dict | None = None, payload: bytes = b"") -> bytes:
    body = json.dumps(data or {}).encode()
    head = {"type": kind, "data_length": len(body)}
    if payload:
        head["payload_length"] = len(payload)
    return json.dumps(head).encode() + b"\n" + body + payload


async def read_event(reader: asyncio.StreamReader) -> tuple[str, dict, bytes]:
    line = await reader.readline()
    if not line:
        raise ConnectionError("speech service closed the connection")
    head = json.loads(line)
    data = dict(head.get("data") or {})
    if head.get("data_length"):
        data.update(json.loads(await reader.readexactly(int(head["data_length"]))))
    payload = await reader.readexactly(int(head["payload_length"])) if head.get("payload_length") else b""
    return str(head.get("type", "")), data, payload


async def _connect(endpoint: tuple[str, int], timeout: float):
    host, port = endpoint
    if not host:
        raise RuntimeError("the speech services aren't set up (no AI PC address)")
    return await asyncio.wait_for(asyncio.open_connection(host, port), timeout)


async def transcribe(pcm16k: bytes, language: str = "en", timeout: float = 30) -> str:
    """16 kHz mono 16-bit audio → text (Whisper)."""
    reader, writer = await _connect(stt_endpoint(), 5)
    try:
        fmt = {"rate": 16000, "width": 2, "channels": 1}
        writer.write(encode("transcribe", {"language": language}))
        writer.write(encode("audio-start", fmt))
        for i in range(0, len(pcm16k), 8192):
            writer.write(encode("audio-chunk", fmt, pcm16k[i:i + 8192]))
        writer.write(encode("audio-stop"))
        await writer.drain()
        async with asyncio.timeout(timeout):
            while True:
                kind, data, _ = await read_event(reader)
                if kind == "transcript":
                    return str(data.get("text") or "").strip()
    finally:
        writer.close()


def resample(pcm: bytes, src: int, dst: int = 16000) -> bytes:
    if src == dst or not pcm:
        return pcm
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float64)
    n = int(len(x) * dst / src)
    y = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return np.clip(y, -32768, 32767).astype("<i2").tobytes()


async def speak(text: str, timeout: float = 60):
    """Text → 16 kHz mono 16-bit audio chunks (Piper), as they arrive."""
    reader, writer = await _connect(tts_endpoint(), 5)
    try:
        data = {"text": text}
        voice = str(store.get("piper_voice") or "").strip()
        if voice:
            data["voice"] = {"name": voice}
        writer.write(encode("synthesize", data))
        await writer.drain()
        rate = 22050
        async with asyncio.timeout(timeout):
            while True:
                kind, info, payload = await read_event(reader)
                if kind == "audio-start":
                    rate = int(info.get("rate") or rate)
                elif kind == "audio-chunk" and payload:
                    rate = int(info.get("rate") or rate)
                    if int(info.get("channels") or 1) != 1 or int(info.get("width") or 2) != 2:
                        raise ValueError("unexpected speech format")
                    yield resample(payload, rate)
                elif kind == "audio-stop":
                    return
    finally:
        writer.close()


async def probe() -> dict:
    """Admin page check: can Ghost hear and speak without the cloud?"""
    out = {}
    for name, ep in (("stt", stt_endpoint()), ("tts", tts_endpoint())):
        try:
            r, w = await _connect(ep, 4)
            w.write(encode("describe"))
            await w.drain()
            kind, _, _ = await asyncio.wait_for(read_event(r), 4)
            w.close()
            out[name] = "ok" if kind == "info" else f"answered {kind}"
        except Exception as e:  # noqa: BLE001
            out[name] = f"not reachable at {ep[0]}:{ep[1]} ({type(e).__name__})"
    return out


# ── the room-voice conversation, unplugged ───────────────────────────────────
async def serve(ws, row, current, event, send_lock, *, continuous=False, limits=None):
    """Same protocol as room_voice.serve (ready → audio → stop → thinking → speaking → done),
    with Whisper → the local brain (with tools) → Piper behind it."""
    from . import chat
    from .room_voice import check_pcm, conversation_over
    lim = limits or {}
    max_input = lim.get("max_input", 20)
    max_turns = lim.get("max_turns", 12)
    between = lim.get("between", 70)
    turns = 0
    while True:
        await event("ready", input_rate=16000, output_rate=16000, max_seconds=max_input,
                    **({"continuous": True} if continuous else {}))
        began, total, audio = time.monotonic(), 0, bytearray()
        while True:
            message = await asyncio.wait_for(ws.receive(), max_input + 5)
            current()
            if message["type"] == "websocket.disconnect":
                from fastapi import WebSocketDisconnect
                raise WebSocketDisconnect()
            if message.get("bytes") is not None:
                total = check_pcm(message["bytes"], total, time.monotonic() - began)
                audio += message["bytes"]
                continue
            raw = message.get("text") or ""
            if len(raw) > 128:
                raise ValueError("control frame too large")
            command = json.loads(raw)
            if command.get("type") == "cancel":
                return
            if command.get("type") != "stop":
                raise ValueError("unexpected control frame")
            if not total:
                raise ValueError("empty recording")
            break
        turns += 1
        await event("thinking")
        heard = await transcribe(bytes(audio))
        current()
        if heard:
            device = dict(row)
            result = await asyncio.wait_for(asyncio.to_thread(chat.reply, device, heard, True), 120)
            answer = str(result.get("text") or "").strip()
        else:
            answer = ""
        current()
        if answer:
            speaking = False
            async for chunk in speak(answer):
                if not speaking:
                    await event("speaking")
                    speaking = True
                for offset in range(0, len(chunk), 2048):
                    async with send_lock:
                        await asyncio.wait_for(ws.send_bytes(chunk[offset:offset + 2048]), 5)
        if not continuous or turns >= max_turns or conversation_over(heard):
            await event("done", **({"follow_up": False} if continuous else {}))
            return
        await event("done", follow_up=True)
        message = await asyncio.wait_for(ws.receive(), between)
        current()
        if message["type"] == "websocket.disconnect":
            from fastapi import WebSocketDisconnect
            raise WebSocketDisconnect()
        raw = message.get("text") or ""
        if len(raw) > 128 or json.loads(raw or "{}").get("type") != "start":
            raise ValueError("unexpected control frame")
