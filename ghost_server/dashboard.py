"""Data for the Ghost Hub tablet's always-on screen.

GET  /api/v1/dashboard        weather, today's + tomorrow's calendar, home tiles, headlines
POST /api/v1/home/switch      {entity_id, action: on|off|toggle} for a tile

Slow things (weather, calendar, news) are cached; Home Assistant states are read
live each time so the tiles show the house as it is now. Tiles only switch
harmless things (lights, switches, fans, scenes…). Locks, alarms, garage doors
and covers are shown but never operated from the tablet.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
import time

from . import store
from .dashboard_cache import PanelCache, wait_briefly

_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()
_panels = PanelCache(_cache, _lock)

SWITCHABLE = {"light", "switch", "fan", "input_boolean", "scene", "script", "media_player"}
READ_ONLY = {"lock", "alarm_control_panel", "cover", "climate", "sensor", "binary_sensor", "weather"}


def _cached(key: str, ttl: float, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        val = fn()
    except Exception:  # noqa: BLE001 — a broken source just leaves that panel empty
        val = hit[1] if hit else None
    with _lock:
        _cache[key] = (now, val)
    return val


# ── weather ──────────────────────────────────────────────────────────────────
def weather_data(city: str) -> dict | None:
    import httpx
    from .tools.web import _WMO
    from .weather_location import resolve_weather_location
    loc = resolve_weather_location(city)
    if loc is None:
        return None
    f = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=6, params={
        "latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": "auto", "forecast_days": 4,
        "current": "temperature_2m,weather_code,is_day,precipitation,rain,showers,snowfall",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"}).json()
    cur, day = f.get("current", {}), f.get("daily", {})
    days = []
    for i, date in enumerate(day.get("time", [])[:4]):
        days.append({"date": date, "code": day["weather_code"][i], "desc": _WMO.get(day["weather_code"][i], "mixed"),
                     "min": round(day["temperature_2m_min"][i]), "max": round(day["temperature_2m_max"][i]),
                     "rain": (day.get("precipitation_probability_max") or [None] * 4)[i]})
    code = effective_weather_code(cur)
    observed = None
    try:
        stamp = dt.datetime.fromisoformat(cur["time"])
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=dt.timezone(dt.timedelta(seconds=f.get("utc_offset_seconds", 0))))
        observed = stamp.timestamp()
    except (KeyError, ValueError, TypeError):
        pass
    snow = {71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 85: "snow showers", 86: "heavy snow showers"}
    desc = "Rain" if code == 61 and cur.get("weather_code") not in (51,53,55,56,57,61,63,65,66,67,80,81,82) else snow.get(code, _WMO.get(code, "mixed"))
    return {"city": loc["name"], "temp": round(cur.get("temperature_2m", 0)), "code": code,
            "desc": desc, "is_day": bool(cur.get("is_day", 1)), "days": days,
            "observed_at": observed, "precipitation": cur.get("precipitation", 0),
            "region": loc.get("admin1", ""), "country": loc.get("country", ""),
            "latitude": loc["latitude"], "longitude": loc["longitude"]}


def effective_weather_code(current):
    code = current.get("weather_code")
    def positive(name):
        try:
            return float(current.get(name) or 0) > 0
        except (TypeError, ValueError):
            return False
    if code in range(95, 100):
        return code
    if code in (*range(71,78),85,86) or positive("snowfall"):
        return code if code in (*range(71,78),85,86) else 73
    if code in (*range(51,68),80,81,82):
        return code
    if any(positive(k) for k in ("rain", "showers", "precipitation")):
        return 61
    return code



# ── calendar ─────────────────────────────────────────────────────────────────
def calendar_data() -> dict:
    import httpx
    from .tools.web import events_on, today
    urls = [u for u in re.split(r"[;\s]+", str(store.get("calendars") or "")) if u.strip()]
    d0 = today()
    out = {"connected": bool(urls), "today": [], "tomorrow": []}
    texts = []
    for u in urls:
        try:
            texts.append(httpx.get(u.replace("webcal://", "https://"), timeout=8, follow_redirects=True).text)
        except Exception:  # noqa: BLE001
            continue
    for key, day in (("today", d0), ("tomorrow", d0 + dt.timedelta(days=1))):
        ev = []
        for t in texts:
            ev += events_on(t, day)
        ev.sort(key=lambda e: ("00:00" if e[0] == "all day" else e[0]))
        out[key] = [{"time": t, "title": s} for t, s in ev[:10]]
    return out


# ── home ─────────────────────────────────────────────────────────────────────
def _tile(s: dict) -> dict:
    from .tools.home import _describe
    eid = s["entity_id"]
    domain = eid.split(".")[0]
    a = s.get("attributes", {})
    bri = a.get("brightness")
    return {"entity_id": eid, "domain": domain, "name": a.get("friendly_name") or eid,
            "state": s.get("state"), "detail": _describe(s).split(": ", 1)[-1],
            "switchable": domain in SWITCHABLE,
            **({"brightness": round(int(bri) * 100 / 255)} if domain == "light" and bri is not None else {}),
            **({"dimmable": True} if domain == "light" and (bri is not None or "brightness" in
                                                            str(a.get("supported_color_modes", ""))) else {})}


def home_tiles() -> dict:
    if not store.get("ha_token"):
        return {"connected": False, "tiles": []}
    from .tools.home import _req
    states = _req("GET", "/api/states").json()
    wanted = [e.strip() for e in re.split(r"[,\s]+", str(store.get("tablet_entities") or "")) if e.strip()]
    if wanted:
        by_id = {s["entity_id"]: s for s in states}
        chosen = [by_id[e] for e in wanted if e in by_id]
    else:
        # Nothing picked on the admin page yet: lights and switches people actually use, then climate and locks.
        def ok(s):
            eid, st = s["entity_id"], s.get("state")
            return st not in ("unavailable", "unknown") and not eid.endswith(("_led", "_indicator"))
        chosen = [s for s in states if s["entity_id"].split(".")[0] in ("light", "switch", "fan") and ok(s)][:10]
        chosen += [s for s in states if s["entity_id"].split(".")[0] in ("climate", "lock") and ok(s)][:4]
    return {"connected": True, "tiles": [_tile(s) for s in chosen[:16]]}


def entities() -> dict:
    """Everything a phone can pin as a favourite: lights, switches, fans, scenes, scripts, media
    (never locks, alarms or doors — those stay voice-and-confirm only)."""
    if not store.get("ha_token"):
        return {"connected": False, "entities": []}
    from .tools.home import _req
    out = []
    for s in _req("GET", "/api/states").json():
        eid = s["entity_id"]
        if eid.split(".")[0] in SWITCHABLE and s.get("state") not in ("unavailable",) \
                and not eid.endswith(("_led", "_indicator")):
            out.append(_tile(s))
    out.sort(key=lambda t: (t["domain"], t["name"].lower()))
    return {"connected": True, "entities": out}


def tiles_for(ids: list[str]) -> dict:
    """Tiles for these entities, in this order (a phone's own favourites)."""
    if not store.get("ha_token"):
        return {"connected": False, "tiles": []}
    from .tools.home import _req
    by_id = {s["entity_id"]: s for s in _req("GET", "/api/states").json()}
    return {"connected": True, "tiles": [_tile(by_id[i]) for i in ids if i in by_id][:40]}


def all_lights_off() -> dict:
    """"Everything off" when leaving or going to bed: every light (not switches — a switch can be the fridge)."""
    from .tools.home import _call, _req
    on = [s["entity_id"] for s in _req("GET", "/api/states").json()
          if s["entity_id"].startswith("light.") and s.get("state") == "on"]
    if on:
        code = _call("light", "turn_off", {"entity_id": on})
        if code >= 300:
            return {"ok": False, "error": f"Home Assistant said HTTP {code}"}
    return {"ok": True, "turned_off": len(on)}


def switch(entity_id: str, action: str) -> dict:
    from .tools.home import _call, _req
    domain = entity_id.split(".")[0]
    if domain not in SWITCHABLE:
        return {"ok": False, "error": f"{domain} can't be switched from the tablet"}
    m = re.fullmatch(r"dim:(\d{1,3})", action or "")
    if m and domain == "light":                                  # brightness from the phone's slider
        pct = max(0, min(100, int(m.group(1))))
        code = _call("light", "turn_on" if pct else "turn_off",
                     {"entity_id": entity_id, **({"brightness_pct": pct} if pct else {})})
        if code >= 300:
            return {"ok": False, "error": f"Home Assistant said HTTP {code}"}
        time.sleep(0.4)
        return {"ok": True, "tile": _tile(_req("GET", f"/api/states/{entity_id}").json())}
    action = action if action in ("on", "off", "toggle") else "toggle"
    if domain in ("scene", "script"):
        service = "turn_on"
    elif domain == "media_player":
        service = "media_play_pause"
    else:
        service = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}[action]
    code = _call(domain, service, {"entity_id": entity_id})
    if code >= 300:
        return {"ok": False, "error": f"Home Assistant said HTTP {code}"}
    time.sleep(0.4)
    s = _req("GET", f"/api/states/{entity_id}").json()
    return {"ok": True, "tile": _tile(s)}


# ── headlines ────────────────────────────────────────────────────────────────
def headlines() -> list[dict]:
    from .news_feed import top_headlines
    rows = top_headlines()
    if rows:
        return rows
    from .tools.web import ddg_news
    country, city = store.get("news_country") or "Australia", store.get("city") or "Melbourne"
    rows = ddg_news(f"{country} news today", 4) + ddg_news(f"{city} news today", 3)
    seen, out = set(), []
    for r in rows:
        t = (r.get("title") or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append({"title": t, "source": r.get("source", ""), "url": r.get("url", ""),
                        "summary": r.get("body", ""), "published": "", "provider": "News"})
    return out[:6]


def build(partial: bool = False) -> dict:
    city = str(store.get("city") or "Melbourne")
    if not partial:
        # Older phone/desktop clients keep their complete-response contract.
        return {
            "time": time.time(), "household": store.get("owner_name") or "", "assistant": store.get("assistant_name") or "Ghost",
            "weather": _cached(f"weather:{city}", 600, lambda: weather_data(city)),
            "calendar": _cached("calendar", 600, calendar_data) or {"connected": False, "today": [], "tomorrow": []},
            "home": _cached("home", 5, home_tiles) or {"connected": False, "tiles": []},
            "headlines": _cached("headlines", 1800, headlines) or [],
            "features": {"faces": bool(store.get("faces_enabled")), "photo_frame_after": _int(store.get("photo_frame_after"), 5)},
            "theme": _theme(),
        }
    loaders = {
        "weather": (f"weather:{city}", 180, lambda: weather_data(city)),
        "calendar": ("calendar", 600, calendar_data),
        "home": ("home", 5, home_tiles),
        "headlines": ("headlines", 1800, headlines),
    }
    jobs = [_panels.start(key, ttl, load) for key, ttl, load in loaders.values()]
    wait_briefly(jobs)
    snapshots = {name: _panels.snapshot(key) for name, (key, _, _) in loaders.items()}
    return {
        "time": time.time(),
        "household": store.get("owner_name") or "",
        "assistant": store.get("assistant_name") or "Ghost",
        "weather": snapshots["weather"][0],
        "weather_updated_at": snapshots["weather"][2],
        "calendar": snapshots["calendar"][0] or {"connected": False, "today": [], "tomorrow": []},
        "home": snapshots["home"][0] or {"connected": False, "tiles": []},
        "headlines": snapshots["headlines"][0] or [],
        "panel_status": {name: value[1] for name, value in snapshots.items()},
        "features": {"faces": bool(store.get("faces_enabled")), "photo_frame_after": _int(store.get("photo_frame_after"), 5)},
        "theme": _theme(),
    }


def _theme() -> dict:
    try:
        from . import theme
        return theme.current()
    except Exception:  # noqa: BLE001
        return {"type": "theme", "id": ""}


def _int(v, default: int) -> int:
    try:
        return max(0, int(float(v)))
    except (TypeError, ValueError):
        return default
