"""HTTPS to Ghost Server with its own pinned certificate (see the server's tls.py).

Home Assistant fetches the certificate once from GET /api/v1/tls, keeps it in the config entry,
and from then on talks to https://…:8781 trusting only that certificate.
"""
from __future__ import annotations

import base64
import hashlib
import ssl
from urllib.parse import urlparse

import aiohttp

CONF_TLS_PEM = "tls_pem"


def context(pem: str) -> ssl.SSLContext | None:
    if not pem:
        return None
    ctx = ssl.create_default_context(cadata=pem)
    ctx.check_hostname = False          # the certificate is the pin, whatever address the NAS has
    return ctx


def ssl_for(url: str, pem: str):
    """What to pass as aiohttp's ssl= for this address (True = aiohttp's normal checks)."""
    return (context(pem) or True) if url.startswith("https://") and pem else True


async def upgrade(session: aiohttp.ClientSession, url: str) -> tuple[str, str] | None:
    """(https url, pem) if the server offers HTTPS, else None."""
    plain = url.rstrip("/").replace("https://", "http://", 1)
    try:
        async with session.get(plain + "/api/v1/tls", timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status != 200:
                return None
            info = await r.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return None
    pem = str(info.get("pem") or "")
    if not info.get("enabled") or "BEGIN CERTIFICATE" not in pem:
        return None
    body = "".join(x for x in pem.splitlines() if x and not x.startswith("-----"))
    sha = hashlib.sha256(base64.b64decode(body)).hexdigest().upper()
    if info.get("sha256") and str(info["sha256"]).upper() != sha:
        return None
    host = urlparse(plain).hostname or ""
    return f"https://{host}:{int(info.get('port') or 8781)}", pem
