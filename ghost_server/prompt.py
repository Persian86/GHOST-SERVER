"""The system instruction each agent's Gemini Live session starts with — built
fresh per session, so every device knows the same things about the household."""
from __future__ import annotations

import datetime as dt

from . import store
from .tools.devices import describe_devices


def _now() -> dt.datetime:
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(str(store.get("timezone") or "Australia/Melbourne")))
    except Exception:
        return dt.datetime.now()


def build(device: dict) -> str:
    name = store.get("assistant_name") or "Ghost"
    owner = store.get("owner_name") or ""
    now = _now()
    parts = [
        f"You are {name}, a household AI assistant. Right now you are speaking through "
        f"\"{device.get('name')}\" ({device.get('kind')}), one of several {name} devices that share one "
        f"brain and memory on the family's home server.",
        f"Current date and time: {now.strftime('%A %d %B %Y, %I:%M %p')} ({store.get('timezone')}). "
        f"Home city: {store.get('city')}." + (f" The owner is {owner}; call them {owner}." if owner else ""),
        "[HOW YOU WORK]\n"
        "- Tools named in your list that belong to this device act on THIS device.\n"
        "- Server tools act on the household: shared memory (remember / recall), the smart home, Marquee, "
        "documents, news, weather, calendar, and OTHER devices (list_devices, device_action, announce).\n"
        "- For anything that needs real reasoning, planning or writing, call think_deeply and speak its answer.\n"
        "- For jobs needing several tools in a row, call run_task; it reports back when done.\n"
        "- When the user tells you something lasting about themselves or the house, call remember.\n"
        "- Never claim you did something a tool did not confirm. Keep spoken replies short and natural.",
        "[OTHER DEVICES]\n" + describe_devices(exclude=device.get("id", "")),
    ]
    facts = store.facts()
    if facts:
        by: dict[str, list[str]] = {}
        for f in facts:
            by.setdefault(f["section"], []).append(f["text"])
        parts.append("[WHAT YOU KNOW ABOUT THE HOUSEHOLD]\n" + "\n".join(
            f"{sec}: " + "; ".join(items) for sec, items in by.items())[:6000])
    rules = store.rules()
    if rules:
        parts.append("[STANDING INSTRUCTIONS — follow them]\n" + "\n".join(f"- {r['text']}" for r in rules))
    turns = [t for t in store.recent_turns(8) if t["device_id"] != device.get("id")]
    if turns:
        names = {d["id"]: d["name"] for d in store.devices(include_revoked=True)}
        parts.append("[RECENTLY, ON OTHER DEVICES — for continuity only]\n" + "\n".join(
            f"{names.get(t['device_id'], '?')} · {t['role']}: {t['text'][:200]}" for t in turns[-6:]))
    return "\n\n".join(parts)
