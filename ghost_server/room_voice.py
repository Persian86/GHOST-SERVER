"""Bounded PCM voice conversations for small room devices.

Cloud credentials remain on the server. Device approval and household tool
ceilings are the same as the existing phone/desktop session API.
"""
from __future__ import annotations

import asyncio
import json
import time

import numpy as np
from fastapi import WebSocketDisconnect

from . import access, prompt, store, tools

MAX_INPUT_SECONDS = 20
MAX_SESSION_SECONDS = 100
MAX_CONVERSATION_SECONDS = 300
MAX_CONVERSATION_TURNS = 12
# A long answer ("count to 100") streams for well over a minute. The device
# sends nothing while it plays, so the reply phase must not use the 70 s
# between-turns wait, and the spoken-reply cap must allow long answers.
REPLY_WAIT_SECONDS = 240
MAX_REPLY_AUDIO_SECONDS = 180
BETWEEN_TURNS_WAIT_SECONDS = 70

# Saying goodbye ends the conversation: after "thanks", "that's all", "bye",
# "goodnight" and friends (or a turn where nothing was actually said) the room
# screen stops listening. A short reply made only of these words counts, so
# "ok thanks Ghost, that's all" and "nah I'm good mate" end it, while anything
# with a real request in it ("thanks, now turn off the lights") carries on.
_CLOSING = {                      # at least one of these must be said
    "thanks", "thank", "thankyou", "ta", "cheers", "bye", "byebye", "goodbye", "goodnight",
    "night", "nite", "later", "laters", "all", "it", "nothing", "no", "nope", "nah",
    "stop", "cancel", "nevermind", "mind", "done", "finished", "enough",
}
_FILLER = {                       # may surround them
    "ok", "okay", "alright", "right", "cool", "great", "awesome", "perfect", "lovely",
    "brilliant", "good", "fine", "sweet", "yeah", "yep", "yes", "so", "and", "just",
    "you", "very", "much", "heaps", "mate", "ghost", "hey", "please", "for", "now",
    "thats", "that", "is", "ill", "be", "will", "thatll", "im", "i", "am", "were", "we",
    "else", "see", "ya", "you", "soon", "never", "have", "a", "nice", "night", "sleep",
    "well", "tonight", "today", "really", "appreciate", "it", "the", "help", "go",
}


def conversation_over(heard: str) -> bool:
    """True when the user's last turn means the conversation should end:
    nothing was transcribed (room noise, not speech) or it was a goodbye."""
    words = "".join(c for c in (heard or "").lower().replace("'", "")
                    if c.isalnum() or c.isspace()).split()
    if not words:
        return True
    if len(words) > 10:
        return False
    return (all(w in _CLOSING or w in _FILLER for w in words)
            and any(w in _CLOSING for w in words))
_active: set[str] = set()


class PCM16k:
    """Streaming 24 kHz -> 16 kHz linear resampler, invariant to packet cuts."""
    def __init__(self):
        self.tail = np.empty(0, dtype=np.float64)
        self.position = 0.0

    def feed(self, pcm: bytes) -> bytes:
        if len(pcm) % 2:
            raise ValueError("unaligned PCM")
        samples = np.concatenate((self.tail, np.frombuffer(pcm, dtype="<i2").astype(np.float64)))
        points = np.arange(self.position, max(0, len(samples) - 1), 1.5)
        out = np.interp(points, np.arange(len(samples)), samples) if len(points) else np.empty(0)
        self.position = (float(points[-1]) + 1.5 if len(points) else self.position)
        drop = max(0, len(samples) - 1)
        self.tail = samples[drop:]
        self.position -= drop
        return np.rint(out).clip(-32768, 32767).astype("<i2").tobytes()


def check_pcm(data: bytes, total: int, elapsed: float) -> int:
    if not data or len(data) > 4096 or len(data) % 2:
        raise ValueError("invalid audio frame")
    total += len(data)
    if total > 32000 * MAX_INPUT_SECONDS or total > 8192 + max(0, elapsed) * 40000:
        raise ValueError("audio limit exceeded")
    return total


def policy(row):
    """A credential, not a claimed room or voice identity, selects permissions."""
    if row is None or row["revoked"] or not row["approved"]:
        raise PermissionError("device is no longer approved")
    access.check_route(row["id"], "GET", "/api/v1/session")


async def serve(ws, row, reauthorize, *, client_factory=None, continuous=False):
    """An explicit voice session, optionally retaining context for bounded follow-ups."""
    did = row["id"]
    if did in _active or len(_active) >= 4:
        await ws.close(code=4429, reason="voice session busy")
        return
    _active.add(did)
    client = None
    tasks = []
    send_lock = asyncio.Lock()

    async def event(kind, **extra):
        async with send_lock:
            await asyncio.wait_for(ws.send_json({"type": kind, **extra}), 5)

    def current():
        fresh = store.device(did)
        policy(fresh)
        if access.role(did) != original_role or bool(store.get("household_access")) != original_access:
            raise PermissionError("access changed")
        reauthorize(fresh)
        return fresh

    try:
        policy(row)
        original_role = access.role(did)
        original_access = bool(store.get("household_access"))
        key = str(store.get("gemini_api_key") or "").strip()
        model = str(store.get("gemini_live_model") or "").strip()
        if not key or not model:
            await event("error", message="Voice is not configured on Ghost Server.")
            return
        from google import genai
        from google.genai import types
        factory = client_factory or genai.Client
        client = factory(api_key=key, http_options={"api_version": "v1alpha", "timeout": 20000})
        declarations = [t for t in tools.declarations("live") if access.tool_allowed(did, t["name"])]
        cfg = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=prompt.build(dict(row)),
            tools=[{"function_declarations": declarations}] if declarations else [],
            speech_config={"voice_config": {"prebuilt_voice_config": {
                "voice_name": store.get("gemini_voice") or "Fenrir"}}},
            input_audio_transcription={}, output_audio_transcription={},
            realtime_input_config={"automatic_activity_detection": {"disabled": True}},
        )
        async with asyncio.timeout(MAX_CONVERSATION_SECONDS if continuous else MAX_SESSION_SECONDS):
            async with client.aio.live.connect(model=model, config=cfg) as session:
                await session.send_realtime_input(activity_start=types.ActivityStart())
                async def ready():
                    extra = {"continuous": True} if continuous else {}
                    await event("ready", input_rate=16000, output_rate=16000,
                                max_seconds=MAX_INPUT_SECONDS, **extra)

                await ready()
                phase = "recording"
                began = time.monotonic()
                waiting_since = began
                turns = 0
                resampler = PCM16k()
                heard, said = [], []

                async def input_audio():
                    total = 0
                    nonlocal phase, began, turns
                    while True:
                        wait = (MAX_INPUT_SECONDS + 5 if phase == "recording"
                                else REPLY_WAIT_SECONDS if phase == "reply"
                                else BETWEEN_TURNS_WAIT_SECONDS)
                        message = await asyncio.wait_for(ws.receive(), wait)
                        current()
                        if message["type"] == "websocket.disconnect":
                            raise WebSocketDisconnect()
                        if message.get("bytes") is not None:
                            if phase != "recording":
                                raise ValueError("recording already ended")
                            data = message["bytes"]
                            total = check_pcm(data, total, time.monotonic() - began)
                            await session.send_realtime_input(audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000"))
                        else:
                            raw = message.get("text") or ""
                            if len(raw) > 128:
                                raise ValueError("control frame too large")
                            command = json.loads(raw)
                            if command.get("type") == "stop" and phase == "recording":
                                if not total:
                                    raise ValueError("empty recording")
                                phase = "reply"
                                turns += 1
                                await event("thinking")
                                await session.send_realtime_input(activity_end=types.ActivityEnd())
                                # Keep detecting disconnect/cancel while the reply is generated.
                            elif command.get("type") == "cancel":
                                return
                            elif continuous and command.get("type") == "start" and phase == "await_start":
                                phase = "recording"
                                began = time.monotonic()
                                total = 0
                                await session.send_realtime_input(activity_start=types.ActivityStart())
                                await ready()
                            else:
                                raise ValueError("unexpected control frame")

                async def output_audio():
                    nonlocal phase, waiting_since, resampler
                    speaking = False
                    output_bytes = 0
                    while True:
                        async for msg in session.receive():
                            current()
                            sc = msg.server_content
                            if sc and sc.input_transcription and sc.input_transcription.text:
                                heard.append(sc.input_transcription.text)
                            if sc and sc.output_transcription and sc.output_transcription.text:
                                said.append(sc.output_transcription.text)
                            if sum(map(len, heard)) + sum(map(len, said)) > 16000:
                                raise ValueError("transcript limit exceeded")
                            if msg.tool_call:
                                if phase != "reply":
                                    raise ValueError("premature tool call")
                                if len(msg.tool_call.function_calls) > 8:
                                    raise ValueError("tool batch too large")
                                responses = []
                                for fc in msg.tool_call.function_calls:
                                    current()
                                    if fc.name not in {t["name"] for t in declarations}:
                                        raise PermissionError("tool was not offered to this device")
                                    if len(json.dumps(dict(fc.args or {}))) > 16384:
                                        raise ValueError("tool arguments too large")
                                    ctx = tools.Ctx(did, row["name"], "live")
                                    result = await asyncio.wait_for(asyncio.to_thread(
                                        tools.run, fc.name, dict(fc.args or {}), ctx), 40)
                                    current()
                                    responses.append(types.FunctionResponse(id=fc.id, name=fc.name,
                                        response={"result": result}))
                                await session.send_tool_response(function_responses=responses)
                            if msg.data:
                                if phase != "reply":
                                    raise ValueError("premature audio reply")
                                audio = resampler.feed(msg.data)
                                if audio:
                                    output_bytes += len(audio)
                                    if output_bytes > 32000 * MAX_REPLY_AUDIO_SECONDS:
                                        raise ValueError("reply too long")
                                    if not speaking:
                                        await event("speaking")
                                        speaking = True
                                    # Bounded packets prevent a large cloud message exhausting device RAM.
                                    for offset in range(0, len(audio), 2048):
                                        async with send_lock:
                                            await asyncio.wait_for(ws.send_bytes(audio[offset:offset + 2048]), 5)
                            if sc and sc.turn_complete:
                                if phase != "reply":
                                    raise ValueError("premature turn completion")
                                current()
                                for role, parts in (("user", heard), ("assistant", said)):
                                    if parts:
                                        store.add_turn(did, role, "".join(parts))
                                if (not continuous or turns >= MAX_CONVERSATION_TURNS
                                        or conversation_over("".join(heard))):
                                    await event("done", **({"follow_up": False} if continuous else {}))
                                    return
                                # Wait for the device to finish playback and explicitly
                                # start its next recording; keep this provider's context.
                                phase = "await_start"
                                waiting_since = time.monotonic()
                                heard.clear(); said.clear()
                                speaking = False
                                output_bytes = 0
                                resampler = PCM16k()
                                await event("done", follow_up=True)
                                break

                async def watch_access():
                    while True:
                        await asyncio.sleep(1)
                        current()
                        if phase == "recording" and time.monotonic() - began > MAX_INPUT_SECONDS + 2:
                            raise TimeoutError("recording timed out")
                        if phase == "await_start" and time.monotonic() - waiting_since > BETWEEN_TURNS_WAIT_SECONDS:
                            raise TimeoutError("follow-up timed out")

                tasks = [asyncio.create_task(fn()) for fn in (input_audio, output_audio, watch_access)]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    task.result()
    except WebSocketDisconnect:
        pass
    except PermissionError:
        await ws.close(code=4403, reason="device access changed")
    except json.JSONDecodeError:
        print(f"[room-voice] {row.get('name') or did}: rejected (4400): control frame was not JSON", flush=True)
        await ws.close(code=4400, reason="invalid voice input")
    except ValueError as exc:
        # These are Ghost Server's own short reasons ("reply too long", ...).
        print(f"[room-voice] {row.get('name') or did}: rejected (4400): {str(exc)[:80]}", flush=True)
        await ws.close(code=4400, reason="invalid voice input")
    except Exception as exc:
        # Provider exceptions may contain URLs or credentials; never forward or
        # log their text - only the exception type, so failures are diagnosable.
        print(f"[room-voice] {row.get('name') or did}: session ended by {type(exc).__name__}", flush=True)
        try:
            await event("error", message="Voice could not connect. Tap Ghost to try again.")
        except Exception:
            pass
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        _active.discard(did)
        if client is not None:
            try:
                await client.aio.aclose()
            except Exception:
                pass
        try:
            await ws.close()
        except Exception:
            pass
