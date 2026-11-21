"""Bushfire and emergency watch — official VicEmergency warnings and fires near home.

Source: the public VicEmergency feed (the same data as the VicEmergency app and
emergency.vic.gov.au). It needs no key. It lists:
  * warnings   — official messages with a level (Advice, Watch and Act,
                 Emergency Warning) and usually the warning area as a polygon
  * incidents  — every reported fire (grass, scrub, bush, structure, planned burns…)

What Ghost does with it:
  * "any fires near me?" → the `fires_near_me` tool reads out what's within range
  * every few minutes the server checks for new or escalated items near home and
    tells every device: Watch and Act / Emergency Warning (or home inside a warning
    area) are urgent and get through Do Not Disturb; Advice and nearby fires don't.
Each item is announced once, and again only if its warning level goes up.
"""
from __future__ import annotations

import math
import time

import httpx

from . import store

FEED = "https://emergency.vic.gov.au/public/osom-geojson.json"
CHECK_EVERY = 300            # s between background checks
FIRE_ALERT_KM = 10           # fires (not official warnings) are announced only this close
SEEN_KEEP = 3 * 86400        # forget announced items after three days

LEVELS = {"emergency warning": 3, "evacuate immediately": 3, "evacuate now": 3, "watch and act": 2,
          "prepare to evacuate": 2, "advice": 1, "community information": 1}
LEVEL_NAMES = {3: "Emergency Warning", 2: "Watch and Act", 1: "Advice", 0: ""}
_DIRS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]

_feed_cache: tuple[float, list] = (0.0, [])
_home_cache: tuple[str, tuple[float, float] | None] = ("", None)


# ── where home is ────────────────────────────────────────────────────────────
def home() -> tuple[float, float] | None:
    """(lat, lon) of home: the `fire_home` setting ("-37.58, 144.73") or the household city."""
    global _home_cache
    raw = str(store.get("fire_home") or "").strip()
    if raw:
        try:
            lat, lon = (float(v) for v in raw.replace(";", ",").split(",")[:2])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
        except ValueError:
            pass
    city = str(store.get("city") or "Melbourne")
    if _home_cache[0] == city and _home_cache[1]:
        return _home_cache[1]
    from .weather_location import resolve_weather_location
    loc = resolve_weather_location(city)
    point = (float(loc["latitude"]), float(loc["longitude"])) if loc else None
    _home_cache = (city, point)
    return point


# ── geometry ─────────────────────────────────────────────────────────────────
def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = map(lambda p: (math.radians(p[0]), math.radians(p[1])), (a, b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, h)))


def direction(frm: tuple[float, float], to: tuple[float, float]) -> str:
    """Compass direction of `to` as seen from `frm` ("north-west")."""
    la1, la2 = math.radians(frm[0]), math.radians(to[0])
    dlo = math.radians(to[1] - frm[1])
    brg = math.degrees(math.atan2(math.sin(dlo) * math.cos(la2),
                                  math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlo)))
    return _DIRS[round(((brg + 360) % 360) / 45) % 8]


def _geoms(g: dict | None) -> list[dict]:
    if not g:
        return []
    if g.get("type") == "GeometryCollection":
        return [x for sub in g.get("geometries") or [] for x in _geoms(sub)]
    return [g]


def _polys(g: dict | None) -> list[list]:
    out = []
    for s in _geoms(g):
        if s.get("type") == "Polygon":
            out.append(s.get("coordinates") or [])
        elif s.get("type") == "MultiPolygon":
            out.extend(s.get("coordinates") or [])
    return out


def _point(g: dict | None) -> tuple[float, float] | None:
    """(lat, lon) for a feature: its point, else the middle of its first polygon."""
    for s in _geoms(g):
        if s.get("type") == "Point":
            try:
                lon, lat = s["coordinates"][:2]
                return float(lat), float(lon)
            except (KeyError, ValueError, TypeError):
                continue
    for poly in _polys(g):
        ring = poly[0] if poly else []
        if ring:
            return (sum(p[1] for p in ring) / len(ring), sum(p[0] for p in ring) / len(ring))
    return None


def _inside_ring(lat: float, lon: float, ring: list) -> bool:
    inside = False
    for i in range(len(ring)):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[i - 1][0], ring[i - 1][1]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
    return inside


def inside(g: dict | None, at: tuple[float, float]) -> bool:
    """Is `at` (lat, lon) inside any polygon of this geometry (holes respected)?"""
    for poly in _polys(g):
        if poly and _inside_ring(at[0], at[1], poly[0]) and not any(_inside_ring(at[0], at[1], h) for h in poly[1:]):
            return True
    return False


# ── the feed ─────────────────────────────────────────────────────────────────
def fetch(max_age: float = 120) -> list[dict]:
    global _feed_cache
    if time.time() - _feed_cache[0] < max_age:
        return _feed_cache[1]
    r = httpx.get(FEED, timeout=20, headers={"User-Agent": "GhostServer/1.0 (home assistant)"})
    r.raise_for_status()
    feats = r.json().get("features") or []
    _feed_cache = (time.time(), feats)
    return feats


def _text(*vals) -> str:
    return " ".join(str(v or "") for v in vals).lower()


def level_of(p: dict) -> int:
    for v in (p.get("sourceTitle"), p.get("category1"), p.get("status"), (p.get("cap") or {}).get("event")):
        n = LEVELS.get(str(v or "").strip().lower())
        if n:
            return n
    return 0


def is_fire(p: dict) -> bool:
    t = _text(p.get("category1"), p.get("category2"), (p.get("cap") or {}).get("event"), p.get("sourceTitle"))
    return "fire" in t or "burn" in t or "smoke" in t


def is_planned(p: dict) -> bool:
    return "planned burn" in _text(p.get("category1"), p.get("category2"))


def items_near(radius_km: float, feats: list[dict] | None = None, at: tuple[float, float] | None = None,
               fires_only: bool = False) -> list[dict]:
    """Warnings and incidents within radius of home (or whose warning area covers home), nearest first."""
    at = at or home()
    if not at:
        return []
    out = []
    for f in feats if feats is not None else fetch():
        p = f.get("properties") or {}
        kind = p.get("feedType")
        if kind not in ("warning", "incident"):
            continue                                     # skip burn-area outlines and anything new
        pt = _point(f.get("geometry"))
        if not pt:
            continue
        fire = is_fire(p)
        if fires_only and not fire:
            continue
        if kind == "incident" and not fire:
            continue                                     # incidents: only fires (no tree-down / traffic noise)
        km = distance_km(at, pt)
        covers = kind == "warning" and inside(f.get("geometry"), at)
        if km > radius_km and not covers:
            continue
        level = level_of(p) if kind == "warning" else 0
        name = str(p.get("location") or p.get("sourceTitle") or "").strip()
        if kind == "warning":     # category2 holds codes like "Met"; the CAP event names the hazard
            what = str((p.get("cap") or {}).get("event") or p.get("category2") or "Warning").strip()
        else:
            what = str(p.get("category2") or p.get("category1") or "Incident").strip()
        out.append({"id": f"{kind}:{p.get('id') or p.get('sourceId') or name}", "kind": kind, "level": level,
                    "level_name": LEVEL_NAMES[level], "what": what, "where": name, "status": str(p.get("status") or ""),
                    "size": str(p.get("sizeFmt") or ""), "km": round(km, 1), "dir": direction(at, pt),
                    "covers_home": covers, "fire": fire, "planned": is_planned(p),
                    "updated": str(p.get("updated") or p.get("created") or "")})
    out.sort(key=lambda i: (not i["covers_home"], -i["level"], i["km"]))
    return out


def describe(i: dict) -> str:
    """One spoken line for an item."""
    if i["kind"] == "warning":
        where = "your area is inside the warning area" if i["covers_home"] else f"{i['km']:.0f} km {i['dir']} of home"
        return f"{i['level_name'] or 'Warning'}: {i['what']} at {i['where']} — {where}."
    bits = [f"{i['what']} at {i['where']}, {i['km']:.0f} km {i['dir']}"]
    if i["status"] and i["status"].lower() not in ("unknown", ""):
        bits.append(f"status {i['status'].lower()}")
    if i["size"] and not i["size"].startswith("0"):
        bits.append(i["size"].rstrip("."))
    return ", ".join(bits) + "."


def summary(radius_km: float, fires_only: bool = False) -> str:
    items = items_near(radius_km, fires_only=fires_only)
    if not items:
        return (f"No fires or emergency warnings within {radius_km:.0f} km of home on VicEmergency right now.")
    warnings = [i for i in items if i["kind"] == "warning"]
    fires = [i for i in items if i["kind"] == "incident" and not i["planned"]]
    planned = [i for i in items if i["planned"]]
    lines = []
    if warnings:
        lines.append(f"{len(warnings)} official warning{'s' * (len(warnings) != 1)}:")
        lines += ["  " + describe(i) for i in warnings[:5]]
    if fires:
        lines.append(f"{len(fires)} fire{'s' * (len(fires) != 1)} reported within {radius_km:.0f} km:")
        lines += ["  " + describe(i) for i in fires[:6]]
    if planned:
        lines.append(f"Also {len(planned)} planned burn{'s' * (len(planned) != 1)} nearby, the closest "
                     f"{planned[0]['km']:.0f} km {planned[0]['dir']}.")
    lines.append("(Source: VicEmergency. For what to do, follow the official warning or call 000 in an emergency.)")
    return "\n".join(lines)


# ── background alerts ────────────────────────────────────────────────────────
def radius() -> float:
    try:
        return max(2.0, min(100.0, float(store.get("fire_radius_km") or 20)))
    except (TypeError, ValueError):
        return 20.0


def to_announce(items: list[dict], seen: dict, now: float) -> list[tuple[dict, bool]]:
    """New or escalated items worth telling the house about, with whether each is urgent.
    `seen` (id → {"level", "at"}) is updated in place."""
    out = []
    for i in items:
        if i["planned"]:
            continue
        if i["kind"] == "incident" and i["km"] > FIRE_ALERT_KM:
            continue
        prev = seen.get(i["id"])
        rank = i["level"] + (10 if i["covers_home"] else 0)
        if prev is not None and rank <= prev.get("level", 0):
            prev["at"] = now
            continue
        seen[i["id"]] = {"level": rank, "at": now}
        out.append((i, i["covers_home"] or i["level"] >= 2))
    for k in [k for k, v in seen.items() if now - v.get("at", 0) > SEEN_KEEP]:
        seen.pop(k, None)
    return out


def check_once(now: float | None = None) -> list[dict]:
    """Fetch, compare with what was already announced, return the messages to send."""
    now = now or time.time()
    seen = store.get("fire_seen")
    if not isinstance(seen, dict):
        seen = {}
    found = to_announce(items_near(radius()), seen, now)
    store.put("fire_seen", seen)
    msgs, calm = [], []
    for item, urgent in found:
        store.add_event("", "fire_alert", {"text": describe(item), "urgent": urgent, "id": item["id"]})
        if not urgent:
            calm.append(item)
            continue
        text = ("Fire alert. " if item["fire"] else "Emergency alert. ") + describe(item) + \
            " Check VicEmergency now and follow the warning."
        msgs.append({"type": "say", "from": "VicEmergency", "text": text, "urgent": True, "safety": True,
                     "fire": True, "at": now})
    if calm:                       # everything that isn't urgent goes out as one message
        head = "Fire nearby. " if len(calm) == 1 else f"{len(calm)} new fire and emergency reports near home. "
        text = head + " ".join(describe(i) for i in calm[:4]) + (" And more on VicEmergency." if len(calm) > 4 else "")
        msgs.append({"type": "say", "from": "VicEmergency", "text": text, "urgent": False, "fire": True, "at": now})
    return msgs


async def fire_loop(every: float = CHECK_EVERY) -> None:
    import asyncio
    from .hub import hub
    await asyncio.sleep(30)                              # let the server settle after start-up
    while True:
        try:
            if store.get("fire_alerts"):
                for msg in await asyncio.to_thread(check_once):
                    await hub.broadcast(msg)
        except Exception as e:  # noqa: BLE001
            print(f"[Ghost Server] fire check skipped: {e}")
        await asyncio.sleep(every)
