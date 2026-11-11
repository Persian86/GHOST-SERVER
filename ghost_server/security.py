"""Ghost Server security: lockouts, two-step login, admin sessions, the security log and alerts.

Everything here is household-sized and dependency-free (TOTP is RFC 6238 with hmac/sha1, the
same codes Google Authenticator, Microsoft Authenticator, 1Password etc. show).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import threading
import time
from urllib.parse import quote

from . import store

_lock = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
#  Lockouts
# ─────────────────────────────────────────────────────────────────────────────
# scope → (wrong tries allowed, window / lock length in seconds, tries from all addresses together)
LIMITS = {"admin": (5, 15 * 60, 15), "pair": (5, 10 * 60, 15)}
_fails: dict[tuple[str, str], list[float]] = {}
_locked: dict[tuple[str, str], float] = {}


def locked_for(scope: str, ip: str) -> int:
    """Seconds left on a lockout for this address (or for everyone), 0 if none."""
    now = time.time()
    with _lock:
        left = max(_locked.get((scope, ip), 0), _locked.get((scope, "*"), 0)) - now
    return int(left) + 1 if left > 0 else 0


def fail(scope: str, ip: str) -> bool:
    """Count a wrong try. True if this one started a lockout."""
    tries, window, everyone = LIMITS[scope]
    now = time.time()
    started = False
    with _lock:
        for who, limit in ((ip, tries), ("*", everyone)):
            hits = [t for t in _fails.get((scope, who), []) if t > now - window] + [now]
            _fails[(scope, who)] = hits
            if len(hits) >= limit and _locked.get((scope, who), 0) <= now:
                _locked[(scope, who)] = now + window
                _fails[(scope, who)] = []
                started = True
    return started


def clear(scope: str, ip: str) -> None:
    with _lock:
        _fails.pop((scope, ip), None)


def reset_all() -> None:                 # tests
    with _lock:
        _fails.clear()
        _locked.clear()


def lock_words(seconds: int) -> str:
    m = max(1, round(seconds / 60))
    return f"{m} minute{'s' if m != 1 else ''}"


# ─────────────────────────────────────────────────────────────────────────────
#  Two-step login (TOTP + backup codes)
# ─────────────────────────────────────────────────────────────────────────────
_pending_secret: dict[str, str] = {}      # session → secret being set up
_last_step = {"n": 0}                     # a code can't be used twice


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    h = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_now(secret: str, t: float | None = None) -> str:
    return _code(secret, int((t or time.time()) // 30))


def _totp_step(secret: str, code: str, t: float | None = None) -> int:
    """The time step the code belongs to (±30 s for clock drift), or 0."""
    code = "".join(c for c in str(code) if c.isdigit())
    if len(code) != 6 or not secret:
        return 0
    now = int((t or time.time()) // 30)
    for step in (now, now - 1, now + 1):
        if hmac.compare_digest(_code(secret, step), code):
            return step
    return 0


def uri(secret: str, label: str = "Ghost Server") -> str:
    owner = str(store.get("owner_name") or "admin")
    return (f"otpauth://totp/{quote(label)}:{quote(owner)}?secret={secret}"
            f"&issuer={quote(label)}&algorithm=SHA1&digits=6&period=30")


def qr_svg(text: str) -> str:
    try:
        import segno
        return segno.make(text, error="m").svg_inline(scale=5, dark="#16141F", light="#FFFFFF", border=2)
    except Exception:  # noqa: BLE001 — the page falls back to showing the setup key
        return ""


def two_step_on() -> bool:
    return bool(store.get("admin_totp_secret"))


def _bhash(code: str) -> str:
    return hashlib.sha256(("ghost-backup:" + code.replace("-", "").lower()).encode()).hexdigest()


def new_backup_codes(n: int = 8) -> list[str]:
    codes = ["-".join(secrets.token_hex(2) for _ in range(2)) for _ in range(n)]
    store.put("admin_backup_codes", [_bhash(c) for c in codes])
    return codes


def backup_left() -> int:
    return len(store.get("admin_backup_codes") or [])


def check_second_step(code: str) -> str:
    """'' if the code is wrong; 'app' or 'backup' if right (a backup code is used up)."""
    code = str(code or "").strip()
    secret = str(store.get("admin_totp_secret") or "")
    with _lock:
        step = _totp_step(secret, code)
        if step and step > _last_step["n"]:
            _last_step["n"] = step
            return "app"
        if step:
            return ""                     # that exact code was already used
        codes = list(store.get("admin_backup_codes") or [])
        h = _bhash(code)
        if h in codes:
            codes.remove(h)
            store.put("admin_backup_codes", codes)
            return "backup"
    return ""


def start_setup(session: str) -> dict:
    secret = new_secret()
    _pending_secret[session] = secret
    u = uri(secret)
    return {"secret": secret, "uri": u, "qr": qr_svg(u)}


def finish_setup(session: str, code: str) -> list[str] | None:
    secret = _pending_secret.get(session, "")
    step = _totp_step(secret, code)
    if not step:
        return None
    _pending_secret.pop(session, None)
    _last_step["n"] = step
    store.put("admin_totp_secret", secret)
    store.put("admin_totp_since", time.time())
    return new_backup_codes()


def turn_off() -> None:
    store.put("admin_totp_secret", "")
    store.put("admin_totp_since", 0)
    store.put("admin_backup_codes", [])


# ─────────────────────────────────────────────────────────────────────────────
#  Admin sessions
# ─────────────────────────────────────────────────────────────────────────────
TTL = 7 * 86400
_sessions: dict[str, dict] = {}            # sha256(token) → info


def _sid(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(ip: str, ua: str) -> str:
    tok = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        _sessions[_sid(tok)] = {"created": now, "seen": now, "exp": now + TTL, "ip": ip, "ua": ua[:200]}
    return tok


def lock_seconds() -> float:
    """Auto-lock: seconds of no use before the admin page locks (0 = off)."""
    try:
        return max(0.0, float(store.get("admin_lock_minutes") or 0)) * 60
    except (TypeError, ValueError):
        return 0.0


def session_ok(token: str, touch: bool = True) -> bool:
    if not token:
        return False
    sid = _sid(token)
    now = time.time()
    idle = lock_seconds() or float(store.get("admin_idle_hours") or 12) * 3600
    with _lock:
        s = _sessions.get(sid)
        if not s:
            return False
        if s["exp"] < now or s["seen"] + idle < now:
            _sessions.pop(sid, None)
            return False
        if touch:
            s["seen"] = now
    return True


def end_session(token: str = "", sid: str = "") -> bool:
    with _lock:
        return _sessions.pop(sid or _sid(token), None) is not None


def end_others(token: str) -> int:
    keep = _sid(token)
    with _lock:
        gone = [k for k in _sessions if k != keep]
        for k in gone:
            _sessions.pop(k, None)
    return len(gone)


def end_all() -> None:
    with _lock:
        _sessions.clear()


def browser_name(ua: str) -> str:
    ua = ua or ""
    b = next((n for k, n in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                             ("Safari/", "Safari")) if k in ua), "Browser")
    o = next((n for k, n in (("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                             ("Mac OS", "Mac"), ("Linux", "Linux")) if k in ua), "")
    return f"{b} on {o}" if o else b


def sessions(current: str = "") -> list[dict]:
    cur = _sid(current) if current else ""
    with _lock:
        items = list(_sessions.items())
    return sorted(({"id": k[:16], "this": k == cur, "browser": browser_name(v["ua"]), "ip": v["ip"],
                    "seen": v["seen"], "created": v["created"]} for k, v in items),
                  key=lambda s: (not s["this"], -s["seen"]))


def session_by_short(short: str) -> str:
    with _lock:
        return next((k for k in _sessions if k.startswith(short) and len(short) >= 8), "")


# ─────────────────────────────────────────────────────────────────────────────
#  Security log + alerts
# ─────────────────────────────────────────────────────────────────────────────
_alerted: dict[str, float] = {}


def log(what: str, ip: str = "", level: str = "ok", device_id: str = "",
        alert: str = "", alert_key: str = "", every: float = 600, alert_data: dict | None = None) -> None:
    """Write to the security log; `alert` (if set) goes to the owner's phone, at most once per
    `every` seconds for the same alert_key, so a flood of wrong passwords is one alert."""
    try:
        store.add_security_event(what, ip, level, device_id)
    except Exception as e:  # noqa: BLE001
        print(f"[Ghost Server] security log: {e}")
    if alert and store.get("security_alerts"):
        k = alert_key or what
        now = time.time()
        if _alerted.get(k, 0) + every > now:
            return
        _alerted[k] = now
        notify(alert, data=alert_data)


def notify(text: str, title: str = "Ghost security", data: dict | None = None) -> int:
    """Send an alert to the owner's own devices (their phone first). It isn't a "say", so
    Do Not Disturb doesn't hold it — the apps show it as a notification."""
    from . import quiet
    from .hub import hub
    msg = {"type": "notify", "title": title, "text": text, "level": "security", "urgent": True, **(data or {})}
    targets = [c for c in hub.online() if quiet.is_mine(c.kind, c.name)]
    phones = [c for c in targets if c.kind == "phone"]
    sent = 0
    for c in phones or targets:
        if hub.loop is None:
            break
        try:
            import asyncio
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is hub.loop:
                asyncio.ensure_future(hub.send(c.device_id, msg))
            else:
                asyncio.run_coroutine_threadsafe(hub.send(c.device_id, msg), hub.loop)
            sent += 1
        except Exception:  # noqa: BLE001
            pass
    return sent


def ago(t: float) -> str:
    s = time.time() - float(t or 0)
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    d = int(s // 86400)
    return "yesterday" if d == 1 else f"{d} days ago"


def _tls() -> dict:
    from . import tls
    return {"enabled": bool(tls.pem()), "port": tls.PORT, "fingerprint": tls.fingerprint(True)}


def overview(current_token: str = "") -> dict:
    week = time.time() - 7 * 86400
    events = store.security_events(60)
    pending = [d for d in store.devices() if not d.get("approved", 1)]
    keys = [{"id": d["id"], "name": d["name"], "kind": d["kind"], "https": bool((d.get("info") or {}).get("https")),
             "renewed": d.get("key_at") or d.get("created_at") or 0,
             "renewed_text": ago(d.get("key_at") or d.get("created_at") or 0)}
            for d in store.devices() if d.get("approved", 1)]
    return {
        "two_step": {"on": two_step_on(), "since": store.get("admin_totp_since") or 0, "backup_left": backup_left()},
        "lockouts_7d": len([e for e in store.security_events(500, week) if "locked" in e["what"].lower()
                            or "paused" in e["what"].lower()]),
        "sessions": sessions(current_token),
        "pending": [{"id": d["id"], "name": d["name"], "kind": d["kind"], "ip": (d.get("ips") or [""])[-1],
                     "asked": ago(d.get("created_at") or 0)} for d in pending],
        "keys": keys,
        "settings": {k: store.get(k) for k in ("household_access", "approve_new_devices", "security_alerts", "key_rotate_days",
                                                "admin_idle_hours", "https_only", "approve_from",
                                                "admin_lock_minutes")},
        "tls": _tls(),
        "log": events,
    }

