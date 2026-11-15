"""Per-call, non-persistent receipts. No arguments, credentials or raw errors in UI feedback."""
from __future__ import annotations

PHASES = {"confirmed", "accepted", "unconfirmed", "failed", "blocked"}


def record(ctx, phase: str, label: str, detail: str) -> None:
    if phase not in PHASES:
        phase = "unconfirmed"
    ctx.extra["action_feedback"] = {"phase": phase, "label": label[:80], "detail": detail[:240]}


def public(ctx) -> dict | None:
    value = ctx.extra.get("action_feedback")
    if not isinstance(value, dict) or value.get("phase") not in PHASES:
        return None
    return {"phase": value["phase"], "label": str(value.get("label", "Action"))[:80],
            "detail": str(value.get("detail", ""))[:240]}


def home_matches(item: dict, before: dict, after: dict | None) -> bool:
    """Confirm only properties we can compare directly with an HA state read-back."""
    if not after or after.get("entity_id") != item["data"]["entity_id"]:
        return False
    state = after.get("state")
    if state in (None, "unknown", "unavailable"):
        return False
    service, domain, data = item["service"], item["domain"], item["data"]
    # Scenes/scripts, arbitrary colors and generic services need stronger evidence.
    if domain in ("scene", "script", "homeassistant") or any(k in data for k in ("color_name", "color_temp_kelvin")):
        return False
    expected = {"turn_on": "on", "turn_off": "off", "lock": "locked", "unlock": "unlocked",
                "open_cover": "open", "close_cover": "closed"}.get(service)
    if service == "toggle":
        expected = {"on": "off", "off": "on"}.get(before.get("state")) if domain != "cover" else None
    if service == "set_temperature":
        try:
            return abs(float(after.get("attributes", {}).get("temperature")) - float(data["temperature"])) <= .1
        except (ValueError, TypeError, KeyError):
            return False
    if expected is None or state != expected:
        return False
    if service in ("open_cover", "close_cover") and "current_position" in after.get("attributes", {}):
        try:
            if float(after["attributes"]["current_position"]) != (100 if service == "open_cover" else 0):
                return False
        except (ValueError, TypeError):
            return False
    if "brightness_pct" in data:
        try:
            return abs(float(after.get("attributes", {}).get("brightness")) / 2.55 - float(data["brightness_pct"])) <= 2
        except (TypeError, ValueError):
            return False
    return True
