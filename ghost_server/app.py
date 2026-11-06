"""
Ghost Server — HTTP + WebSocket API and the admin page.

    Agents  (Authorization: Bearer <device key>)
      POST /api/v1/pair            {code, name, kind, info}  → {device_id, device_key}   (no key needed)
      GET  /api/v1/session                                    → everything to start Gemini Live
      POST /api/v1/tools/call      {name, args}               → {result}
      POST /api/v1/chat            {text}                     → {text, tools}
      POST /api/v1/assist          {text, room, satellite}    → {text, tools}   (room speakers via Home Assistant)
      POST /api/v1/turns           {turns:[{role,text}]}      → log conversation for shared context
      POST /api/v1/events          {type, data}               → notifications, battery, alerts
      GET  /api/v1/devices                                    → the household's devices
      GET/POST /api/v1/timers, /lists, /notes                 → shared timers, lists, notes
      POST /api/v1/faces/identify {image}  /faces/enroll      → Visual ID
      GET  /api/v1/photos, /photos/{n}?w=&k=                  → the photo frame
      WS   /api/v1/agent?key=…                                → live link (see PROTOCOL.md)
      GET  /files/{id}?k=…                                    → documents Ghost made

    Admin (cookie from /admin/login) — the web page at /
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import __version__, cameras, chat, config, dashboard, faces, household, live, photos, prompt, quiet, store, tools
from .hub import AgentConn, hub

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(_app):
    if store._db_path is None:          # tests (and embedders) may have opened another database
        store.init()
    tools.load()
    hub.loop = asyncio.get_running_loop()
    print(f"[Ghost Server] v{__version__} — data in {config.DATA_DIR}")
    bg = [asyncio.create_task(quiet.quiet_loop()), asyncio.create_task(quiet.safety_loop()),
          asyncio.create_task(household.timer_loop())]
    yield
    for t in bg:
        t.cancel()


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
    out = {
        "device": {"id": d["id"], "name": d["name"], "kind": d["kind"]},
        "assistant_name": store.get("assistant_name"),
        "owner_name": store.get("owner_name"),
        "system_instruction": prompt.build(d),
        "server_tools": tools.declarations("live"),
        "gemini": creds,
        "server_time": time.time(),
    }
    # Away mode: phones and tablets keep a copy of the Gemini key so they can still
    # talk (with their own tools + web search) when they can't reach the server.
    key = str(store.get("gemini_api_key") or "").strip()
    if store.get("away_mode") and key and d.get("kind") in ("phone", "tablet"):
        out["offline"] = {"api_key": key, "model": store.get("gemini_live_model"),
                          "voice": store.get("gemini_voice")}
    return out


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


@app.post("/api/v1/assist")
async def assist_ep(body: dict, dev: dict = Depends(device_auth)):
    """A spoken request from a room speaker behind a hub (the Home Assistant
    integration): answered for speech, aware of the room, with its own history."""
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "text is empty")
    room_dev = chat.room_device(dev, str(body.get("room") or ""), str(body.get("satellite") or ""))
    try:
        out = await asyncio.to_thread(chat.reply, room_dev, text, True)
    except RuntimeError as e:
        out = {"text": str(e), "tools": []}
    out["conversation_id"] = body.get("conversation_id") or room_dev["id"]
    return out


# ── household: timers, lists, notes ──────────────────────────────────────────
@app.get("/api/v1/timers")
async def timers_get(dev: dict = Depends(device_auth)):
    return await asyncio.to_thread(household.timers_snapshot)


@app.post("/api/v1/timers")
async def timers_set(body: dict, dev: dict = Depends(device_auth)):
    """From a screen: {seconds, label} for a timer, or {alarm: '6:30am', days, label}."""
    if body.get("alarm"):
        r = await asyncio.to_thread(household.set_alarm, str(body["alarm"]), str(body.get("label") or ""),
                                    str(body.get("days") or ""), dev["id"], "")
        if not r:
            raise HTTPException(400, "I couldn't read that alarm time")
        return r
    secs = float(body.get("seconds") or 0)
    if secs <= 0:
        raise HTTPException(400, "seconds must be more than 0")
    return await asyncio.to_thread(household.set_timer, secs, str(body.get("label") or ""), dev["id"], "")


@app.post("/api/v1/timers/{tid}")
async def timers_edit(tid: str, body: dict, dev: dict = Depends(device_auth)):
    """{action: stop | cancel | add, seconds}"""
    a = str(body.get("action") or "stop")
    if a == "add":
        ok = await asyncio.to_thread(household.add_time, tid, float(body.get("seconds") or 60))
    elif a == "cancel":
        await asyncio.to_thread(household.cancel_alarm_forever, tid)
        ok = True
    else:
        ok = await asyncio.to_thread(household.stop, tid)
    return {"ok": ok}


@app.get("/api/v1/lists")
async def lists_get(dev: dict = Depends(device_auth)):
    return await asyncio.to_thread(household.lists_snapshot)


@app.post("/api/v1/lists")
async def lists_add(body: dict, dev: dict = Depends(device_auth)):
    added = await asyncio.to_thread(household.add_items, str(body.get("list") or "shopping"),
                                    str(body.get("text") or ""), str(body.get("by") or dev["name"]))
    return {"added": added}


@app.post("/api/v1/lists/item/{iid}")
async def lists_item(iid: int, body: dict, dev: dict = Depends(device_auth)):
    ok = await asyncio.to_thread(household.set_item, iid, bool(body.get("done")), bool(body.get("delete")))
    return {"ok": ok}


@app.get("/api/v1/notes")
async def notes_get(dev: dict = Depends(device_auth), who: str = ""):
    return {"type": "notes", "items": await asyncio.to_thread(household.notes, who)}


@app.post("/api/v1/notes")
async def notes_add(body: dict, dev: dict = Depends(device_auth)):
    if not str(body.get("text") or "").strip():
        raise HTTPException(400, "the note is empty")
    return await asyncio.to_thread(household.leave_note, str(body["text"]), str(body.get("to") or ""),
                                   str(body.get("from") or ""))


@app.post("/api/v1/notes/{nid}")
async def notes_done(nid: int, dev: dict = Depends(device_auth)):
    return {"ok": await asyncio.to_thread(household.done_note, nid)}


# ── Visual ID ────────────────────────────────────────────────────────────────
def _personal(name: str) -> dict:
    """What the tablet shows when it recognises someone: their notes and their own calendar."""
    out = {"notes": household.notes(name)}
    p = faces.person(name)
    if p and p.get("calendar"):
        import httpx
        from .tools.web import events_on, today
        try:
            ics = httpx.get(p["calendar"].replace("webcal://", "https://"), timeout=8, follow_redirects=True).text
            out["agenda"] = [{"time": t, "title": s} for t, s in events_on(ics, today())[:8]]
        except Exception:  # noqa: BLE001
            out["agenda"] = []
    return out


@app.post("/api/v1/faces/identify")
async def faces_identify(body: dict, dev: dict = Depends(device_auth)):
    if not store.get("faces_enabled"):
        return {"people": [], "guests": 0, "faces": 0, "disabled": True}
    try:
        r = await asyncio.to_thread(faces.identify, body.get("image") or "", dev)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001 — models missing or broken
        raise HTTPException(503, f"Visual ID isn't working on the server: {e}")
    if r.get("for"):
        r.update(await asyncio.to_thread(_personal, r["for"]))
    return r


@app.post("/api/v1/faces/enroll")
async def faces_enroll(body: dict, dev: dict = Depends(device_auth)):
    """Adding a person needs the admin password, so a visitor can't teach Ghost to call them you."""
    stored = str(store.get("admin_password_hash") or "")
    if not stored or not _pw_ok(str(body.get("admin_password") or ""), stored):
        raise HTTPException(403, "That isn't the admin password")
    try:
        r = await asyncio.to_thread(faces.enroll, str(body.get("name") or ""), body.get("image") or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"Visual ID isn't working on the server: {e}")
    if r.get("ok") and not store.get("faces_enabled"):
        store.put("faces_enabled", True)
    return r


# ── photo frame ──────────────────────────────────────────────────────────────
@app.get("/api/v1/photos")
async def photos_list(dev: dict = Depends(device_auth)):
    return await asyncio.to_thread(photos.listing)


@app.get("/photos/{n}")
async def photo(n: int, k: str = "", w: int = 1600, authorization: str | None = Header(None)):
    if store.device_by_key(_key_from(authorization, k)) is None:
        raise HTTPException(401, "Unknown device key")
    data = await asyncio.to_thread(photos.image, n, w)
    if data is None:
        raise HTTPException(404, "No such photo")
    from fastapi.responses import Response
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


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
    if body.get("type") == "presence":
        state = str((body.get("data") or {}).get("state", ""))
        if state in PRESENCE and str(store.get(f"on_{state}") or "").strip():
            asyncio.get_running_loop().run_in_executor(None, run_presence, dict(store.device(dev["id"])), state)
            return {"ok": True, "automation": True}
    return {"ok": True}


PRESENCE = {"left_home": "just left home", "approaching_home": "is a couple of km from home",
            "arrived_home": "just got home"}


def run_presence(dev: dict, state: str) -> str:
    """A phone said it left / is nearly / got home: carry out the household's instruction for that."""
    instruction = str(store.get(f"on_{state}") or "").strip()
    if not instruction:
        return ""
    who = store.get("owner_name") or "The owner"
    prompt = (f"[AUTOMATION — not a person talking] {who} ({dev['name']}) {PRESENCE[state]}. "
              f"Carry out this standing instruction now, without asking questions: {instruction}\n"
              "Never unlock doors, open garage doors or disarm alarms from an automation; if the instruction asks "
              "for that, skip it and say so. Then reply with one short sentence saying what you did.")
    try:
        text = chat.reply(dev, prompt).get("text", "")
    except Exception as e:  # noqa: BLE001 — automations must never break the events endpoint
        text = f"Couldn't run the '{state.replace('_', ' ')}' automation: {e}"
    if text:
        hub.send_sync(dev["id"], {"type": "task_done", "status": "done", "text": text})
    return text


@app.get("/api/v1/dashboard")
async def dashboard_ep(dev: dict = Depends(device_auth)):
    """The Ghost Hub tablet's always-on screen: weather, calendar, home tiles, headlines."""
    return await asyncio.to_thread(dashboard.build)


@app.post("/api/v1/home/switch")
async def home_switch(body: dict, dev: dict = Depends(device_auth)):
    """A tap on a home tile. Lights, switches, fans, scenes only — never locks, alarms or doors."""
    eid = str(body.get("entity_id", ""))
    if not re.fullmatch(r"[a-z_]+\.[A-Za-z0-9_]+", eid):
        raise HTTPException(400, "bad entity_id")
    try:
        return await asyncio.to_thread(dashboard.switch, eid, str(body.get("action", "toggle")))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


# ── security cameras ─────────────────────────────────────────────────────────
@app.get("/api/v1/cameras")
async def cameras_list(dev: dict = Depends(device_auth)):
    cams = await asyncio.to_thread(cameras.all_cameras)
    return {"cameras": [{"id": c.id, "name": c.name, "source": c.source} for c in cams]}


@app.get("/api/v1/cameras/status")
async def cameras_status(dev: dict = Depends(device_auth)):
    """For the Cameras screen: the cameras, what Ghost is watching, and the latest alerts (with pictures)."""
    from . import watcher
    cams = await asyncio.to_thread(cameras.all_cameras)
    watches = [{"camera": w.cam.name, "until": w.until, "look_for": w.look_for, "alerts": w.alerts,
                "seen": [s for _, s in w.recent[-3:]], "problem": w.error} for w in list(watcher.WATCHES.values())]
    alerts = [{"camera": e["data"].get("camera", ""), "summary": e["data"].get("summary", ""),
               "picture": e["data"].get("picture", ""), "at": e["at"]}
              for e in store.recent_events(10, "camera_alert")]
    return {"cameras": [{"id": c.id, "name": c.name, "source": c.source} for c in cams],
            "watching": watches, "alerts": alerts}


# ── Do Not Disturb ───────────────────────────────────────────────────────────
@app.get("/api/v1/quiet")
async def quiet_get(dev: dict = Depends(device_auth)):
    return quiet.snapshot(dev["kind"], dev["name"])


@app.post("/api/v1/quiet")
async def quiet_set(body: dict, dev: dict = Depends(device_auth)):
    """{"scope": "me"|"house", "on": bool, "minutes"?: n, "until"?: "7am"} and/or settings:
    {"me_schedule": "22:00-06:30", "house_schedule": "", "favourites_phone": [...], "android_sync": bool,
     "safety": bool}"""
    try:
        if "on" in body:
            quiet.set_quiet(str(body.get("scope") or "me"), bool(body["on"]),
                            minutes=body.get("minutes"), until=body.get("until"))
        for scope in quiet.SCOPES:
            if f"{scope}_schedule" in body:
                quiet.set_schedule(scope, str(body[f"{scope}_schedule"] or ""))
        if "favourites_phone" in body:
            names = [str(n).strip() for n in (body.get("favourites_phone") or []) if str(n).strip()][:100]
            store.put("quiet_favourites_phone", names)
        if "android_sync" in body:
            store.put("quiet_android_sync", bool(body["android_sync"]))
        if "safety" in body:
            store.put("safety_alerts", bool(body["safety"]))
    except ValueError as e:
        raise HTTPException(400, str(e))
    await quiet.tick(force=True)
    return quiet.snapshot(dev["kind"], dev["name"])


@app.post("/api/v1/cameras/view")
async def cameras_view(body: dict, dev: dict = Depends(device_auth)):
    """A short-lived link (/cam/<token>) that shows one camera, or 'all' as a grid."""
    cams = await asyncio.to_thread(cameras.find, str(body.get("camera") or "all"))
    if not cams:
        raise HTTPException(404, "no camera matches that — or none are set up yet")
    return {"path": "/cam/" + cameras.issue(cams), "title": cams[0].name if len(cams) == 1 else "Cameras",
            "count": len(cams)}


@app.get("/cam/{tok}", response_class=HTMLResponse)
async def cam_page(tok: str):
    cams = cameras.view(tok)
    if cams is None:
        return HTMLResponse("<body style='background:#000;color:#aaa;font-family:sans-serif;padding:40px'>"
                            "This camera link has expired — ask Ghost to show the cameras again.</body>", 410)
    return cameras.page(tok, cams)


@app.get("/cam/{tok}/stream/{i}")
async def cam_stream(tok: str, i: int):
    from fastapi.responses import StreamingResponse
    cams = cameras.view(tok)
    if cams is None or not 0 <= i < len(cams):
        raise HTTPException(404, "expired")
    try:
        ctype, body = await cameras.mjpeg(cams[i])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"camera unavailable: {e}")
    return StreamingResponse(body, media_type=ctype, headers={"Cache-Control": "no-store"})


@app.get("/cam/{tok}/snap/{i}")
async def cam_snap(tok: str, i: int):
    from fastapi.responses import Response
    cams = cameras.view(tok)
    if cams is None or not 0 <= i < len(cams):
        raise HTTPException(404, "expired")
    try:
        jpg = await asyncio.to_thread(cameras.snapshot, cams[i])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"camera unavailable: {e}")
    return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


MAX_UPLOAD = 40 * 1024 * 1024


@app.post("/api/v1/files")
async def upload_file(request: Request, name: str = "file", dev: dict = Depends(device_auth)):
    """A device shares a file (a photo from the phone…). Raw body. Returns its /files/<id> path, which any
    paired device can download with its own key — e.g. send it to the PC with device_action receive_file."""
    import re
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file too big (40 MB max)")
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name)[:120] or "file"
    folder = config.FILES_DIR / time.strftime("%Y-%m")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{secrets.token_hex(4)}-{safe}"
    path.write_bytes(data)
    fid = store.add_file(safe, path, dev["id"])
    return {"id": fid, "name": safe, "path": f"/files/{fid}", "bytes": len(data)}


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
    await ws.send_json(quiet.snapshot(row["kind"], row["name"]))
    for snap in (household.timers_snapshot, household.lists_snapshot, household.notes_snapshot):
        try:
            await ws.send_json(await asyncio.to_thread(snap))
        except Exception:  # noqa: BLE001
            pass
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
    if any(k.startswith(("quiet_", "safety_")) for k in body):
        quiet.tick_soon()
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


@app.get("/admin/api/people")
def admin_people(_: bool = Depends(admin_auth)):
    return {"people": faces.people(), "seen": faces.last_seen(), "enabled": bool(store.get("faces_enabled"))}


@app.post("/admin/api/people/{pid}")
def admin_person(pid: int, body: dict, _: bool = Depends(admin_auth)):
    if body.get("delete"):
        faces.delete_person(pid)
    else:
        faces.edit_person(pid, body.get("name"), body.get("calendar"))
    return {"people": faces.people()}


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
