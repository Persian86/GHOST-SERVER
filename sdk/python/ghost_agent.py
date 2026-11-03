"""
ghost_agent — the Python SDK for building Ghost agents.

An agent is anything that connects to Ghost Server: a desktop app, a tablet
panel, a Raspberry Pi in the kitchen. It:

  1. pairs once with a code from the admin page (the key is saved locally),
  2. declares its own CAPABILITIES — things only this device can do
     (open an app, take a screenshot, show a picture) — which the server and
     every other device can then use,
  3. keeps a live link open so the server can call those capabilities and
     send it things to say,
  4. talks to the user — usually with its own Gemini Live session, built from
     `session()`, forwarding any server tool calls to the server.

Minimal use:

    from ghost_agent import GhostAgent

    agent = GhostAgent("http://192.168.80.244:8780", name="Office PC", kind="desktop")
    agent.pair_if_needed()                     # asks for the code on first run

    @agent.capability("open_app", "Open an application on this PC by name",
                      {"type": "OBJECT", "properties": {"name": {"type": "STRING"}}, "required": ["name"]})
    def open_app(args):
        ...
        return "opened"

    agent.on_say = lambda text: print("SAY:", text)
    asyncio.run(agent.run_link())              # stays connected, reconnects by itself

Dependencies: httpx, websockets (and google-genai for the Live helpers).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import platform
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

__all__ = ["GhostAgent", "GhostError"]


class GhostError(RuntimeError):
    pass


class GhostAgent:
    def __init__(self, server: str, name: str = "", kind: str = "agent", key: str | None = None,
                 state_file: str | os.PathLike | None = None):
        self.server = server.rstrip("/")
        self.name = name or platform.node() or "Ghost agent"
        self.kind = kind
        self.state_file = Path(state_file or Path.home() / ".ghost_agent.json")
        self.key = key or self._load_key()
        self.capabilities: dict[str, dict] = {}       # name -> {decl, handler}
        self.on_say: Callable[[str], Any] | None = None
        self.on_message: Callable[[dict], Any] | None = None
        self.on_task_done: Callable[[dict], Any] | None = None
        self.info: dict = {"platform": platform.system(), "sdk": "python/0.1"}
        self.connected = False
        self._ws = None

    # ── pairing ──────────────────────────────────────────────────────────────
    def _load_key(self) -> str:
        try:
            data = json.loads(self.state_file.read_text())
            if data.get("server") == self.server:
                return data.get("device_key", "")
        except Exception:
            pass
        return ""

    def pair(self, code: str) -> dict:
        r = httpx.post(f"{self.server}/api/v1/pair", timeout=15,
                       json={"code": code, "name": self.name, "kind": self.kind, "info": self.info})
        if r.status_code != 200:
            raise GhostError(r.json().get("detail", r.text))
        data = r.json()
        self.key = data["device_key"]
        self.state_file.write_text(json.dumps({"server": self.server, "device_key": self.key,
                                               "device_id": data["device_id"]}))
        return data

    def pair_if_needed(self, ask: Callable[[str], str] = input) -> None:
        if not self.key:
            self.pair(ask("Pairing code from the Ghost Server admin page: ").strip())

    # ── HTTP API ─────────────────────────────────────────────────────────────
    def _h(self) -> dict:
        if not self.key:
            raise GhostError("not paired yet")
        return {"Authorization": f"Bearer {self.key}"}

    def _post(self, path: str, body: dict, timeout: float = 60) -> dict:
        r = httpx.post(self.server + path, json=body, headers=self._h(), timeout=timeout)
        if r.status_code >= 400:
            raise GhostError(f"{r.status_code}: {r.text[:200]}")
        return r.json()

    def session(self) -> dict:
        """System instruction, server tool declarations and Gemini credentials
        for a fresh Live session. Call it for EVERY new session — the
        instruction carries the current time, memory and device list."""
        r = httpx.get(self.server + "/api/v1/session", headers=self._h(), timeout=30)
        if r.status_code >= 400:
            raise GhostError(f"{r.status_code}: {r.text[:200]}")
        return r.json()

    def call_tool(self, name: str, args: dict | None = None, timeout: float = 200) -> str:
        """Run a SERVER tool (memory, smart home, think_deeply, device_action…)."""
        return self._post("/api/v1/tools/call", {"name": name, "args": args or {}}, timeout)["result"]

    def chat(self, text: str) -> dict:
        """Text conversation with Claude + every tool. Returns {text, tools}."""
        return self._post("/api/v1/chat", {"text": text}, 200)

    def log_turns(self, turns: list[dict]) -> None:
        """[{role: user|assistant, text}] — lets other devices pick up the thread."""
        self._post("/api/v1/turns", {"turns": turns}, 15)

    def event(self, type_: str, data: dict) -> None:
        """Report something: notification, battery, alert, status…"""
        self._post("/api/v1/events", {"type": type_, "data": data}, 15)

    def file_url(self, fid: str) -> str:
        return f"{self.server}/files/{fid}?k={self.key}"

    # ── capabilities ─────────────────────────────────────────────────────────
    def capability(self, name: str, description: str, parameters: dict | None = None):
        """Decorator: declare something only this device can do. The handler
        gets the args dict (sync or async) and returns a short result string."""
        def deco(fn):
            self.capabilities[name] = {
                "decl": {"name": name, "description": " ".join(description.split()),
                         "parameters": parameters or {"type": "OBJECT", "properties": {}}},
                "handler": fn}
            return fn
        return deco

    def declarations(self) -> list[dict]:
        return [c["decl"] for c in self.capabilities.values()]

    async def run_capability(self, name: str, args: dict) -> dict:
        cap = self.capabilities.get(name)
        if cap is None:
            return {"ok": False, "error": f"this device has no '{name}'"}
        try:
            out = cap["handler"](args or {})
            if inspect.isawaitable(out):
                out = await out
            return {"ok": True, "result": "done" if out is None else str(out)}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ── live link ────────────────────────────────────────────────────────────
    async def run_link(self, stop: Optional[asyncio.Event] = None) -> None:
        """Stay connected for as long as `stop` is unset. Reconnects with backoff."""
        import websockets
        url = self.server.replace("http://", "ws://").replace("https://", "wss://") + \
            f"/api/v1/agent?key={self.key}"
        backoff = 1.0
        while not (stop and stop.is_set()):
            try:
                async with websockets.connect(url, ping_interval=25, max_size=4_000_000) as ws:
                    self._ws = ws
                    await ws.send(json.dumps({"type": "hello", "capabilities": self.declarations(),
                                              "info": self.info}))
                    self.connected, backoff = True, 1.0
                    async for raw in ws:
                        await self._handle(ws, json.loads(raw))
                        if stop and stop.is_set():
                            break
            except Exception as e:
                code = getattr(getattr(e, "rcvd", None), "code", None)
                if code == 4401:
                    raise GhostError("the server rejected this device key — pair again") from e
            finally:
                self.connected, self._ws = False, None
            if stop and stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _handle(self, ws, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "call":
            res = await self.run_capability(msg.get("tool", ""), msg.get("args") or {})
            await ws.send(json.dumps({"type": "result", "id": msg.get("id"), **res}))
        elif kind == "say" and self.on_say:
            out = self.on_say(msg.get("text", ""))
            if inspect.isawaitable(out):
                await out
        elif kind == "task_done" and (self.on_task_done or self.on_say):
            cb = self.on_task_done or (lambda m: self.on_say(m.get("say", "")))
            out = cb(msg)
            if inspect.isawaitable(out):
                await out
        elif self.on_message:
            out = self.on_message(msg)
            if inspect.isawaitable(out):
                await out

    async def send_event(self, event: str, data: dict) -> None:
        if self._ws is not None:
            await self._ws.send(json.dumps({"type": "event", "event": event, "data": data}))

    # ── Gemini Live helpers ──────────────────────────────────────────────────
    def live_setup(self, session: dict | None = None, *, response_audio: bool = True):
        """(client, model, config) for google-genai's client.aio.live.connect().
        The config carries the server's instruction plus server AND local tools."""
        from google import genai
        from google.genai import types
        s = session or self.session()
        g = s["gemini"]
        if g.get("error"):
            raise GhostError(g["error"])
        client = genai.Client(api_key=g.get("token") or g.get("api_key"),
                              http_options={"api_version": g.get("api_version", "v1alpha")})
        decls = s["server_tools"] + self.declarations()
        cfg = types.LiveConnectConfig(
            response_modalities=["AUDIO"] if response_audio else ["TEXT"],
            system_instruction=s["system_instruction"],
            tools=[{"function_declarations": decls}],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=g.get("voice") or "Fenrir"))),
            output_audio_transcription={}, input_audio_transcription={},
        )
        model = g["model"] if g["model"].startswith("models/") else "models/" + g["model"]
        return client, model, cfg

    async def answer_tool_calls(self, live_session, tool_call) -> None:
        """Run every call in a Live `tool_call` — locally if it is one of this
        device's capabilities, otherwise on the server — and send the results."""
        from google.genai import types
        responses = []
        for fc in tool_call.function_calls:
            args = dict(fc.args or {})
            if fc.name in self.capabilities:
                r = await self.run_capability(fc.name, args)
                result = r.get("result") if r["ok"] else f"failed: {r['error']}"
            else:
                result = await asyncio.to_thread(self.call_tool, fc.name, args)
            responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response={"result": result}))
        await live_session.send_tool_response(function_responses=responses)
