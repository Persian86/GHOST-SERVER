"""Conservative, deterministic targeting for shared household timer commands."""
from __future__ import annotations


def select(rows: list[dict], label: str, action: str, device_id: str,
           kind: str = "") -> tuple[list[dict], str]:
    name = label.strip().casefold()
    kind = kind.strip().casefold()
    if kind and kind not in ("timer", "alarm"):
        return [], "Do you mean a timer or an alarm?"
    if name in ("timer", "the timer", "timers", "all timers"):
        kind = "timer"
    elif name in ("alarm", "the alarm", "alarms", "all alarms"):
        kind = "alarm"
    all_requested = name in ("all", "everything", "every", "all timers", "all alarms")
    generic = not name or all_requested or name in ("timer", "the timer", "timers", "alarm", "the alarm", "alarms")
    hits = [r for r in rows if r["status"] in ("running", "ringing") and (not kind or r["kind"] == kind)]
    if not generic:
        exact = [r for r in hits if r["id"] == label or r["label"].casefold() == name]
        hits = exact or [r for r in hits if r["label"] and name in r["label"].casefold()]
    if action == "snooze":
        hits = [r for r in hits if r["status"] == "ringing"]
        if not hits:
            return [], "Nothing matching that is ringing."
    elif action in ("stop", "cancel", "dismiss", "off") and not all_requested:
        hits = [r for r in hits if r["status"] == "ringing"] or hits
    if not hits:
        return [], "I couldn't find a matching active timer or alarm."
    if all_requested:
        return hits, ""
    if generic and len(hits) > 1 and all(r["status"] == "ringing" for r in hits):
        local = [r for r in hits if device_id and r.get("device_id") == device_id]
        if len(local) == 1:
            return local, ""
    if len(hits) == 1:
        return hits, ""
    names = list(dict.fromkeys((r["label"] + " " + r["kind"]).strip() for r in hits))
    if len(names) == 1:
        return [], f"There are several {names[0]}s. Please select one in the timer controls."
    choices = ", ".join(names[:4])
    return [], f"Which one do you mean: {choices}? Please tell me its name."
