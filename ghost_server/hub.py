"""
Live connections to agents.

Every agent keeps one WebSocket open to /api/v1/agent. Over it the server can:
  * ask the agent to run one of ITS tools  ({"type":"call"} → {"type":"result"})
  * tell it to say something                ({"type":"say"})
  * push anything else an agent understands ({"type":"notify"}, "refresh" …)

Tool handlers run on worker threads, so the *_sync helpers bridge into the
event loop with run_coroutine_threadsafe.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import store


@dataclass
class AgentConn:
    device_id: str
    name: str
    kind: str
    ws: Any
    capabilities: list = field(default_factory=list)   # Gemini-style declarations
    info: dict = field(default_factory=dict)
    since: float = field(default_factory=time.time)


class Hub:
    def __init__(self) -> None:
        self.conns: dict[str, AgentConn] = {}
        self.pending: dict[str, asyncio.Future] = {}
        self.pending_owner: dict[str, str] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    # ── connection lifecycle ─────────────────────────────────────────────────
    def attach(self, conn: AgentConn) -> None:
        self.loop = asyncio.get_running_loop()
        old = self.conns.get(conn.device_id)
        self.conns[conn.device_id] = conn
        if old is not None and old.ws is not conn.ws:
            asyncio.ensure_future(_close_quietly(old.ws))

    def detach(self, device_id: str, ws: Any) -> None:
        c = self.conns.get(device_id)
        if c is not None and c.ws is ws:
            self.conns.pop(device_id, None)

    def online(self) -> list[AgentConn]:
        return list(self.conns.values())

    def is_online(self, device_id: str) -> bool:
        return device_id in self.conns

    # ── messages from agents ─────────────────────────────────────────────────
    def resolve(self, msg: dict, device_id: str = "") -> None:
        cid = str(msg.get("id") or "")
        if self.pending_owner.get(cid) != device_id:
            return
        self.pending_owner.pop(cid, None)
        fut = self.pending.pop(cid, None)
        if fut is not None and not fut.done():
            fut.set_result(msg)

    # ── server → agent ───────────────────────────────────────────────────────
    async def send(self, device_id: str, msg: dict) -> bool:
        c = self.conns.get(device_id)
        if c is None:
            return False
        from . import access
        if not access.outgoing_allowed(device_id,msg):
            return False
        # Do Not Disturb: hold what would be said, unless it's allowed through
        if msg.get("type") in ("say", "task_done"):
            from . import quiet
            if quiet.device_quiet(c.kind, name=c.name) and not quiet.gets_through(msg):
                quiet.hold(device_id, msg)
                return True
        try:
            await c.ws.send_json(msg)
            return True
        except Exception:
            self.detach(device_id, c.ws)
            return False

    async def call(self, device_id: str, tool: str, args: dict, timeout: float = 20.0) -> dict:
        cid = "c_" + secrets.token_hex(6)
        fut = asyncio.get_running_loop().create_future()
        self.pending[cid] = fut
        self.pending_owner[cid] = device_id
        if not await self.send(device_id, {"type": "call", "id": cid, "tool": tool, "args": args or {}}):
            self.pending.pop(cid, None)
            self.pending_owner.pop(cid, None)
            return {"ok": False, "error": "that device is offline"}
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self.pending.pop(cid, None)
            self.pending_owner.pop(cid, None)
            return {"ok": False, "error": "the device did not answer in time"}

    async def broadcast(self, msg: dict, except_id: str = "") -> int:
        n = 0
        for did in list(self.conns):
            if did != except_id and await self.send(did, msg):
                n += 1
        return n

    # ── bridges for worker threads ──────────────────────────────────────────
    def call_sync(self, device_id: str, tool: str, args: dict, timeout: float = 20.0) -> dict:
        if self.loop is None:
            return {"ok": False, "error": "no devices have connected yet"}
        fut = asyncio.run_coroutine_threadsafe(self.call(device_id, tool, args, timeout), self.loop)
        try:
            return fut.result(timeout + 2)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def send_sync(self, device_id: str, msg: dict) -> bool:
        if self.loop is None:
            return False
        try:
            return asyncio.run_coroutine_threadsafe(self.send(device_id, msg), self.loop).result(5)
        except Exception:
            return False

    def broadcast_sync(self, msg: dict, except_id: str = "") -> int:
        if self.loop is None:
            return 0
        try:
            return asyncio.run_coroutine_threadsafe(self.broadcast(msg, except_id), self.loop).result(5)
        except Exception:
            return 0


async def _close_quietly(ws) -> None:
    try:
        await ws.close(code=4000, reason="replaced by a newer connection")
    except Exception:
        pass


hub = Hub()


def find_device(name: str, online_only: bool = True) -> Optional[dict]:
    """A device by (part of) its name, id or kind."""
    n = str(name or "").strip().lower()
    if not n:
        return None
    pool = store.devices()
    if online_only:
        pool = [d for d in pool if hub.is_online(d["id"])]
    for test in (lambda d: d["id"].lower() == n or d["name"].lower() == n,
                 lambda d: n in d["name"].lower() or d["name"].lower() in n,
                 lambda d: d["kind"].lower() == n or n.rstrip("s") in d["kind"].lower()):
        hits = [d for d in pool if test(d)]
        if hits:
            return hits[0]
    return None

