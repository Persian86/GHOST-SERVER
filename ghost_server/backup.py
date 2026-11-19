"""
Settings backup & transfer — Security page → "Backup & transfer".

Export: every setting and key on this Ghost Server (Anthropic, Gemini, Home
Assistant, Marquee, calendars, cameras, local AI, themes, Do Not Disturb, …)
in ONE file, locked with a password the owner chooses. Import: that file +
password into another Ghost Server — a new PC, a rebuilt container, a newer
version — and everything is back without retyping a single key.

THE FILE
    JSON, so it's obviously a Ghost file and survives email/USB/cloud drives:
        {"format": "ghost-server-settings", "version": 1,
         "kdf": {"name": "scrypt", "n": 32768, "r": 8, "p": 1, "salt": …},
         "nonce": …, "data": …}
    `data` is AES-256-GCM over the settings JSON; the key comes from the
    password through scrypt, so a stolen file is useless without it. The
    header is authenticated too (it's the GCM associated data), so nobody can
    quietly weaken the KDF settings on a file.

WHAT'S LEFT OUT, ON PURPOSE
    The admin password and two-step login (PROTECTED_KEYS) — each server keeps
    its own sign-in, and a backup file must never be a way in. Paired devices
    aren't settings either: they pair with the new server as usual.

IMPORTING INTO A DIFFERENT VERSION
    Settings this server doesn't know (from a newer version) are skipped and
    listed; settings the file doesn't have keep this server's values. So a
    backup moves forwards and backwards between versions.
"""
from __future__ import annotations

import base64
import json
import os
import time

from . import __version__, store

FORMAT = "ghost-server-settings"
VERSION = 1
MAX_FILE_BYTES = 4 * 1024 * 1024
MIN_PASSWORD = 8
_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}


class BackupError(Exception):
    """A problem the owner can fix (wrong password, wrong file…) — shown as is."""


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode(), validate=True)


def _key(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))


def _header_bytes(header: dict) -> bytes:
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode()


def stored_settings() -> dict:
    """Exactly what this server has saved (not the defaults), minus sign-in secrets."""
    out = {}
    for row in store.q("SELECT k, v FROM settings"):
        if row["k"] in store.PROTECTED_KEYS:
            continue
        try:
            out[row["k"]] = json.loads(row["v"])
        except Exception:
            out[row["k"]] = row["v"]
    return out


def export(password: str) -> tuple[str, int]:
    """(file text, number of settings in it)."""
    if len(password or "") < MIN_PASSWORD:
        raise BackupError(f"Choose a backup password of at least {MIN_PASSWORD} characters.")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    settings = stored_settings()
    payload = json.dumps({"settings": settings, "exported_at": int(time.time()),
                          "server_version": __version__}).encode()
    salt, nonce = os.urandom(16), os.urandom(12)
    header = {"format": FORMAT, "version": VERSION,
              "kdf": dict(name="scrypt", salt=_b64(salt), **_SCRYPT)}
    sealed = AESGCM(_key(password, salt, **_SCRYPT)).encrypt(nonce, payload, _header_bytes(header))
    doc = dict(header, nonce=_b64(nonce), data=_b64(sealed),
               note="Ghost Server settings backup (encrypted). Import it on the Ghost admin page: "
                    "Security → Backup & transfer.")
    return json.dumps(doc, indent=1), len(settings)


def read(text: str, password: str) -> dict:
    """Decrypts a backup file → its settings dict. Raises BackupError on any problem."""
    if len(text or "") > MAX_FILE_BYTES:
        raise BackupError("That file is far too big to be a Ghost settings backup.")
    try:
        doc = json.loads(text)
    except Exception:
        raise BackupError("That isn't a Ghost settings backup (it isn't the right kind of file).")
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise BackupError("That isn't a Ghost settings backup.")
    if int(doc.get("version") or 0) > VERSION:
        raise BackupError("That backup was made by a newer Ghost Server — update this one first.")
    kdf = doc.get("kdf") or {}
    try:
        n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
        salt, nonce, data = _unb64(kdf["salt"]), _unb64(doc["nonce"]), _unb64(doc["data"])
    except Exception:
        raise BackupError("That backup file is damaged.")
    # Refuse silly KDF settings: too weak to trust, or big enough to stall the server.
    if kdf.get("name") != "scrypt" or not (2 ** 14 <= n <= 2 ** 20) or not (1 <= r <= 16) or not (1 <= p <= 4):
        raise BackupError("That backup file is damaged.")
    header = {"format": doc["format"], "version": doc["version"], "kdf": kdf}
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        plain = AESGCM(_key(password or "", salt, n, r, p)).decrypt(nonce, data, _header_bytes(header))
    except InvalidTag:
        raise BackupError("Wrong backup password (or the file has been changed).")
    try:
        settings = json.loads(plain)["settings"]
        assert isinstance(settings, dict)
    except Exception:
        raise BackupError("That backup file is damaged.")
    return settings


def plan(settings: dict) -> tuple[dict, list[str]]:
    """Which settings this server can take ({key: value}), and which it can't (names)."""
    usable, skipped = {}, []
    for k, v in settings.items():
        if k in store.PROTECTED_KEYS or k not in store.DEFAULTS:
            skipped.append(k)
        else:
            usable[k] = v
    return usable, sorted(skipped)
