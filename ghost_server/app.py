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

from . import (__version__, cameras, chat, config, dashboard, door, faces, household, live, meals, photos,
               prompt, quiet, security, store, theme, tools, voices)
from .hub import AgentConn, hub

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(_app):
    if store._db_path is None:          # tests (and embedders) may have opened another database
        store.init()
    tools.load()
    from . import routines
    routines.recover()
    hub.loop = asyncio.get_running_loop()
    print(f"[Ghost Server] v{__version__} — data in {config.DATA_DIR}")
    bg = [asyncio.create_task(quiet.quiet_loop()), asyncio.create_task(quiet.safety_loop()),
          asyncio.create_task(household.timer_loop()), asyncio.create_task(door.door_loop()),
          asyncio.create_task(theme.theme_loop())]
    yield
    for t in bg:
        t.cancel()


app = FastAPI(title="Ghost Server", version=__version__, lifespan=_lifespan)

# plain HTTP stays open for these even in "HTTPS only", so a device can fetch the certificate
_PLAIN_OK = ("/api/v1/health", "/api/v1/tls", "/ghost-server.crt")


@app.middleware("http")
async def _https_only(request: Request, call_next):
    if store.get("https_only") and request.url.scheme == "http" and not request.url.path.startswith(_PLAIN_OK):
        from . import tls
        if tls.pem():
            host = (request.url.hostname or "localhost")
            if request.url.path in ("/", "") and request.method == "GET":
                from fastapi.responses import RedirectResponse
                return RedirectResponse(f"https://{host}:{tls.PORT}/", status_code=307)
            return JSONResponse({"detail": f"Use HTTPS: https://{host}:{tls.PORT} (GET /api/v1/tls for the certificate)",
                                 "https_port": tls.PORT}, status_code=426)
    return await call_next(request)
STATIC = Path(__file__).parent / "static"


# ─────────────────────────────────────────────────────────────────────────────
#  Auth helpers
# ─────────────────────────────────────────────────────────────────────────────
def _key_from(authorization: str | None, k: str | None = None) -> str:
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:].strip()
    return (k or "").strip()


def _ip(request) -> str:
    return getattr(getattr(request, "client", None), "host", "") or ""


from . import access

def _close_device_link(did, code=1000, reason="access policy changed"):
    conn = hub.conns.get(did)
    if conn is not None and hub.loop is not None:
        asyncio.run_coroutine_threadsafe(conn.ws.close(code=code,reason=reason),hub.loop)

WAITING = "Waiting for approval — approve this device on the Ghost Server admin page (Security)"


def _check_device(row, ip: str, secure: bool | None = None) -> None:
    """Shared by the HTTP API and the live link: approval, "used from a new address" alerts, and
    remembering whether the device has moved to HTTPS (shown on the Security tab)."""
    if not row["approved"]:
        raise HTTPException(403, WAITING)
    if secure is not None:
        info = json.loads(row["info"] or "{}")
        if info.get("https") != secure:
            store.touch_device(row["id"], info={"https": secure})
    if ip and store.note_ip(row["id"], ip):
        security.log(f"{row['name']} connected from a new address", ip, "warn", row["id"],
                     alert=f"{row['name']} just connected from a new address ({ip}). If that wasn't you, "
                           "remove it on the admin page.", alert_key="ip:" + row["id"], every=3600)


def device_auth(request: Request, authorization: str | None = Header(None)) -> dict:
    row = store.device_by_key(_key_from(authorization))
    if row is None:
        raise HTTPException(401, "Unknown or revoked device key — pair this device again")
    _check_device(row, _ip(request), request.url.scheme == "https")
    try:
        access.check_route(row["id"],request.method,request.url.path)
    except PermissionError as exc:
        raise HTTPException(403,str(exc))
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
    # the page's own background refresh says X-Ghost-Passive: it mustn't keep an unattended page unlocked
    passive = request.headers.get("x-ghost-passive") == "1"
    if not security.session_ok(request.cookies.get("ghost_admin", ""), touch=not passive):
        raise HTTPException(401, "Admin login needed")
    # another web page can't make your browser change things here: it can't add this header
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-ghost-admin") != "1":
        raise HTTPException(403, "Missing X-Ghost-Admin header")
    return True


def _admin_password_check(pw: str, code: str, ip: str, what: str) -> None:
    """Password (+ two-step code if on) for actions outside the admin page, e.g. adding a
    person on the tablet. Wrong tries count towards the same lockout as the login page."""
    left = security.locked_for("admin", ip)
    if left:
        raise HTTPException(429, f"Too many wrong tries — wait {security.lock_words(left)}")
    stored = str(store.get("admin_password_hash") or "")
    ok = bool(stored) and _pw_ok(pw, stored)
    if ok and security.two_step_on():
        ok = bool(security.check_second_step(code))
    if not ok:
        if security.fail("admin", ip):
            security.log(f"Admin page locked for 15 minutes ({what})", ip, "bad",
                         alert="Too many wrong admin passwords — the admin page is locked for 15 minutes.",
                         alert_key="lock:admin")
        else:
            security.log(f"Wrong admin password or code ({what})", ip, "bad",
                         alert="Someone typed a wrong Ghost admin password.", alert_key="fail:admin")
        raise HTTPException(403, "That isn't the admin password" +
                            (" and code" if security.two_step_on() else ""))
    security.clear("admin", ip)


# ─────────────────────────────────────────────────────────────────────────────
#  Agents
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/v1/tls")
def tls_info():
    """Public: the server's certificate, for devices to pin (see tls.py)."""
    from . import tls
    return tls.info()


@app.get("/ghost-server.crt")
def tls_cert():
    """The certificate as a file, to install on a PC so the browser trusts the admin page."""
    from fastapi.responses import Response
    from . import tls
    if not tls.pem():
        raise HTTPException(404, "HTTPS isn't set up")
    return Response(tls.pem(), media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": "attachment; filename=ghost-server.crt"})


@app.get("/api/v1/theme")
def theme_now(dev: dict = Depends(device_auth)):
    return theme.current()


@app.get("/admin/api/themes")
def admin_themes(_: bool = Depends(admin_auth)):
    """For the admin card: every theme, which holidays exist, and today's."""
    return {"themes": {k: {"name": v["name"], "emoji": v["emoji"]} for k, v in theme.THEMES.items()},
            "holidays": theme.HOLIDAYS, "today": theme.current()}


@app.get("/api/v1/health")
def health():
    return {"ok": True, "app": "ghost-server", "version": __version__, "online": len(hub.conns)}


@app.post("/api/v1/pair")
async def pair(body: dict, request: Request):
    ip = _ip(request)
    left = security.locked_for("pair", ip)
    if left:
        raise HTTPException(429, f"Pairing is paused after too many wrong codes — try again in "
                                 f"{security.lock_words(left)}")
    got = store.redeem_pair_code(str(body.get("code", "")), str(body.get("name", "")),
                                 str(body.get("kind", "agent")), body.get("info") or {})
    if not got:
        if security.fail("pair", ip):
            security.log("Wrong pairing codes, pairing paused for 10 minutes", ip, "bad",
                         alert="Several wrong pairing codes were tried — pairing is paused for 10 minutes.",
                         alert_key="lock:pair")
        else:
            security.log("Wrong pairing code", ip, "bad")
        raise HTTPException(400, "That pairing code is wrong or has expired — make a new one on the admin page")
    security.clear("pair", ip)
    did, key = got
    name = str(body.get("name") or body.get("kind") or "device")[:60]
    store.note_ip(did, ip)
    approved = bool(store.device(did)["approved"])
    if approved:
        security.log(f"{name} paired", ip, "ok", did)
    else:
        security.log(f"{name} asked to join (waiting)", ip, "warn", did,
                     alert=f"{name} wants to join Ghost — approve or reject it on the admin page (Security).",
                     alert_key="join:" + did,
                     # the owner's phone can answer it straight from the notification (after fingerprint)
                     alert_data={"action": "approve_device", "device_id": did, "device_name": name,
                                 "device_kind": str(body.get("kind") or "")[:30], "ip": ip})
    return {"device_id": did, "device_key": key, "assistant_name": store.get("assistant_name"),
            "approved": approved, **({} if approved else {"message": WAITING})}


@app.get("/api/v1/key")
def key_info(dev: dict = Depends(device_auth)):
    """How old this device's key is, and how often the server wants it renewed."""
    d = store.device(dev["id"])
    return {"rotate_days": int(store.get("key_rotate_days") or 30),
            "age_days": round((time.time() - float(d["key_at"] or d["created_at"] or 0)) / 86400, 1)}


@app.post("/api/v1/devices/{did}/approve")
def device_approve_from_owner(did: str, body: dict, request: Request, dev: dict = Depends(device_auth)):
    """Approve or reject a waiting device from the owner's own phone or PC (the app asks for the
    fingerprint / phone PIN first). Only for devices still waiting — never to undo a removal or
    approve something already removed."""
    if access.enabled() and access.role(dev["id"]) != "owner":
        raise HTTPException(403,"Only an owner-profile device can approve devices")
    allowed = [x.strip().lower() for x in str(store.get("approve_from") or "").split(",") if x.strip()]
    ok = (any(a == dev["name"].lower() or a in dev["name"].lower() for a in allowed) if allowed
          else quiet.is_mine(dev["kind"], dev["name"]))
    if access.enabled(): ok = access.role(dev["id"]) == "owner"
    if dev["kind"] not in ("phone", "desktop", "laptop") or not ok:
        raise HTTPException(403, "Only the owner's own phone or PC can approve devices")
    cur = store.device(did)
    if cur is None or cur["revoked"]:
        raise HTTPException(404, "That device has already been removed")
    if cur["approved"]:
        return {"ok": True, "already": True, "name": cur["name"]}
    ip = _ip(request)
    if body.get("approve"):
        store.approve_device(did)
        store.x("UPDATE devices SET key_at = ? WHERE id = ? AND key_at = 0", (time.time(), did))
        security.log(f"{cur['name']} approved from {dev['name']}", ip, "ok", did)
    else:
        store.revoke_device(did)
        security.log(f"{cur['name']} rejected from {dev['name']}", ip, "ok", did)
    return {"ok": True, "approved": bool(body.get("approve")), "name": cur["name"]}


@app.post("/api/v1/key/rotate")
def key_rotate(request: Request, dev: dict = Depends(device_auth)):
    """The device swaps its key for a fresh one (apps do this every key_rotate_days). The old
    key keeps working for a day in case this reply is lost."""
    key = store.rotate_key(dev["id"])
    security.log(f"{dev['name']} renewed its key", _ip(request), "ok", dev["id"])
    return {"device_key": key, "rotate_days": int(store.get("key_rotate_days") or 30)}


@app.get("/api/v1/session")
async def session(dev: dict = Depends(device_auth)):
    d = dict(store.device(dev["id"]))
    creds = await asyncio.to_thread(live.credentials)
    out = {
        "device": {"id": d["id"], "name": d["name"], "kind": d["kind"]},
        "assistant_name": store.get("assistant_name"),
        "owner_name": store.get("owner_name"),
        "system_instruction": prompt.build(d),
        "server_tools": [t for t in tools.declarations("live") if access.tool_allowed(d["id"],t["name"])],
        "access": {"enabled":access.enabled(),"role":access.role(d["id"])},
        "gemini": creds,
        "server_time": time.time(),
        "key": {"rotate_days": int(store.get("key_rotate_days") or 30),
                "age_days": round((time.time() - float(d.get("key_at") or d.get("created_at") or 0)) / 86400, 1)},
    }
    if access.limited(d["id"]) and "api_key" in creds:
        creds.pop("api_key",None)
        creds["error"] = "A short-lived voice token is required for this restricted device."
    if access.limited(d["id"]):
        out["owner_name"] = ""
        if not creds.get("token"):
            out["gemini"] = {"model":creds.get("model"),"voice":creds.get("voice"),
                             "api_version":creds.get("api_version"),"error":"Restricted devices require a short-lived voice token."}
    # Away mode: phones and tablets keep a copy of the Gemini key so they can still
    # talk (with their own tools + web search) when they can't reach the server.
    key = str(store.get("gemini_api_key") or "").strip()
    if not access.limited(d["id"]) and store.get("away_mode") and key and d.get("kind") in ("phone", "tablet"):
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


# ── front door ───────────────────────────────────────────────────────────────
@app.get("/api/v1/door")
def door_history(limit: int = 20, dev: dict = Depends(device_auth)):
    """Who came to the door lately: [{at, camera, summary, picture, kind}] newest first."""
    st = door.status()
    return {"items": door.history(max(1, min(int(limit), 60))), "cameras": st["cameras"], "bells": st["bells"]}


# ── meal planner ─────────────────────────────────────────────────────────────
@app.get("/api/v1/meals")
def meals_week(days: int = 7, dev: dict = Depends(device_auth)):
    return {"days": meals.week(days=max(1, min(int(days), 14)))}


@app.post("/api/v1/meals")
async def meals_set(body: dict, dev: dict = Depends(device_auth)):
    """{"day": "thursday", "title": "Tacos"} (ingredients looked up), or {"day": …, "clear": true}."""
    d = meals.parse_day(str(body.get("day") or "today"))
    if d is None:
        raise HTTPException(400, "Which day?")
    if body.get("clear") or not str(body.get("title") or "").strip():
        await asyncio.to_thread(meals.clear, d)
    else:
        ing = body.get("ingredients")
        await asyncio.to_thread(meals.set_meal, d, str(body["title"]),
                                [str(x) for x in ing] if isinstance(ing, list) else None, dev["name"])
    return {"days": meals.week()}


@app.post("/api/v1/meals/shop")
async def meals_shop(body: dict, dev: dict = Depends(device_auth)):
    """Put the coming week's (or the given days') ingredients on the shopping list."""
    days = [meals.parse_day(x) for x in (body.get("days") or [])] or \
        [meals.today() + __import__("datetime").timedelta(days=i) for i in range(7)]
    r = await asyncio.to_thread(meals.shop, [d for d in days if d], dev["name"])
    return {**r, "days": meals.week()}


_enrol_tickets: dict[str, tuple[str, float]] = {}


@app.post("/api/v1/faces/enroll")
async def faces_enroll(body: dict, request: Request, dev: dict = Depends(device_auth)):
    """Adding a person needs the admin password, so a visitor can't teach Ghost to call them you."""
    # the first photo needs the password (+ code); the rest of that session uses the ticket it gets back,
    # because a two-step code can only be used once
    tk = str(body.get("ticket") or "")
    held = _enrol_tickets.get(tk)
    if not (held and held[0] == dev["id"] and held[1] > time.time()):
        await asyncio.to_thread(_admin_password_check, str(body.get("admin_password") or ""),
                                str(body.get("admin_code") or ""), _ip(request), "adding a person")
        tk = secrets.token_urlsafe(16)
        for k in [k for k, v in _enrol_tickets.items() if v[1] < time.time()]:
            _enrol_tickets.pop(k, None)
        _enrol_tickets[tk] = (dev["id"], time.time() + 600)
    try:
        r = await asyncio.to_thread(faces.enroll, str(body.get("name") or ""), body.get("image") or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"Visual ID isn't working on the server: {e}")
    if r.get("ok"):
        security.log(f"Visual ID: {str(body.get('name') or 'someone')[:40]} added on {dev['name']}", _ip(request), "ok",
                     dev["id"])
    if r.get("ok") and not store.get("faces_enabled"):
        store.put("faces_enabled", True)
    if r.get("ok"):
        person = faces.person(str(body.get("name") or ""))
        if person:
            r["voice_ticket"] = voices.ticket(dev["id"], person["id"])
            r["voice_enrolled"] = any(p["person_id"] == person["id"] and p["device_id"] == dev["id"] for p in voices.profiles())
    return {**r, "ticket": tk}


# Voice ID is household personalisation, not proof of identity for permissions.
@app.post("/api/v1/voices/enroll/start")
async def voice_enroll_start(body: dict, request: Request, dev: dict = Depends(device_auth)):
    name = str(body.get("name") or "").strip()
    person = await asyncio.to_thread(faces.person, name)
    if not person:
        raise HTTPException(400, "Add this person with Visual ID first")
    token = str(body.get("ticket") or "")
    try:
        if not token:
            await asyncio.to_thread(_admin_password_check, str(body.get("admin_password") or ""),
                                    str(body.get("admin_code") or ""), _ip(request), "voice enrolment")
            token = voices.ticket(dev["id"], person["id"])
        return await asyncio.to_thread(voices.begin, token, dev["id"], name)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"Voice ID is unavailable: {exc}")


@app.post("/api/v1/voices/enroll/sample")
async def voice_enroll_sample(body: dict, dev: dict = Depends(device_auth)):
    try:
        if body.get("sample_rate", 16000) != 16000:
            raise ValueError("Use 16 kHz mono PCM")
        return await asyncio.to_thread(voices.enroll, str(body.get("ticket") or ""), dev["id"],
                                       int(body.get("index", -1)), body.get("pcm") or "")
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(503, f"Voice ID is unavailable: {exc}")


@app.post("/api/v1/voices/identify")
async def voice_identify(body: dict, dev: dict = Depends(device_auth)):
    try:
        if body.get("sample_rate", 16000) != 16000:
            raise ValueError("Use 16 kHz mono PCM")
        return await asyncio.to_thread(voices.identify, body.get("pcm") or "", dev["id"])
    except ValueError:
        return {"person": None, "reason": "unclear_audio"}
    except Exception:
        # A missing model never turns an unrecognised voice into a known person.
        return {"person": None, "reason": "unavailable"}


@app.post("/admin/api/people/{pid}/voice/delete")
def voice_delete(pid: int, _: bool = Depends(admin_auth)):
    voices.delete(pid)
    return {"ok": True}


# ── photo frame ──────────────────────────────────────────────────────────────
@app.get("/api/v1/photos")
async def photos_list(dev: dict = Depends(device_auth)):
    return await asyncio.to_thread(photos.listing)


@app.get("/photos/{n}")
async def photo(n: int, k: str = "", w: int = 1600, authorization: str | None = Header(None)):
    if store.device_by_key(_key_from(authorization, k)) is None:
        raise HTTPException(401, "Unknown device key")
    try: access.check_route(store.device_by_key(_key_from(authorization,k))["id"],"GET","/photos/")
    except PermissionError as exc: raise HTTPException(403,str(exc))
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
    if body.get("type") == "presence" and not access.limited(dev["id"]):
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


@app.get("/api/v1/home/entities")
async def home_entities(dev: dict = Depends(device_auth)):
    """What can be pinned as a favourite on a phone."""
    try:
        return await asyncio.to_thread(dashboard.entities)
    except Exception as e:  # noqa: BLE001
        return {"connected": False, "entities": [], "error": str(e)}


@app.get("/api/v1/home/tiles")
async def home_tiles_for(ids: str = "", dev: dict = Depends(device_auth)):
    """Tiles for a phone's favourites (?ids=light.kitchen,scene.movie)."""
    want = [i for i in re.split(r"[,\s]+", ids) if re.fullmatch(r"[a-z_]+\.[A-Za-z0-9_]+", i)]
    try:
        return await asyncio.to_thread(dashboard.tiles_for, want) if want else \
            await asyncio.to_thread(dashboard.home_tiles)
    except Exception as e:  # noqa: BLE001
        return {"connected": False, "tiles": [], "error": str(e)}


@app.post("/api/v1/home/lights-off")
async def home_lights_off(dev: dict = Depends(device_auth)):
    denial = access.authorize_action(dev["id"],"home_lights_off",{})
    if denial: return {"ok":False,"error":denial}
    try:
        return await asyncio.to_thread(dashboard.all_lights_off)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@app.post("/api/v1/home/switch")
async def home_switch(body: dict, dev: dict = Depends(device_auth)):
    """A tap on a home tile. Lights, switches, fans, scenes only — never locks, alarms or doors."""
    eid = str(body.get("entity_id", ""))
    if not re.fullmatch(r"[a-z_]+\.[A-Za-z0-9_]+", eid):
        raise HTTPException(400, "bad entity_id")
    denial = access.authorize_action(dev["id"],"home_tile",{"entity_id":eid,"action":str(body.get("action","toggle"))})
    if denial: return {"ok":False,"error":denial}
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
    device = store.device_by_key(_key_from(authorization,k))
    if device is not None:
        try: access.check_route(device["id"],"GET","/files/")
        except PermissionError as exc: raise HTTPException(403,str(exc))
    ok = device is not None
    if not ok:
        ok = security.session_ok(request.cookies.get("ghost_admin", ""))
    if not ok:
        raise HTTPException(401, "Not allowed")
    f = store.file(fid)
    if not f or not Path(f["path"]).exists():
        raise HTTPException(404, "No such file")
    return FileResponse(f["path"], filename=f["name"])


@app.websocket("/api/v1/agent")
async def agent_ws(ws: WebSocket, key: str = ""):
    row = store.device_by_key(key or _key_from(ws.headers.get("authorization")))
    # accept first so the app sees a close code it understands (a refused handshake is just "HTTP 403")
    if store.get("https_only") and ws.url.scheme == "ws":
        await ws.accept()
        await ws.close(code=4426, reason="use HTTPS (wss) — GET /api/v1/tls")
        return
    if row is None:
        await ws.accept()
        await ws.close(code=4401, reason="bad device key")
        return
    try:
        _check_device(row, _ip(ws), ws.url.scheme == "wss")
    except HTTPException:
        await ws.accept()
        await ws.close(code=4403, reason="waiting for approval")
        return
    await ws.accept()
    conn = AgentConn(device_id=row["id"], name=row["name"], kind=row["kind"], ws=ws,
                     capabilities=json.loads(row["capabilities"] or "[]"))
    hub.attach(conn)
    store.touch_device(row["id"])
    await ws.send_json({"type": "welcome", "device_id": row["id"], "name": row["name"],
                        "assistant_name": store.get("assistant_name")})
    if not access.limited(row["id"]):
        await ws.send_json(quiet.snapshot(row["kind"], row["name"]))
    for snap in (household.timers_snapshot, household.lists_snapshot, household.notes_snapshot, meals.snapshot,
                 theme.current):
        try:
            if access.limited(row["id"]): continue
            await ws.send_json(await asyncio.to_thread(snap))
        except Exception:  # noqa: BLE001
            pass
    try:
        while True:
            msg = await ws.receive_json()
            current_device = store.device(row["id"])
            if current_device is None or current_device["revoked"] or not current_device["approved"]:
                await ws.close(code=4401,reason="device no longer approved")
                break
            kind = msg.get("type")
            if kind == "hello":
                caps = [c for c in (msg.get("capabilities") or []) if isinstance(c, dict) and c.get("name")][:60]
                conn.capabilities, conn.info = caps, msg.get("info") or {}
                store.touch_device(row["id"], capabilities=caps, info=conn.info)
                await ws.send_json({"type": "ready", "server_tools": [t["name"] for t in tools.declarations("live") if access.tool_allowed(row["id"],t["name"])]})
            elif kind == "result":
                hub.resolve(msg, row["id"])
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
    return {"needs_setup": not store.get("admin_password_hash"),
            "logged_in": security.session_ok(request.cookies.get("ghost_admin", ""), touch=False),
            "two_step": security.two_step_on(),
            "lock_minutes": float(store.get("admin_lock_minutes") or 0)}


def _locked_out(ip: str) -> None:
    left = security.locked_for("admin", ip)
    if left:
        raise HTTPException(429, f"Too many wrong tries — the admin page is locked for {security.lock_words(left)}")


def _wrong(ip: str, what: str) -> None:
    if security.fail("admin", ip):
        security.log("Admin page locked for 15 minutes after wrong tries", ip, "bad",
                     alert="Too many wrong admin passwords — the admin page is locked for 15 minutes.",
                     alert_key="lock:admin")
    else:
        security.log(what, ip, "bad", alert="Someone typed a wrong Ghost admin password.", alert_key="fail:admin")
    time.sleep(1)


@app.post("/admin/login")
def admin_login(body: dict, request: Request):
    ip = _ip(request)
    _locked_out(ip)
    pw = str(body.get("password", ""))
    stored = store.get("admin_password_hash")
    how = "password"
    if not stored:
        if len(pw) < 8:
            raise HTTPException(400, "Choose a password of at least 8 characters")
        store.put("admin_password_hash", _pw_hash(pw))
        security.log("Admin password set", ip)
    elif not _pw_ok(pw, stored):
        _wrong(ip, "Wrong admin password")
        raise HTTPException(401, "Wrong password")
    elif security.two_step_on():
        code = str(body.get("code") or "").strip()
        if not code:
            return {"ok": False, "need_code": True}
        how = security.check_second_step(code)
        if not how:
            _wrong(ip, "Wrong 2-step code")
            raise HTTPException(401, "That code isn't right — check the time on your phone, or use a backup code")
        how = "2-step code" if how == "app" else "a backup code"
    security.clear("admin", ip)
    tok = security.new_session(ip, request.headers.get("user-agent", ""))
    security.log(f"Admin signed in with {how}", ip, "ok")
    if how == "a backup code":
        security.notify(f"A backup code was used to sign in to the Ghost admin page. "
                        f"{security.backup_left()} left.")
    r = JSONResponse({"ok": True})
    r.set_cookie("ghost_admin", tok, max_age=security.TTL, httponly=True, samesite="strict",
                 secure=request.url.scheme == "https")
    return r


@app.post("/admin/logout")
def admin_logout(request: Request):
    security.end_session(request.cookies.get("ghost_admin", ""))
    r = JSONResponse({"ok": True})
    r.delete_cookie("ghost_admin")
    return r


@app.post("/admin/api/password")
def admin_password(body: dict, request: Request, _: bool = Depends(admin_auth)):
    ip = _ip(request)
    _locked_out(ip)
    if not _pw_ok(str(body.get("current") or ""), str(store.get("admin_password_hash") or "")):
        _wrong(ip, "Wrong admin password (changing password)")
        raise HTTPException(403, "The current password isn't right")
    new = str(body.get("new") or "")
    if len(new) < 8:
        raise HTTPException(400, "Choose a password of at least 8 characters")
    store.put("admin_password_hash", _pw_hash(new))
    n = security.end_others(request.cookies.get("ghost_admin", ""))
    security.log("Admin password changed", ip, "ok", alert="The Ghost admin password was changed.")
    return {"ok": True, "signed_out": n}


# ── security tab ─────────────────────────────────────────────────────────────
@app.get("/admin/api/security")
def admin_security(request: Request, _: bool = Depends(admin_auth)):
    return security.overview(request.cookies.get("ghost_admin", ""))


@app.post("/admin/api/2fa/start")
def twofa_start(request: Request, _: bool = Depends(admin_auth)):
    return security.start_setup(request.cookies.get("ghost_admin", ""))


@app.post("/admin/api/2fa/enable")
def twofa_enable(body: dict, request: Request, _: bool = Depends(admin_auth)):
    codes = security.finish_setup(request.cookies.get("ghost_admin", ""), str(body.get("code") or ""))
    if codes is None:
        raise HTTPException(400, "That code isn't right — scan the QR code again and type the 6 digits you see")
    security.log("Two-step login turned on", _ip(request), "ok", alert="Two-step login was turned on for the admin page.")
    return {"ok": True, "backup_codes": codes}


def _reauth(body: dict, request: Request, what: str) -> None:
    ip = _ip(request)
    _locked_out(ip)
    ok = _pw_ok(str(body.get("password") or ""), str(store.get("admin_password_hash") or ""))
    if ok and security.two_step_on():
        ok = bool(security.check_second_step(str(body.get("code") or "")))
    if not ok:
        _wrong(ip, f"Wrong password or code ({what})")
        raise HTTPException(403, "Password or code isn't right")


@app.post("/admin/api/2fa/disable")
def twofa_disable(body: dict, request: Request, _: bool = Depends(admin_auth)):
    _reauth(body, request, "turning off 2-step login")
    security.turn_off()
    security.log("Two-step login turned off", _ip(request), "warn",
                 alert="Two-step login was turned OFF for the Ghost admin page.")
    return {"ok": True}


@app.post("/admin/api/2fa/backup-codes")
def twofa_backup(body: dict, request: Request, _: bool = Depends(admin_auth)):
    _reauth(body, request, "new backup codes")
    if not security.two_step_on():
        raise HTTPException(400, "Two-step login is off")
    security.log("New backup codes made (old ones stop working)", _ip(request), "ok")
    return {"ok": True, "backup_codes": security.new_backup_codes()}


@app.post("/admin/api/sessions/others")
def sessions_others(request: Request, _: bool = Depends(admin_auth)):
    n = security.end_others(request.cookies.get("ghost_admin", ""))
    security.log(f"Signed out {n} other browser{'s' if n != 1 else ''}", _ip(request), "ok")
    return {"ok": True, "signed_out": n}


@app.post("/admin/api/sessions/{sid}/end")
def session_end(sid: str, request: Request, _: bool = Depends(admin_auth)):
    full = security.session_by_short(sid)
    if not full or not security.end_session(sid=full):
        raise HTTPException(404, "No such browser")
    security.log("Signed out a browser", _ip(request), "ok")
    return {"ok": True}


@app.get("/admin/api/overview")
def overview(_: bool = Depends(admin_auth)):
    from .tools.tasks import TASKS
    devs = store.devices()
    for d in devs:
        d["access_role"] = access.role(d["id"],configured=True)
        d["online"] = hub.is_online(d["id"])
        if d["online"]:
            d["capabilities"] = hub.conns[d["id"]].capabilities
    return {"settings": store.all_settings(), "devices": devs, "facts": store.facts(), "rules": store.rules(),
            "events": store.recent_events(25), "files": store.files(20), "turns": store.recent_turns(30),
            "tasks": [{"id": k, **{x: v[x] for x in ("goal", "status", "result", "started")}, "steps": len(v["log"])}
                      for k, v in list(TASKS.items())[-10:]],
            "tools": [t["name"] for t in tools.declarations("live")], "version": __version__}


@app.put("/admin/api/settings")
def save_settings(body: dict, request: Request, _: bool = Depends(admin_auth)):
    if "household_access" in body and not isinstance(body["household_access"],bool):
        raise HTTPException(400,"Household access must be on or off")
    changed = []
    for k, v in body.items():
        if k not in store.DEFAULTS or k in store.PROTECTED_KEYS:
            continue
        if k in store.SECRET_KEYS and (v is None or str(v).startswith("•••")):
            continue                      # untouched masked secret
        if store.get(k) != v:
            changed.append(k)
        store.put(k, v)
    if any(k.startswith("theme_") for k in changed):
        theme.push_soon()                 # the new look on every screen now
    if "household_access" in changed:
        access.invalidate()
        for conn in list(hub.conns.values()):
            _close_device_link(conn.device_id)
    if changed:                           # names only — never the values (keys, tokens)
        sec = {"household_access", "approve_new_devices", "security_alerts"}
        off = [k for k in sec & set(changed) if not body.get(k)]
        security.log("Settings changed: " + ", ".join(sorted(changed))[:180], _ip(request),
                     "warn" if off else "ok",
                     alert=("Security setting turned off: " + ", ".join(off)) if off else "")
    if any(k.startswith(("quiet_", "safety_")) for k in body):
        quiet.tick_soon()
    return {"ok": True, "settings": store.all_settings()}



@app.get("/admin/api/action-approvals")
def action_approvals(_:bool=Depends(admin_auth)):
    return {"enabled":access.enabled(),"requests":access.pending()}

@app.post("/admin/api/action-approvals/{token}")
def action_decide(token:str,body:dict,request:Request,_:bool=Depends(admin_auth)):
    if not isinstance(body.get("approve"),bool):raise HTTPException(400,"Choose approve or decline")
    try: access.decide(token,body["approve"])
    except ValueError as exc:raise HTTPException(409,str(exc))
    security.log("Action request "+token+(" approved" if body["approve"] else " declined"),_ip(request))
    return {"ok":True,"executed":False}

@app.post("/admin/api/pair-code")
def pair_code(request: Request, _: bool = Depends(admin_auth)):
    security.log("Pairing code made", _ip(request))
    return {"code": store.new_pair_code(10), "expires_in": 600,
            "needs_approval": bool(store.get("approve_new_devices"))}


@app.post("/admin/api/devices/{did}")
def device_edit(did: str, body: dict, request: Request, _: bool = Depends(admin_auth)):
    cur = store.device(did)
    if cur is None:
        raise HTTPException(404, "No such device")
    if "access_role" in body:
        try: access.set_role(did,str(body["access_role"]))
        except ValueError as exc: raise HTTPException(400,str(exc))
        access.invalidate()
        security.log(f"Device access profile changed to {body['access_role']}",_ip(request),"warn",did)
        _close_device_link(did,reason="access profile changed")
    if body.get("approve") and not body.get("revoke"):
        store.approve_device(did)
        store.x("UPDATE devices SET key_at = ? WHERE id = ? AND key_at = 0", (time.time(), did))
        security.log(f"{cur['name']} approved", _ip(request), "ok", did)
    if body.get("revoke"):
        security.log(f"{cur['name']} {'rejected' if not cur['approved'] else 'removed'}", _ip(request), "ok", did)
        store.revoke_device(did)
        c = hub.conns.get(did)
        if c:
            _close_device_link(did,code=4401,reason="revoked")
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





@app.get("/admin/api/routine-home-options")
async def routine_home_options(_:bool=Depends(admin_auth)):
    from .tools import home
    try:
        def read():
            states=home._req("GET","/api/states").json()
            return [{"id":x["entity_id"],"name":str((x.get("attributes") or {}).get("friendly_name") or x["entity_id"])} for x in states if str(x.get("entity_id","")).split('.')[0] in ("light","fan","media_player")]
        return {"connected":True,"items":await asyncio.to_thread(read)}
    except Exception:return {"connected":False,"items":[],"error":"Home Assistant controls could not be loaded."}

@app.get("/admin/api/routines")
def admin_routines(_:bool=Depends(admin_auth)):
    from . import routines
    return {"routines":routines.definitions(),"runs":routines.runs(),
            "devices":[{"id":d["id"],"name":d["name"],"permitted":bool(d["approved"] and access.tool_allowed(d["id"],"routines"))} for d in store.devices() if d["id"]!="dev_admin"],
            "access_enforced":access.enabled()}

@app.post("/admin/api/routines")
def routine_create(body:dict,request:Request,_:bool=Depends(admin_auth)):
    from . import routines
    try: rid=routines.save(body)
    except (ValueError,PermissionError) as exc:raise HTTPException(400,str(exc))
    security.log("Routine created "+rid,_ip(request));return {"ok":True,"id":rid}

@app.put("/admin/api/routines/{rid}")
def routine_save(rid:str,body:dict,request:Request,_:bool=Depends(admin_auth)):
    from . import routines
    try: routines.save(body,rid)
    except (ValueError,PermissionError) as exc:raise HTTPException(400,str(exc))
    security.log("Routine updated "+rid,_ip(request));return {"ok":True}

@app.delete("/admin/api/routines/{rid}")
def routine_delete(rid:str,request:Request,_:bool=Depends(admin_auth)):
    from . import routines
    try: routines.delete(rid)
    except ValueError as exc:raise HTTPException(404,str(exc))
    security.log("Routine deleted "+rid,_ip(request));return {"ok":True}

@app.post("/admin/api/routines/{rid}/start")
def routine_start(rid:str,body:dict,request:Request,_:bool=Depends(admin_auth)):
    from . import routines
    if set(body)!={"device_id"}:raise HTTPException(400,"Choose the source device")
    try: result=routines.start(rid,body["device_id"])
    except (ValueError,PermissionError) as exc:raise HTTPException(400,str(exc))
    security.log("Routine started "+result["id"],_ip(request),device_id=result["device_id"]);return result

@app.post("/admin/api/routine-runs/{run_id}/{action}")
def routine_control(run_id:str,action:str,request:Request,_:bool=Depends(admin_auth)):
    from . import routines
    if action not in ("resume","cancel"):raise HTTPException(400,"Choose resume or cancel")
    try: result=(routines.resume if action=="resume" else routines.cancel)(run_id)
    except (ValueError,PermissionError) as exc:raise HTTPException(400,str(exc))
    security.log("Routine "+action+" "+run_id,_ip(request),device_id=result["device_id"]);return result

@app.get("/admin/api/personal")
def personal_get(_:bool=Depends(admin_auth)):
    from . import personal
    return personal.snapshot()

@app.post("/admin/api/personal/people")
def personal_create(body:dict,request:Request,_:bool=Depends(admin_auth)):
    from . import personal
    try: person=personal.create(body.get("name",""))
    except ValueError as exc:raise HTTPException(400,str(exc))
    security.log("Personal reply profile created",_ip(request))
    return person

@app.put("/admin/api/personal/people/{pid}")
def personal_save(pid:int,body:dict,request:Request,_:bool=Depends(admin_auth)):
    from . import personal
    try: result=personal.save(pid,body)
    except ValueError as exc:raise HTTPException(400,str(exc))
    security.log("Personal reply preferences updated",_ip(request))
    return {"ok":True,"preferences":result}

@app.delete("/admin/api/personal/people/{pid}")
def personal_reset(pid:int,request:Request,_:bool=Depends(admin_auth)):
    from . import personal
    try: personal.reset(pid)
    except ValueError as exc:raise HTTPException(400,str(exc))
    security.log("Personal reply preferences reset",_ip(request))
    return {"ok":True}

@app.put("/admin/api/personal/devices/{did}")
def personal_assign(did:str,body:dict,request:Request,_:bool=Depends(admin_auth)):
    from . import personal
    if set(body)!={"person_id"}:raise HTTPException(400,"Choose a person or shared/unassigned")
    try: personal.assign(did,body["person_id"])
    except ValueError as exc:raise HTTPException(400,str(exc))
    security.log("Personal reply assignment updated",_ip(request),device_id=did)
    return {"ok":True}

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
    """HTTP on 8780 and HTTPS on 8781, one process (see tls.py)."""
    import uvicorn
    from . import tls
    config.ensure_dirs()
    servers = [uvicorn.Server(uvicorn.Config("ghost_server.app:app", host="0.0.0.0", port=config.PORT,
                                             log_level="info"))]
    try:
        if tls.ensure():
            servers.append(uvicorn.Server(uvicorn.Config(
                "ghost_server.app:app", host="0.0.0.0", port=tls.PORT, log_level="info",
                ssl_certfile=str(tls.cert_path()), ssl_keyfile=str(tls.key_path()))))
            print(f"[Ghost Server] HTTPS on port {tls.PORT} — certificate {tls.fingerprint(True)[:39]}…")
    except Exception as e:  # noqa: BLE001 — HTTPS is extra; never stop the server over it
        print(f"[Ghost Server] HTTPS not started: {e}")

    async def both():
        # one app, two listeners: lifespan (background loops) must only run once
        for extra in servers[1:]:
            extra.config.lifespan = "off"
        await asyncio.gather(*(s.serve() for s in servers))
    asyncio.run(both())


if __name__ == "__main__":
    main()




