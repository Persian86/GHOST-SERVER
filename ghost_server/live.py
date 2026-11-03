"""Credentials for an agent's own Gemini Live session.

Preferred: a short-lived, single-use ephemeral token, so the real Gemini key
never leaves the server. Fallback (only if switched on in settings): the key."""
from __future__ import annotations

import datetime as dt

from . import store


def credentials() -> dict:
    key = str(store.get("gemini_api_key") or "").strip()
    out = {"model": store.get("gemini_live_model"), "voice": store.get("gemini_voice"), "api_version": "v1alpha"}
    if not key:
        out["error"] = "No Gemini key on the server — add it on the admin page."
        return out
    try:
        from google import genai
        client = genai.Client(api_key=key, http_options={"api_version": "v1alpha"})
        now = dt.datetime.now(tz=dt.timezone.utc)
        tok = client.auth_tokens.create(config={
            "uses": 1,
            "expire_time": now + dt.timedelta(minutes=30),
            "new_session_expire_time": now + dt.timedelta(minutes=2),
            "http_options": {"api_version": "v1alpha"},
        })
        out.update(token=tok.name, expires=(now + dt.timedelta(minutes=30)).isoformat())
    except Exception as e:
        if store.get("share_gemini_key"):
            out.update(api_key=key, note=f"ephemeral token unavailable ({type(e).__name__}); sharing the key")
        else:
            out["error"] = f"Couldn't make a Gemini session token: {e}"
    return out
