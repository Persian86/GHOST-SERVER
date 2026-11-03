"""
Ghost Server — HTTP + WebSocket API and the admin page.

    Agents  (Authorization: Bearer <device key>)
      POST /api/v1/pair            {code, name, kind, info}  → {device_id, device_key}   (no key needed)
      GET  /api/v1/session                                    → everything to start Gemini Live
      POST /api/v1/tools/call      {name, args}               → {result}
      POST /api/v1/chat            {text}                     → {text, tools}
      POST /api/v1/turns           {turns:[{role,text}]}      → log conversation for shared context
      POST /api/v1/events          {type, data}               → notifications, battery, alerts
      GET  /api/v1/devices                                    → the household's devices
      WS   /api/v1/agent?key=…                                → live link (see PROTOCOL.md)
      GET  /files/{id}?k=…                                    → documents Ghost made

    Admin (cookie from /admin/login) — the web page at /
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import __version__, chat, config, live, prompt, store, tools
from .hub import AgentConn, hub

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(_app):
    if store._db_path is None:          # tests (and embedders) may have opened another database
        store.init()
    tools.load()
    hub.loop = asyncio.get_running_loop()
    print(f"[Ghost Server] v{__version__} — data in {config.DATA_DIR}")
    yield


app = FastAPI(title="Ghost Server", version=__version__, lifespan=_lifespan)
STATIC = Path(__file__).parent / "static"
_admin_sessions: dict[str, float] = {}
ADMIN_TTL = 7 * 86400


# ─────────────────────────────────────────────────────────────────────────────
#  Auth helpers
# ─────────────────────────────────────────────────────────────────────────────
def _key_from(authorization: str | None, k: str | None = None) -> str:
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:].strip()
    return (k or "").strip()


def device_auth(authorization: str | None = Header(None)) -> dict:
    row = store.device_by_key(_key_from(authorization))
    if row is None:
        raise HTTPException(401, "Unknown or revoked device key — pair this device again")
    store.touch_device(row["id"])
    return {"id": row["id"], "name": row["name"], "kind": row["kind"]}


def _pw_hash(pw: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${h}"


def _pw_ok(pw: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
        return hmac.compare_digest(_pw_hash(pw, salt), stored)
    except ValueError:
        return False


def admin_auth(request: Request) -> bool:
    tok = request.cookies.get("ghost_admin", "")
    if tok and _admin_sessions.get(tok, 0) > time.time():
        return True
    raise HTTPException(401, "Admin login needed")


# ─────────────────────────────────────────────────────────────────────────────
#  Agents
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/v1/health")
def health():
    return {"ok": True, "app": "ghost-server", "version": __version__, "online": len(hub.conns)}


@app.post("/api/v1/pair")
async def pair(body: dict):
    got = store.redeem_pair_code(str(body.get("code", "")), str(body.get("name", "")),
                                 str(body.get("kind", "agent")), body.get("info") or {})
    if not got:
        raise HTTPException(400, "That pairing code is wrong or has expired — make a new one on the admin page")
    did, key = got
    return {"device_id": did, "device_key": key, "assistant_name": store.get("assistant_name")}


@app.get("/api/v1/session")
async def session(dev: dict = Depends(device_auth)):
    d = dict(store.device(dev["id"]))
    creds = await asyncio.to_thread(live.credentials)
    return {
        "device": {"id": d["id"], "name": d["name"], "kind": d["kind"]},
        "assistant_name": store.get("assistant_name"),
        "owner_name": store.get("owner_name"),
        "system_instruction": prompt.build(d),
        "server_tools": tools.declarations("live"),
        "gemini": creds,
        "server_time": time.time(),
    }


@app.post("/api/v1/tools/call")
async def tool_call(body: dict, dev: dict = Depends(device_auth)):
    name = str(body.get("name", ""))
    ctx = tools.Ctx(dev["id"], dev["name"], "live")
    result = await asyncio.to_thread(tools.run, name, body.get("args") or {}, ctx)
    return {"name": name, "result": result}


@app.post("/api/v1/chat")
async def chat_ep(body: dict, dev: dict = Depends(device_auth)):
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "text is empty")
    try:
        return await asyncio.to_thread(chat.reply, dev, text)
    except RuntimeError as e:
        return {"text": str(e), "tools": []}


@app.post("/api/v1/llm")
async def llm(body: dict, dev: dict = Depends(device_auth)):
    """One-shot generation on the server's Claude, for an agent's background
    work (reading a screenshot, writing code, summarising a file). Messages use
    the OpenAI shape; images as data-URI image_url parts are accepted."""
    from .tools.brain import claude
    messages = body.get("messages") or []
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "messages is empty")
    fast = str(body.get("tier", "")).lower() == "fast"
    max_tokens = max(16, min(int(body.get("max_tokens") or 4096), 16000))

    def run():
        text, _ = claude(fast=fast).complete(messages, timeout=float(body.get("timeout") or 180),
                                             temperature=body.get("temperature"), max_tokens=max_tokens)
        return text
    try:
        return {"text": await asyncio.to_thread(run)}
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.post("/api/v1/turns")
async def turns(body: dict, dev: dict = Depends(device_auth)):
    n = 0
    for t in (body.get("turns") or [])[:50]:
        role = "assistant" if str(t.get("role")) in ("assistant", "model", "ghost") else "user"
        store.add_turn(dev["id"], role, str(t.get("text", "")))
        n += 1
    return {"ok": True, "saved": n}


@app.post("/api/v1/events")
async def events(body: dict, dev: dict = Depends(device_auth)):
    store.add_event(dev["id"], str(body.get("type", "event")), body.get("data") or {})
    if body.get("type") == "status":
        store.touch_device(dev["id"], info=body.get("data") or {})
    return {"ok": True}


@app.get("/api/v1/devices")
async def devices_ep(dev: dict = Depends(device_auth)):
    return {"devices": [{"id": d["id"], "name": d["name"], "kind": d["kind"], "online": hub.is_online(d["id"]),
                         "capabilities": [c.get("name") for c in (hub.conns[d["id"]].capabilities
                                                                    if hub.is_online(d["id"]) else d["capabilities"])]}
                        for d in store.devices()]}


@app.get("/files/{fid}")
async def get_file(fid: str, request: Request, k: str = "", authorization: str | None = Header(None)):
    ok = store.device_by_key(_key_from(authorization, k)) is not None
    if not ok:
        tok = request.cookies.get("ghost_admin", "")
        ok = _admin_sessions.get(tok, 0) > time.time()
    if not ok:
        raise HTTPException(401, "Not allowed")
    f = store.file(fid)
    if not f or not Path(f["path"]).exists():
        raise HTTPException(404, "No such file")
    return FileResponse(f["path"], filename=f["name"])


@app.websocket("/api/v1/agent")
async def agent_ws(ws: WebSocket, key: str = ""):
    row = store.device_by_key(key or _key_from(ws.headers.get("authorization")))
    if row is None:
        await ws.close(code=4401, reason="bad device key")
        return
    await ws.accept()
    conn = AgentConn(device_id=row["id"], name=row["name"], kind=row["kind"], ws=ws,
                     capabilities=json.loads(row["capabilities"] or "[]"))
    hub.attach(conn)
    store.touch_device(row["id"])
    await ws.send_json({"type": "welcome", "device_id": row["id"], "name": row["name"],
                        "assistant_name": store.get("assistant_name")})
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "hello":
                caps = [c for c in (msg.get("capabilities") or []) if isinstance(c, dict) and c.get("name")][:60]
                conn.capabilities, conn.info = caps, msg.get("info") or {}
                store.touch_device(row["id"], capabilities=caps, info=conn.info)
                await ws.send_json({"type": "ready", "server_tools": [t["name"] for t in tools.declarations("live")]})
            elif kind == "result":
                hub.resolve(msg)
            elif kind == "event":
                store.add_event(row["id"], str(msg.get("event", "event")), msg.get("data") or {})
            elif kind == "ping":
                store.touch_device(row["id"])
                await ws.send_json({"type": "pong", "t": time.time()})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[Ghost Server] agent {row['name']} dropped: {e}")
    finally:
        hub.detach(row["id"], ws)


# ─────────────────────────────────────────────────────────────────────────────
#  Admin
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def admin_page():
    return (STATIC / "admin.html").read_text(encoding="utf-8")


@app.get("/admin/state")
def admin_state(request: Request):
    tok = request.cookies.get("ghost_admin", "")
    return {"needs_setup": not store.get("admin_password_hash"),
            "logged_in": _admin_sessions.get(tok, 0) > time.time()}


@app.post("/admin/login")
def admin_login(body: dict):
    pw = str(body.get("password", ""))
    stored = store.get("admin_password_hash")
    if not stored:
        if len(pw) < 6:
            raise HTTPException(400, "Choose a password of at least 6 characters")
        store.put("admin_password_hash", _pw_hash(pw))
    elif not _pw_ok(pw, stored):
        time.sleep(1)
        raise HTTPException(401, "Wrong password")
    tok = secrets.token_urlsafe(24)
    _admin_sessions[tok] = time.time() + ADMIN_TTL
    r = JSONResponse({"ok": True})
    r.set_cookie("ghost_admin", tok, max_age=ADMIN_TTL, httponly=True, samesite="lax")
    return r


@app.get("/admin/api/overview")
def overview(_: bool = Depends(admin_auth)):
    from .tools.tasks import TASKS
    devs = store.devices()
    for d in devs:
        d["online"] = hub.is_online(d["id"])
        if d["online"]:
            d["capabilities"] = hub.conns[d["id"]].capabilities
    return {"settings": store.all_settings(), "devices": devs, "facts": store.facts(), "rules": store.rules(),
            "events": store.recent_events(25), "files": store.files(20), "turns": store.recent_turns(30),
            "tasks": [{"id": k, **{x: v[x] for x in ("goal", "status", "result", "started")}, "steps": len(v["log"])}
                      for k, v in list(TASKS.items())[-10:]],
            "tools": [t["name"] for t in tools.declarations("live")], "version": __version__}


@app.put("/admin/api/settings")
def save_settings(body: dict, _: bool = Depends(admin_auth)):
    for k, v in body.items():
        if k not in store.DEFAULTS or k == "admin_password_hash":
            continue
        if k in store.SECRET_KEYS and (v is None or str(v).startswith("•••")):
            continue                      # untouched masked secret
        store.put(k, v)
    return {"ok": True, "settings": store.all_settings()}


@app.post("/admin/api/pair-code")
def pair_code(_: bool = Depends(admin_auth)):
    return {"code": store.new_pair_code(10), "expires_in": 600}


@app.post("/admin/api/devices/{did}")
def device_edit(did: str, body: dict, _: bool = Depends(admin_auth)):
    if body.get("revoke"):
        store.revoke_device(did)
        c = hub.conns.get(did)
        if c:
            asyncio.ensure_future(c.ws.close(code=4401, reason="revoked"))
    if body.get("name"):
        store.rename_device(did, str(body["name"]))
    return {"ok": True}


@app.post("/admin/api/devices/{did}/say")
async def device_say(did: str, body: dict, _: bool = Depends(admin_auth)):
    msg = {"type": "say", "text": str(body.get("text", "")), "from": "admin"}
    ok = await (hub.broadcast(msg) if did == "all" else hub.send(did, msg))
    return {"ok": bool(ok)}


@app.post("/admin/api/memory")
def memory_edit(body: dict, _: bool = Depends(admin_auth)):
    if body.get("add_fact"):
        store.add_fact(body["add_fact"], body.get("section") or "Notes", "admin")
    if body.get("delete_fact"):
        store.x("DELETE FROM facts WHERE id = ?", (int(body["delete_fact"]),))
    if body.get("add_rule"):
        store.add_rule(body["add_rule"])
    if body.get("delete_rule"):
        store.x("DELETE FROM rules WHERE id = ?", (int(body["delete_rule"]),))
    return {"ok": True}


@app.post("/admin/api/test/{what}")
async def test(what: str, _: bool = Depends(admin_auth)):
    def run():
        try:
            if what == "claude":
                from .tools.brain import claude
                t, _ = claude(fast=True).complete([{"role": "user", "content": "Reply with: ok"}], timeout=20, max_tokens=5)
                return True, f"Claude answered: {t.strip()}"
            if what == "gemini":
                c = live.credentials()
                return ("error" not in c), c.get("error") or ("Ephemeral token OK" if c.get("token") else c.get("note"))
            if what == "ha":
                from .tools import home
                n = len(home._req("GET", "/api/states").json())
                return True, f"Home Assistant: {n} entities"
            if what == "marquee":
                from .tools.marquee import _req
                d = _req("GET", "/ext/v1/me").json()
                return True, f"Marquee: {d.get('serverName')} as {d.get('profile', {}).get('name')}"
            if what == "briefing":
                from .tools.web import briefing_parts
                p = briefing_parts()
                return bool(p), " | ".join(f"{k}: {v[:60]}" for k, v in p.items())
            return False, "unknown test"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
    ok, msg = await asyncio.to_thread(run)
    return {"ok": ok, "message": msg}


@app.post("/admin/api/chat")
async def admin_chat(body: dict, _: bool = Depends(admin_auth)):
    """Talk to Ghost from the admin page (as a pseudo-device)."""
    rows = store.q("SELECT * FROM devices WHERE id = 'dev_admin'")
    if not rows:
        store.x("INSERT INTO devices (id, name, kind, key_hash, created_at, last_seen) VALUES (?,?,?,?,?,?)",
                ("dev_admin", "Admin page", "web", "admin-" + secrets.token_hex(8), time.time(), time.time()))
    try:
        return await asyncio.to_thread(chat.reply, {"id": "dev_admin", "name": "Admin page", "kind": "web"},
                                       str(body.get("text", "")))
    except RuntimeError as e:
        return {"text": str(e), "tools": []}


def main() -> None:
    import uvicorn
    config.ensure_dirs()
    uvicorn.run("ghost_server.app:app", host="0.0.0.0", port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
