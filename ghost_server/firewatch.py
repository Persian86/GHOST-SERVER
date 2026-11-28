"""Bushfire and emergency watch — official VicEmergency warnings and fires near home.

Source: the public VicEmergency feed (the same data as the VicEmergency app and
emergency.vic.gov.au). It needs no key. It lists:
  * warnings   — official messages with a level (Advice, Watch and Act,
                 Emergency Warning) and usually the warning area as a polygon
  * incidents  — every reported fire (grass, scrub, bush, structure, planned burns…)

What Ghost does with it:
  * "any fires near me?" → the `fires_near_me` tool reads out what's within range
  * every few minutes the server checks for new or escalated items near home and
    sends every phone and tablet a notification. Ghost doesn't say them out loud
    (by request, Nov 2026): Watch and Act / Emergency Warning (or home inside a
    warning area) are marked urgent; Advice and nearby fires are grouped into one.
Each item is sent once, and again only if its warning level goes up.
"""
from __future__ import annotations

import math
import time

import httpx

from . import store

FEED = "https://emergency.vic.gov.au/public/osom-geojson.json"
CHECK_EVERY = 300            # s between background checks
FIRE_ALERT_KM = 10           # fire and flood incidents (not official warnings) are announced only this close
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


def is_flood(p: dict) -> bool:
    t = _text(p.get("category1"), p.get("category2"), (p.get("cap") or {}).get("event"), p.get("sourceTitle"))
    return "flood" in t


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
        flood = not fire and is_flood(p)
        if fires_only and not fire:
            continue
        if kind == "incident" and not (fire or flood):
            continue                                     # incidents: fires and floods only (no tree-down / traffic noise)
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
                    "covers_home": covers, "fire": fire, "flood": flood, "planned": is_planned(p),
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
        return (f"No fires, floods or emergency warnings within {radius_km:.0f} km of home on VicEmergency right now.")
    warnings = [i for i in items if i["kind"] == "warning"]
    fires = [i for i in items if i["kind"] == "incident" and i["fire"] and not i["planned"]]
    floods = [i for i in items if i["kind"] == "incident" and i.get("flood")]
    planned = [i for i in items if i["planned"]]
    lines = []
    if warnings:
        lines.append(f"{len(warnings)} official warning{'s' * (len(warnings) != 1)}:")
        lines += ["  " + describe(i) for i in warnings[:5]]
    if fires:
        lines.append(f"{len(fires)} fire{'s' * (len(fires) != 1)} reported within {radius_km:.0f} km:")
        lines += ["  " + describe(i) for i in fires[:6]]
    if floods:
        lines.append(f"{len(floods)} flood incident{'s' * (len(floods) != 1)} reported within {radius_km:.0f} km:")
        lines += ["  " + describe(i) for i in floods[:6]]
    if planned:
        lines.append(f"Also {len(planned)} planned burn{'s' * (len(planned) != 1)} nearby, the closest "
                     f"{planned[0]['km']:.0f} km {planned[0]['dir']}.")
    lines.append("(Source: VicEmergency. For what to do, follow the official warning or call 000 in an emergency.)")
    return "\n".join(lines)


# ── background alerts ────────────────────────────────────────────────────────
def radius() -> float:
    try:
        return max(2.0, min(100.0, float(store.get("fire_radius_km") or 5)))
    except (TypeError, ValueError):
        return 5.0


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
        text = describe(item) + " Check VicEmergency now and follow the warning."
        msgs.append({"type": "notify", "title": "⚠ VicEmergency " + ("fire warning" if item["fire"] else
                                                                   "flood warning" if item.get("flood") else "warning"),
                     "text": text, "level": "fire", "urgent": True, "fire": True, "at": now})
    if calm:                       # everything that isn't urgent goes out as one message
        head = (("Flooding nearby. " if calm[0].get("flood") else "Fire nearby. ") if len(calm) == 1
                else f"{len(calm)} new emergency reports near home. ")
        text = head + " ".join(describe(i) for i in calm[:4]) + (" And more on VicEmergency." if len(calm) > 4 else "")
        msgs.append({"type": "notify", "title": "VicEmergency", "text": text, "level": "fire", "urgent": False, "fire": True, "at": now})
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


# ── fire danger rating and total fire ban (CFA) ──────────────────────────────
# CFA publishes the Bureau of Meteorology's Fire Danger Ratings and Total Fire Ban
# declarations for each Victorian fire district as one public RSS feed (no key).
CFA_FDR_FEED = "https://www.cfa.vic.gov.au/cfa/rssfeed/tfbfdrforecast_rss.xml"
DISTRICTS = ["Mallee", "Wimmera", "South West", "Northern Country", "North Central", "Central",
             "North East", "East Gippsland", "West and South Gippsland"]
_fdr_cache: tuple[float, str] = (0.0, "")


def fetch_fdr(max_age: float = 1800) -> str:
    global _fdr_cache
    if time.time() - _fdr_cache[0] < max_age and _fdr_cache[1]:
        return _fdr_cache[1]
    r = httpx.get(CFA_FDR_FEED, timeout=15, headers={"User-Agent": "GhostServer/1.0 (home assistant)"})
    r.raise_for_status()
    _fdr_cache = (time.time(), r.text)
    return r.text


def _plain(html_text: str) -> str:
    import html as _html
    import re
    t = re.sub(r"(?i)<br\s*/?>|</p>|<p[^>]*>", "\n", html_text or "")
    return _html.unescape(re.sub(r"<[^>]+>", "", t))


def _district_lines(text: str) -> dict[str, str]:
    import re
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*([A-Za-z][A-Za-z &]+?)\s*:\s*(.+?)\s*$", line)
        if m and m.group(1).strip().lower() in {d.lower() for d in DISTRICTS}:
            out[m.group(1).strip().lower()] = m.group(2).strip()
    return out


def parse_fdr(xml_text: str, district: str, today=None) -> dict | None:
    """Today's rating and fire ban for one district from the CFA feed, or None if it isn't listed."""
    import datetime as dt
    import xml.etree.ElementTree as ET
    today = today or dt.date.today()
    items = ET.fromstring(xml_text).findall(".//item")
    if not items:
        return None
    pick = items[0]
    for it in items:
        try:
            if dt.datetime.strptime((it.findtext("title") or "").strip(), "%A, %d %B %Y").date() == today:
                pick = it
                break
        except ValueError:
            continue
    text = _plain(pick.findtext("description") or "")
    head, _, ratings = text.partition("Fire Danger Ratings")
    want = district.strip().lower()
    rating = _district_lines(ratings).get(want)
    ban = _district_lines(head).get(want, "")
    if rating is None and not ban:
        return None
    rating = (rating or "").strip()
    return {"district": next((d for d in DISTRICTS if d.lower() == want), district), "day": (pick.findtext("title") or "").strip(),
            "rating": rating.capitalize() if rating and rating.upper() != "NO RATING" else "",
            "total_fire_ban": ban.upper().startswith("YES")}


def home_status() -> dict:
    """What the tablet's Home weather panel shows: today's danger rating, any fire ban, and warnings near home."""
    district = str(store.get("fire_district") or "Central")
    out: dict = {"district": district, "rating": "", "total_fire_ban": False}
    try:
        out.update(parse_fdr(fetch_fdr(), district) or {})
    except Exception:  # noqa: BLE001 — the panel just leaves the rating out
        pass
    try:
        km = radius()
        items = items_near(km) if home() else []
        out.update({"home_known": bool(home()), "radius_km": km, "near": len(items),
                    "top": describe(items[0]) if items else "",
                    "top_level": items[0]["level"] if items else 0,
                    "covers_home": bool(items and items[0]["covers_home"])})
    except Exception:  # noqa: BLE001
        pass
    return out
