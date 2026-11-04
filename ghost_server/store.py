"""
Everything Ghost Server remembers, in one SQLite file (/data/ghost.db).

    settings     key → JSON value (API keys, integration addresses, owner name…)
    devices      paired agents: id, name, kind, key hash, capabilities, last seen
    pair_codes   short-lived pairing codes shown on the admin page
    facts        what Ghost knows about the household (shared by every agent)
    rules        standing instructions ("always use Celsius")
    turns        conversation log posted by agents (for summaries and recall)
    events       things agents report: notifications, battery, sensor changes
    files        documents Ghost made, downloadable from any device
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from . import config

_local = threading.local()
_init_lock = threading.Lock()
_db_path: Path | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY, name TEXT, kind TEXT, key_hash TEXT UNIQUE,
    capabilities TEXT DEFAULT '[]', info TEXT DEFAULT '{}',
    created_at REAL, last_seen REAL, revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS pair_codes (code TEXT PRIMARY KEY, expires REAL, kind TEXT);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, section TEXT, text TEXT, source TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS rules (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, role TEXT, text TEXT, at REAL);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, type TEXT, data TEXT, at REAL);
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY, name TEXT, path TEXT, device_id TEXT, created_at REAL);
CREATE INDEX IF NOT EXISTS turns_at ON turns(at);
CREATE INDEX IF NOT EXISTS events_at ON events(at);
"""


def init(path: Path | None = None) -> None:
    """Open (and create) the database. Tests pass a temporary path."""
    global _db_path
    with _init_lock:
        config.ensure_dirs() if path is None else path.parent.mkdir(parents=True, exist_ok=True)
        _db_path = path or config.DB_PATH
        _local.__dict__.clear()
        con = _con()
        con.executescript(SCHEMA)
        con.commit()


def _con() -> sqlite3.Connection:
    con = getattr(_local, "con", None)
    if con is None or getattr(_local, "path", None) != _db_path:
        if _db_path is None:
            init()
        con = sqlite3.connect(str(_db_path), timeout=15, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        _local.con, _local.path = con, _db_path
    return con


def q(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    return _con().execute(sql, args).fetchall()


def x(sql: str, args: tuple = ()) -> int:
    con = _con()
    cur = con.execute(sql, args)
    con.commit()
    return cur.lastrowid


# ── settings ─────────────────────────────────────────────────────────────────
DEFAULTS: dict[str, Any] = {
    "owner_name": "",
    "assistant_name": "Ghost",
    "city": "Melbourne",
    "news_country": "Australia",
    "timezone": "Australia/Melbourne",
    "anthropic_api_key": "",
    "claude_model": "claude-sonnet-5-5",
    "claude_fast_model": "claude-haiku-5-5",
    "gemini_api_key": "",
    "gemini_live_model": "gemini-3.1-flash-live-preview",
    "gemini_voice": "Fenrir",
    "share_gemini_key": False,          # fallback when ephemeral tokens fail
    "away_mode": False,
    # phone presence automations: plain-English instructions Ghost carries out (Claude + server tools)
    "tablet_entities": "",              # Ghost Hub tiles: Home Assistant entity ids (empty = pick automatically)
    "on_left_home": "",
    "on_approaching_home": "",
    "on_arrived_home": "",                 # give phones/tablets the Gemini key so they keep working away from home
    "ha_url": "http://192.168.80.244:8123",
    "ha_token": "",
    "marquee_url": "http://192.168.80.45:8420",
    "marquee_key": "",
    "calendars": "",
    "admin_password_hash": "",
}
SECRET_KEYS = {"anthropic_api_key", "gemini_api_key", "ha_token", "marquee_key", "calendars",
               "admin_password_hash"}


def get(key: str, default: Any = None) -> Any:
    rows = q("SELECT v FROM settings WHERE k = ?", (key,))
    if rows:
        try:
            return json.loads(rows[0]["v"])
        except Exception:
            return rows[0]["v"]
    return DEFAULTS.get(key, default) if default is None else default


def put(key: str, value: Any) -> None:
    x("INSERT OR REPLACE INTO settings (k, v) VALUES (?, ?)", (key, json.dumps(value)))


def all_settings(reveal: bool = False) -> dict:
    out = dict(DEFAULTS)
    for r in q("SELECT k, v FROM settings"):
        try:
            out[r["k"]] = json.loads(r["v"])
        except Exception:
            out[r["k"]] = r["v"]
    if not reveal:
        for k in SECRET_KEYS:
            v = str(out.get(k) or "")
            out[k] = ("•••" + v[-4:]) if v else ""
    out.pop("admin_password_hash", None)
    return out


# ── devices ──────────────────────────────────────────────────────────────────
def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_pair_code(minutes: int = 10) -> str:
    code = "".join(secrets.choice("23456789") for _ in range(6))
    x("DELETE FROM pair_codes WHERE expires < ?", (time.time(),))
    x("INSERT OR REPLACE INTO pair_codes (code, expires, kind) VALUES (?, ?, ?)",
      (code, time.time() + minutes * 60, "device"))
    return code


def redeem_pair_code(code: str, name: str, kind: str, info: dict | None = None) -> tuple[str, str] | None:
    """(device_id, device_key) or None. The key is shown to the device ONCE."""
    rows = q("SELECT * FROM pair_codes WHERE code = ? AND expires > ?", (str(code).strip(), time.time()))
    if not rows:
        return None
    x("DELETE FROM pair_codes WHERE code = ?", (code,))
    device_id = "dev_" + secrets.token_hex(5)
    key = "gk_" + secrets.token_urlsafe(32)
    x("INSERT INTO devices (id, name, kind, key_hash, info, created_at, last_seen) VALUES (?,?,?,?,?,?,?)",
      (device_id, (name or kind or "device")[:60], (kind or "agent")[:30], _hash(key),
       json.dumps(info or {}), time.time(), time.time()))
    return device_id, key


def device_by_key(key: str) -> sqlite3.Row | None:
    if not key:
        return None
    rows = q("SELECT * FROM devices WHERE key_hash = ? AND revoked = 0", (_hash(key),))
    return rows[0] if rows else None


def device(device_id: str) -> sqlite3.Row | None:
    rows = q("SELECT * FROM devices WHERE id = ?", (device_id,))
    return rows[0] if rows else None


def devices(include_revoked: bool = False) -> list[dict]:
    rows = q("SELECT * FROM devices" + ("" if include_revoked else " WHERE revoked = 0") + " ORDER BY created_at")
    out = []
    for r in rows:
        d = dict(r)
        d.pop("key_hash", None)
        d["capabilities"] = json.loads(d.get("capabilities") or "[]")
        d["info"] = json.loads(d.get("info") or "{}")
        out.append(d)
    return out


def touch_device(device_id: str, capabilities: list | None = None, info: dict | None = None) -> None:
    if capabilities is not None:
        x("UPDATE devices SET capabilities = ?, last_seen = ? WHERE id = ?",
          (json.dumps(capabilities), time.time(), device_id))
    if info is not None:
        cur = device(device_id)
        merged = json.loads(cur["info"] or "{}") if cur else {}
        merged.update(info)
        x("UPDATE devices SET info = ? WHERE id = ?", (json.dumps(merged), device_id))
    x("UPDATE devices SET last_seen = ? WHERE id = ?", (time.time(), device_id))


def rename_device(device_id: str, name: str) -> None:
    x("UPDATE devices SET name = ? WHERE id = ?", (name[:60], device_id))


def revoke_device(device_id: str) -> None:
    x("UPDATE devices SET revoked = 1 WHERE id = ?", (device_id,))


# ── memory ───────────────────────────────────────────────────────────────────
def add_fact(text: str, section: str = "Notes", source: str = "") -> bool:
    text = " ".join(str(text).split())[:500]
    if not text or q("SELECT 1 FROM facts WHERE lower(text) = lower(?)", (text,)):
        return False
    x("INSERT INTO facts (section, text, source, created_at) VALUES (?,?,?,?)",
      ((section or "Notes").title()[:40], text, source, time.time()))
    return True


def facts() -> list[dict]:
    return [dict(r) for r in q("SELECT * FROM facts ORDER BY section, id")]


def forget_facts(match: str) -> int:
    rows = q("SELECT id FROM facts WHERE lower(text) LIKE ?", (f"%{match.lower()}%",))
    for r in rows:
        x("DELETE FROM facts WHERE id = ?", (r["id"],))
    return len(rows)


def add_rule(text: str) -> bool:
    text = " ".join(str(text).split())[:300]
    if not text or q("SELECT 1 FROM rules WHERE lower(text) = lower(?)", (text,)):
        return False
    x("INSERT INTO rules (text, created_at) VALUES (?, ?)", (text, time.time()))
    return True


def rules() -> list[dict]:
    return [dict(r) for r in q("SELECT * FROM rules ORDER BY id")]


def remove_rules(match: str) -> int:
    rows = q("SELECT id FROM rules WHERE lower(text) LIKE ? OR id = ?",
             (f"%{match.lower()}%", match if str(match).isdigit() else -1))
    for r in rows:
        x("DELETE FROM rules WHERE id = ?", (r["id"],))
    return len(rows)


# ── turns & events ───────────────────────────────────────────────────────────
def add_turn(device_id: str, role: str, text: str) -> None:
    if text and text.strip():
        x("INSERT INTO turns (device_id, role, text, at) VALUES (?,?,?,?)",
          (device_id, role[:12], text[:4000], time.time()))


def recent_turns(limit: int = 20, device_id: str | None = None) -> list[dict]:
    if device_id:
        rows = q("SELECT * FROM turns WHERE device_id = ? ORDER BY id DESC LIMIT ?", (device_id, limit))
    else:
        rows = q("SELECT * FROM turns ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in reversed(rows)]


def add_event(device_id: str, type_: str, data: dict) -> None:
    x("INSERT INTO events (device_id, type, data, at) VALUES (?,?,?,?)",
      (device_id, type_[:40], json.dumps(data)[:20000], time.time()))
    x("DELETE FROM events WHERE at < ?", (time.time() - 7 * 86400,))


def recent_events(limit: int = 30, type_: str | None = None) -> list[dict]:
    if type_:
        rows = q("SELECT * FROM events WHERE type = ? ORDER BY id DESC LIMIT ?", (type_, limit))
    else:
        rows = q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        d = dict(r)
        d["data"] = json.loads(d["data"] or "{}")
        out.append(d)
    return out


# ── files ────────────────────────────────────────────────────────────────────
def add_file(name: str, path: Path, device_id: str = "") -> str:
    fid = secrets.token_urlsafe(9)
    x("INSERT INTO files (id, name, path, device_id, created_at) VALUES (?,?,?,?,?)",
      (fid, name, str(path), device_id, time.time()))
    return fid


def file(fid: str) -> dict | None:
    rows = q("SELECT * FROM files WHERE id = ?", (fid,))
    return dict(rows[0]) if rows else None


def files(limit: int = 50) -> list[dict]:
    return [dict(r) for r in q("SELECT * FROM files ORDER BY created_at DESC LIMIT ?", (limit,))]
