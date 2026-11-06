"""Seasonal themes — Ghost dresses up for the holidays.

The server decides what today is (Halloween, Christmas, Easter, a family birthday… or just the
season) and every app gets {"type": "theme", ...} on connect and when it changes. The apps tint
their accent colours, put an accessory on Ghost (a witch's hat, a Santa hat, bunny ears, a party
hat…), let something gently fall across the screen (bats, snow, hearts, eggs, confetti, leaves)
and change the greeting ("Happy Halloween, Adam").

Admin page → Brain & keys → Seasonal themes: Automatic / Off / preview any theme, which holidays
count, family birthdays, and the hemisphere for the seasons (southern by default — Melbourne).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re

from . import store

# id → how it looks. colours: accent (buttons, rings), soft (text accents), orb (Ghost's body: hi, mid, lo, deep)
THEMES: dict[str, dict] = {
    "halloween": {"name": "Halloween", "emoji": "🎃", "greeting": "Happy Halloween", "particles": "bats",
                  "accessory": "witch_hat", "accent": "#FF8A3D", "soft": "#FFB27A",
                  "orb": ["#FFE2C7", "#FF9B50", "#B8461A", "#3D1405"]},
    "christmas": {"name": "Christmas", "emoji": "🎄", "greeting": "Merry Christmas", "particles": "snow",
                  "accessory": "santa_hat", "accent": "#E8505B", "soft": "#9FE3B4",
                  "orb": ["#FFFFFF", "#F2E9FF", "#B8A6FF", "#3A2E7A"]},
    "easter": {"name": "Easter", "emoji": "🐣", "greeting": "Happy Easter", "particles": "eggs",
               "accessory": "bunny_ears", "accent": "#F7A8D8", "soft": "#BDE8C9",
               "orb": ["#FFF6FB", "#F9C9E6", "#B884D6", "#4A2A66"]},
    "valentines": {"name": "Valentine's Day", "emoji": "💜", "greeting": "Happy Valentine's Day", "particles": "hearts",
                   "accessory": "heart", "accent": "#FF6B9A", "soft": "#FFB3CB",
                   "orb": ["#FFE8F0", "#FF9EC0", "#C2386B", "#4A0F25"]},
    "new_year": {"name": "New Year", "emoji": "🎆", "greeting": "Happy New Year", "particles": "confetti",
                 "accessory": "party_hat", "accent": "#FFD46B", "soft": "#FFE7A8",
                 "orb": ["#FFF7DD", "#FFD46B", "#B78A1E", "#3D2A05"]},
    "lunar_new_year": {"name": "Lunar New Year", "emoji": "🧧", "greeting": "Happy Lunar New Year", "particles": "lanterns",
                       "accessory": "none", "accent": "#E8505B", "soft": "#FFD46B",
                       "orb": ["#FFE9D6", "#FF8A6B", "#B3262F", "#3D0A0D"]},
    "st_patricks": {"name": "St Patrick's Day", "emoji": "☘️", "greeting": "Happy St Patrick's Day", "particles": "clovers",
                    "accessory": "shamrock", "accent": "#5BD48A", "soft": "#A8EFC2",
                    "orb": ["#E9FFF1", "#7BE3A4", "#21894F", "#06331B"]},
    "nowruz": {"name": "Nowruz", "emoji": "🌸", "greeting": "Nowruz mobarak", "particles": "petals",
               "accessory": "flower", "accent": "#7EE0B5", "soft": "#F9C9E6",
               "orb": ["#F3FFF8", "#A8F0CF", "#3E9E79", "#0E3A2B"]},
    "anzac": {"name": "Anzac Day", "emoji": "🌺", "greeting": "Lest we forget", "particles": "none",
              "accessory": "poppy", "accent": "#C7414B", "soft": "#E0A3A8",
              "orb": ["#E6DEFF", "#B8A6FF", "#5B45C9", "#241A57"]},
    "mothers_day": {"name": "Mother's Day", "emoji": "💐", "greeting": "Happy Mother's Day", "particles": "petals",
                    "accessory": "flower", "accent": "#F7A8D8", "soft": "#FFD1EA",
                    "orb": ["#FFF2FA", "#F9C9E6", "#B95A93", "#45163A"]},
    "fathers_day": {"name": "Father's Day", "emoji": "👔", "greeting": "Happy Father's Day", "particles": "stars",
                    "accessory": "bow_tie", "accent": "#6FB7FF", "soft": "#B5D9FF",
                    "orb": ["#EAF4FF", "#9CCBFF", "#2F6AB0", "#0B2645"]},
    "birthday": {"name": "Birthday", "emoji": "🎂", "greeting": "Happy birthday", "particles": "confetti",
                 "accessory": "party_hat", "accent": "#FFD46B", "soft": "#FFB3CB",
                 "orb": ["#FFF7DD", "#FFC6E2", "#B865D6", "#3A1752"]},
    # the seasons — gentle, for every other day
    "summer": {"name": "Summer", "emoji": "☀️", "greeting": "", "particles": "sparkles", "accessory": "sunglasses",
               "accent": "#FFC15E", "soft": "#FFE0A3", "orb": None},
    "autumn": {"name": "Autumn", "emoji": "🍂", "greeting": "", "particles": "leaves", "accessory": "none",
               "accent": "#E08A4B", "soft": "#F2C49B", "orb": None},
    "winter": {"name": "Winter", "emoji": "❄️", "greeting": "", "particles": "snow_light", "accessory": "beanie",
               "accent": "#8FC7FF", "soft": "#CFE6FF", "orb": None},
    "spring": {"name": "Spring", "emoji": "🌼", "greeting": "", "particles": "petals", "accessory": "none",
               "accent": "#9FE38A", "soft": "#D7F5C9", "orb": None},
}
HOLIDAYS = ["halloween", "christmas", "easter", "valentines", "new_year", "lunar_new_year", "st_patricks",
            "nowruz", "anzac", "mothers_day", "fathers_day", "birthday"]
SEASONS = ["summer", "autumn", "winter", "spring"]
LUNAR_NEW_YEAR = {2026: (2, 17), 2027: (2, 6), 2028: (1, 26), 2029: (2, 13), 2030: (2, 3), 2031: (1, 23),
                  2032: (2, 11), 2033: (1, 31), 2034: (2, 19), 2035: (2, 8)}


def easter(year: int) -> dt.date:
    """Western Easter Sunday (Anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def _nth_sunday(year: int, month: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def birthdays() -> list[tuple[str, int, int]]:
    """'Adam 14/03, Hayley 2/7' → [(name, day, month)] (Australian day/month)."""
    out = []
    for part in re.split(r"[,;\n]+", str(store.get("theme_birthdays") or "")):
        m = re.match(r"\s*(.+?)\s+(\d{1,2})[/.-](\d{1,2})\s*$", part)
        if m:
            try:
                dt.date(2000, int(m.group(3)), int(m.group(2)))
                out.append((m.group(1).strip(), int(m.group(2)), int(m.group(3))))
            except ValueError:
                pass
    return out


def holiday_on(d: dt.date) -> tuple[str, str]:
    """(theme id, who) for a holiday on this date, or ("", "")."""
    y = d.year
    for name, day, month in birthdays():
        if (d.day, d.month) == (day, month):
            return "birthday", name
    e = easter(y)
    checks = [
        ("new_year", d == dt.date(y, 12, 31) or d == dt.date(y, 1, 1)),
        ("christmas", d.month == 12 and 1 <= d.day <= 26),
        ("halloween", d.month == 10 and 24 <= d.day <= 31),
        ("easter", e - dt.timedelta(days=3) <= d <= e + dt.timedelta(days=1)),       # Thursday → Easter Monday
        ("valentines", (d.month, d.day) == (2, 14)),
        ("lunar_new_year", y in LUNAR_NEW_YEAR and 0 <= (d - dt.date(y, *LUNAR_NEW_YEAR[y])).days <= 2),
        ("st_patricks", (d.month, d.day) == (3, 17)),
        ("nowruz", d.month == 3 and 19 <= d.day <= 22),
        ("anzac", (d.month, d.day) == (4, 25)),
        ("mothers_day", d == _nth_sunday(y, 5, 2)),                                  # Australia: 2nd Sunday in May
        ("fathers_day", d == _nth_sunday(y, 9, 1)),                                  # Australia: 1st Sunday in September
    ]
    off = set(_split(store.get("theme_off")))
    for tid, hit in checks:
        if hit and tid not in off:
            return tid, ""
    return "", ""


def season_on(d: dt.date) -> str:
    south = str(store.get("theme_hemisphere") or "south").lower().startswith("s")
    m = d.month
    north = "winter" if m in (12, 1, 2) else "spring" if m in (3, 4, 5) else "summer" if m in (6, 7, 8) else "autumn"
    flip = {"winter": "summer", "summer": "winter", "spring": "autumn", "autumn": "spring"}
    return flip[north] if south else north


def _split(v) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v or "").split(",") if x.strip()]


def current(d: dt.date | None = None) -> dict:
    """What the apps should look like today: {"id", "name", "greeting", "particles", "accessory", colours…}.
    id "" = no theme (normal Ghost)."""
    from .quiet import _local
    d = d or _local().date()
    mode = str(store.get("theme_mode") or "auto")
    who = ""
    if mode == "off":
        return {"type": "theme", "id": "", "date": d.isoformat()}
    if mode in THEMES:                                          # previewing one from the admin page
        tid = mode
    else:
        tid, who = holiday_on(d)
        if not tid and store.get("theme_seasons"):
            tid = season_on(d)
    if not tid:
        return {"type": "theme", "id": "", "date": d.isoformat()}
    t = dict(THEMES[tid])
    if tid == "birthday" and who:
        t["greeting"] = f"Happy birthday, {who}"
        t["for"] = who
    return {"type": "theme", "id": tid, "date": d.isoformat(), "holiday": tid in HOLIDAYS,
            "effects": bool(store.get("theme_effects")), **t}


def prompt_line() -> str:
    """For Ghost's instructions: so it can join in ("Happy Halloween!") on the day."""
    t = current()
    if not t.get("holiday"):
        return ""
    if t["id"] == "anzac":
        return "Today is Anzac Day in Australia — a day of remembrance; be respectful, no jokes about it."
    if t["id"] == "birthday":
        return f"Today is {t.get('for')}'s birthday — wish them a happy birthday when you talk to them."
    return f"Today it's {t['name']} — feel free to say '{t['greeting']}' and be a little festive."


_last = {"key": ""}


async def theme_loop(every: float = 300) -> None:
    """Tell every device when the theme changes (at midnight, or when the admin changes it)."""
    from .hub import hub
    while True:
        try:
            t = await asyncio.to_thread(current)
            key = f"{t.get('id')}|{t.get('greeting', '')}|{t.get('effects')}"
            if key != _last["key"]:
                if _last["key"]:
                    await hub.broadcast(t)
                _last["key"] = key
        except Exception as e:  # noqa: BLE001
            print(f"[Ghost Server] theme: {e}")
        await asyncio.sleep(every)


def push_soon() -> None:
    """After a settings change: tell devices now rather than within 5 minutes."""
    from . import household
    t = current()
    _last["key"] = f"{t.get('id')}|{t.get('greeting', '')}|{t.get('effects')}"
    household._push(t)
