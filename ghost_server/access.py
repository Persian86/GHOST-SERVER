"""Opt-in device access ceilings and exact, one-use owner approvals.

Roles belong to paired device credentials, never a spoken name/face/voice result.
"""
from __future__ import annotations
import hashlib
import json
import secrets
import threading
import time
from . import store

ROLES = ("owner", "adult", "child", "guest")
PUBLIC_TOOLS = {"weather", "news", "think_deeply"}
PUBLIC_ROUTES = {("GET", "/api/v1/session"), ("GET", "/api/v1/key"),
                 ("POST", "/api/v1/key/rotate"),
                 ("POST", "/api/v1/tools/call"), ("POST", "/api/v1/chat"),
                 ("POST", "/api/v1/assist"), ("POST", "/api/v1/llm"),
                 ("POST", "/api/v1/turns"), ("POST", "/api/v1/events")}
SCHEMA = """CREATE TABLE IF NOT EXISTS device_access(device_id TEXT PRIMARY KEY,role TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS action_approvals(
 id TEXT PRIMARY KEY,device_id TEXT NOT NULL,role TEXT NOT NULL,digest TEXT NOT NULL,
 operation TEXT NOT NULL,arguments TEXT NOT NULL,expires REAL NOT NULL,status TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS approval_lookup ON action_approvals(device_id,digest,status);"""
_lock = threading.RLock()


def schema(): store._con().executescript(SCHEMA)
def enabled(): return store.get("household_access") is True
def parent(did): return str(did or "").split("~", 1)[0]


def role(did, configured=False):
    if not configured and not enabled(): return "adult"
    schema()
    rows = store.q("SELECT role FROM device_access WHERE device_id=?", (parent(did),))
    return (rows[0]["role"] if rows[0]["role"] in ROLES else "guest") if rows else "adult"


def limited(did): return bool(did) and enabled() and role(did) in ("child", "guest")


def set_role(did, value):
    if value not in ROLES: raise ValueError("Choose owner, adult, child or guest")
    with _lock:
        schema()
        row = store.device(did)
        if row is None or row["revoked"]: raise ValueError("No active device")
        store.x("INSERT INTO device_access VALUES(?,?) ON CONFLICT(device_id) DO UPDATE SET role=excluded.role", (did, value))
        store.x("DELETE FROM action_approvals WHERE device_id=?", (did,))


def initial(did): set_role(did, "guest" if enabled() else "adult")


def invalidate():
    with _lock:
        schema();store.x("DELETE FROM action_approvals")
    # Old camera links must not become a route around a newly restricted profile.
    from . import cameras
    with cameras._vlock: cameras._views.clear()


def check_route(did, method, path):
    if limited(did) and (method, path) not in PUBLIC_ROUTES:
        raise PermissionError("This device's household access profile does not allow that feature")


def tool_allowed(did, name):
    if enabled() and did:
        row = store.device(parent(did))
        if row is None or row["revoked"] or not row["approved"]: return False
    return not limited(did) or name in PUBLIC_TOOLS


def outgoing_allowed(did, message):
    if enabled():
        row = store.device(parent(did))
        if row is None or row["revoked"] or not row["approved"]: return False
    return (not limited(did) or message.get("type") in {"welcome", "ready", "pong"}
            or message.get("safety") is True)


def _clean():
    schema();store.x("DELETE FROM action_approvals WHERE expires<=?", (time.time(),))


def authorize_action(did, operation, arguments):
    """None means allowed. Text means denied/pending; callers MUST NOT act then."""
    if not enabled() or not did: return None  # trusted server/admin-authored internal work
    did = parent(did)
    with _lock:
        row = store.device(did)
        if row is None or row["revoked"] or not row["approved"]:
            return "Permission denied: this device is no longer approved."
        if limited(did): return "Permission denied by this device's household access profile."
        raw = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        if len(raw.encode()) > 16384: return "Action too large for owner approval."
        digest = hashlib.sha256((operation + "\0" + raw).encode()).hexdigest()
        _clean()
        rows = store.q("SELECT * FROM action_approvals WHERE device_id=? AND digest=? AND role=? ORDER BY expires DESC", (did, digest, role(did)))
        for approval in rows:
            if approval["status"] == "approved":
                con = store._con()
                used = con.execute("UPDATE action_approvals SET status='used' WHERE id=? AND status='approved' AND expires>?",
                                   (approval["id"], time.time())).rowcount
                con.commit()
                if used: return None
            if approval["status"] == "pending":
                return f"OWNER APPROVAL NEEDED: request {approval['id']}. Review it on Ghost Server → Security, then retry this exact action."
            if approval["status"] == "denied": return "The owner declined this action. Try again after the request expires."
        if len(store.q("SELECT id FROM action_approvals WHERE status='pending'")) >= 100:
            return "Too many pending owner approvals. Review them on the Security page."
        token = "ap_" + secrets.token_hex(10)
        store.x("INSERT INTO action_approvals VALUES(?,?,?,?,?,?,?,?)",
                (token, did, role(did), digest, operation, raw, time.time()+300, "pending"))
        return f"OWNER APPROVAL NEEDED: request {token}. Review it on Ghost Server → Security, then retry this exact action."


def pending():
    with _lock:
        _clean()
        return [dict(r) for r in store.q("SELECT a.id,a.device_id,d.name,a.operation,a.arguments,a.expires FROM action_approvals a JOIN devices d ON d.id=a.device_id WHERE a.status='pending' AND d.revoked=0 ORDER BY a.expires")]


def decide(token, approve):
    with _lock:
        _clean()
        rows = store.q("SELECT * FROM action_approvals WHERE id=? AND status='pending'", (token,))
        if not rows: raise ValueError("This request expired or was already reviewed")
        item = rows[0];device = store.device(item["device_id"])
        if not enabled() or device is None or device["revoked"] or not device["approved"] or role(device["id"]) != item["role"]:
            raise ValueError("This device or its access profile changed")
        store.x("UPDATE action_approvals SET status=? WHERE id=? AND status='pending'", ("approved" if approve else "denied", token))
