"""Explicit, fail-closed handoff to the requesting tablet's signed-in Marquee player."""
from __future__ import annotations

import json
import re
from . import access, store
from .hub import hub

TOKEN = re.compile(r"[A-Za-z0-9_:.-]{1,240}\Z")
CLIENT = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
HERE = re.compile(r"\s+(?:here|on this tablet|on this screen)\s*[.!?]?\s*$", re.I)


def native(device: str, action: str, token: str, **kwargs) -> dict:
    reply = hub.call_sync(device, "marquee_player", {"action": action, "voice_call_id": token, **kwargs}, timeout=12 if action == "prepare" else 5)
    if not reply or not reply.get("ok"):
        raise RuntimeError("The tablet's Marquee player didn't answer. Open Ghost's Marquee page and sign in first.")
    result = json.loads(reply.get("result") or "{}")
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(result.get("error", "This tablet's Marquee player is not ready.") if isinstance(result, dict) else "Invalid player response.")
    return result


def command(text: str, ctx, request) -> str:
    device = access.parent(ctx.device_id)
    row = store.device(device)
    token = ctx.extra.get("request_id", "")
    if not row or row["kind"] != "tablet" or not access.tool_allowed(ctx.device_id, "marquee"):
        return "Play here is available from Ghost voice on the tablet. No other screen was selected."
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        return "Update the tablet app and use voice there to play on this tablet. No other screen was selected."
    sent = False
    try:
        capabilities = request("GET", "/ext/v1/assistant/capabilities")
        capabilities.raise_for_status()
        caps = capabilities.json()
        if caps.get("strictClientTarget") != 1 or caps.get("ghostHold") != 1:
            return "Update Marquee before using play here. No playback command was sent."
        prepared = native(device, "prepare", token)
        client = prepared.get("client_id", "")
        connection = prepared.get("connection_id", "")
        if not isinstance(client, str) or not CLIENT.fullmatch(client) or not isinstance(connection, str) or not CLIENT.fullmatch(connection):
            raise RuntimeError("The tablet returned an invalid Marquee screen identity.")
        # Mark uncertain before the HTTP call: a timeout may occur after Marquee accepted it.
        sent = True
        response = request("POST", "/ext/v1/assistant", json={
            "text": HERE.sub("", text).strip(), "targetClientId": client, "targetConnectionId": connection, "voiceCallId": token})
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            native(device, "cancel", token)
            return result.get("speech") or "Marquee could not accept that request."
        request_id = (result.get("action") or {}).get("requestId")
        if not isinstance(request_id, str) or not TOKEN.fullmatch(request_id):
            native(device, "cancel", token)
            return result.get("speech") or "Marquee returned no playback request."
        native(device, "confirm", token, request_id=request_id)
        return ("Queued on this tablet. Playback will start when this voice conversation ends. "
                "Briefly acknowledge this, then use stop_listening unless the user asked another question. "
                "Do not say playback has already started.")
    except Exception as exc:
        if sent:
            try:
                native(device, "cancel", token)
            except Exception:
                pass
        return f"Couldn't confirm playback here ({type(exc).__name__}: {exc}). No other screen was selected; don't retry automatically."
