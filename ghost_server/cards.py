"""Visual cards for room screens.

When Ghost answers a question that has a picture-worthy answer (the weather
now; music controls next), the room-voice session also sends the screen a
small "card" message so it can show the answer while Ghost speaks it.

Cards are tiny JSON events (the screen accepts text messages of up to 512
bytes), are best-effort and never affect the spoken answer: any failure here
just means no card.
"""
from __future__ import annotations

import datetime as dt
import json

MAX_CARD_BYTES = 480          # the screen rejects text messages over 512 bytes


def _short(text, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def weather_card(data: dict | None) -> dict | None:
    """Build a weather card from dashboard.weather_data() output."""
    if not data or "temp" not in data:
        return None
    days = []
    for day in (data.get("days") or [])[:3]:
        try:
            name = dt.date.fromisoformat(day["date"]).strftime("%a")
        except (KeyError, ValueError, TypeError):
            continue
        rain = day.get("rain")
        days.append([name, int(day["max"]), int(day["min"]), _short(day.get("desc"), 14),
                     int(rain) if isinstance(rain, (int, float)) else -1])
    card = {"type": "card", "kind": "weather", "city": _short(data.get("city"), 24),
            "temp": int(data["temp"]), "desc": _short(data.get("desc"), 22),
            "day": bool(data.get("is_day", True)), "days": days}
    return fit(card)


def fit(card: dict) -> dict | None:
    """Keep a card under the screen's size limit, dropping forecast days first."""
    while len(json.dumps(card, ensure_ascii=False).encode()) > MAX_CARD_BYTES:
        if card.get("days"):
            card["days"] = card["days"][:-1]
        else:
            return None
    return card


def for_tool(name: str, args: dict, household_city: str | None) -> dict | None:
    """The card for a tool Ghost just used, or None. Runs off the event loop."""
    if name == "weather":
        from .dashboard import _cached, weather_data
        city = str(args.get("city") or household_city or "Melbourne")
        return weather_card(_cached(f"weather:{city}", 600, lambda: weather_data(city)))
    return None
