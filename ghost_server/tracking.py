"""Where's Adam? — family locations from Home Assistant, with a live map.

Each phone's Home Assistant app (Android or iPhone) reports its location to a
`person.*` entity. Ghost reads those:
  * "where's Hayley?" → place name (a Home Assistant zone like Work, or the street
    and suburb), how long ago, moving or not, distance and driving time from home
  * a live map link (/track/<token>) opens on the asking screen: the dot moves as
    new positions arrive, with the trail, home, and "Open in Google Maps" buttons
  * while the map is open, Ghost asks the phone for a fresh position every 30 s
    (the app's `request_location_update` notification), so it moves in near real time
A map link lasts an hour (or ends once the person gets home), and every look is
logged as an event so the household can see who checked.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass, field

import httpx

from . import store

LINK_TTL = 3600                 # s a live map link works
PING_EVERY = 30                 # s between "send a fresh location" requests while a map is open
TRAIL_MINUTES = 90              # how much history the map shows
UA = {"User-Agent": "GhostServer/1.0 (family home assistant)"}


def _ha(method: str, path: str, **kw):
    from .tools.home import _req
    return _req(method, path, **kw)


# ── people ───────────────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower().replace("_", " ")).split())


def people(states: list[dict] | None = None) -> list[dict]:
    states = states if states is not None else _ha("GET", "/api/states").json()
    return [s for s in states if str(s.get("entity_id", "")).startswith("person.")]


def find_person(name: str, states: list[dict] | None = None) -> dict | None:
    want = _norm(name)
    want = re.sub(r"^(my |the )", "", want)
    ps = people(states)
    for exact in (True, False):
        for p in ps:
            names = {_norm(p["attributes"].get("friendly_name")), _norm(p["entity_id"].split(".", 1)[1])}
            first = {n.split(" ")[0] for n in names if n}
            if exact and (want in names or want in first):
                return p
            if not exact and any(want and (want in n or n in want) for n in names | first if n):
                return p
    return None


def _zones(states: list[dict]) -> list[dict]:
    return [s for s in states if str(s.get("entity_id", "")).startswith("zone.")]


def home_point(states: list[dict]) -> tuple[float, float] | None:
    for z in _zones(states):
        if z["entity_id"] == "zone.home":
            a = z["attributes"]
            return float(a["latitude"]), float(a["longitude"])
    return None


def distance_km(a, b) -> float:
    (la1, lo1), (la2, lo2) = [(math.radians(p[0]), math.radians(p[1])) for p in (a, b)]
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, h)))


def position(p: dict) -> tuple[float, float] | None:
    a = p.get("attributes") or {}
    try:
        lat, lon = float(a["latitude"]), float(a["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def _when(iso: str) -> float:
    try:
        return dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def ago(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60} minute{'s' * (s // 60 != 1)} ago"
    h = s // 3600
    return f"{h} hour{'s' * (h != 1)} ago"


# ── place names and driving time ─────────────────────────────────────────────
_geo_cache: dict[tuple, tuple[float, str]] = {}
_geo_lock = threading.Lock()
_geo_last = [0.0]


def place_name(lat: float, lon: float) -> str:
    """Street and suburb for a point (OpenStreetMap), cached, at most one lookup a second."""
    key = (round(lat, 3), round(lon, 3))
    with _geo_lock:
        hit = _geo_cache.get(key)
        if hit and time.time() - hit[0] < 6 * 3600:
            return hit[1]
        wait = 1.05 - (time.time() - _geo_last[0])
        if wait > 0:
            time.sleep(wait)
        _geo_last[0] = time.time()
    try:
        r = httpx.get("https://nominatim.openstreetmap.org/reverse", headers=UA, timeout=6,
                      params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 17, "addressdetails": 1})
        a = r.json().get("address") or {}
        road = a.get("road") or a.get("pedestrian") or a.get("footway") or ""
        area = a.get("suburb") or a.get("town") or a.get("village") or a.get("city_district") or a.get("city") or ""
        name = ", ".join(x for x in (road, area) if x) or str(r.json().get("display_name", ""))[:60]
    except Exception:  # noqa: BLE001
        name = ""
    with _geo_lock:
        _geo_cache[key] = (time.time(), name)
    return name


_eta_cache: dict[str, tuple[float, tuple, int | None]] = {}


def drive_minutes(frm: tuple[float, float], to: tuple[float, float], key: str = "") -> int | None:
    """Driving time (minutes) by road, from the public OSRM router; a rough estimate if it's down."""
    hit = _eta_cache.get(key)
    if key and hit and time.time() - hit[0] < 60 and distance_km(hit[1], frm) < .3:
        return hit[2]
    mins = None
    try:
        r = httpx.get(f"https://router.project-osrm.org/route/v1/driving/{frm[1]},{frm[0]};{to[1]},{to[0]}",
                      params={"overview": "false"}, headers=UA, timeout=6)
        routes = r.json().get("routes") or []
        if routes:
            mins = max(1, round(routes[0]["duration"] / 60))
    except Exception:  # noqa: BLE001
        pass
    if mins is None:
        mins = max(1, round(distance_km(frm, to) * 1.3 / 45 * 60))     # roads wind; ~45 km/h average
    if key:
        _eta_cache[key] = (time.time(), frm, mins)
    return mins


# ── trails ───────────────────────────────────────────────────────────────────
_trails: dict[str, list[tuple[float, float, float]]] = {}       # entity → [(t, lat, lon)]
_trail_lock = threading.Lock()


def _remember(entity: str, t: float, pt: tuple[float, float]) -> None:
    with _trail_lock:
        tr = _trails.setdefault(entity, [])
        if any(abs(x[0] - t) < 1 and (x[1], x[2]) == pt for x in tr):
            return
        if tr and t >= tr[-1][0] and (tr[-1][1], tr[-1][2]) == pt:
            return                                       # standing still: one point is enough
        tr.append((t, pt[0], pt[1]))
        tr.sort(key=lambda x: x[0])
        cut = time.time() - TRAIL_MINUTES * 60
        _trails[entity] = [x for x in tr if x[0] >= cut][-600:]


_seeded: set[str] = set()


def seed_trail(entity: str) -> None:
    """Fill in the trail from Home Assistant's history the first time someone is looked at."""
    if entity in _seeded:
        return
    _seeded.add(entity)
    start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=TRAIL_MINUTES)).isoformat()
    try:
        rows = _ha("GET", f"/api/history/period/{start}", params={"filter_entity_id": entity}).json()
    except Exception:  # noqa: BLE001
        _seeded.discard(entity)
        return
    for row in (rows[0] if rows else []):
        pt = position(row)
        if pt:
            _remember(entity, _when(row.get("last_updated") or row.get("last_changed")), pt)


def trail(entity: str) -> list[list[float]]:
    with _trail_lock:
        return [[round(la, 6), round(lo, 6)] for _, la, lo in _trails.get(entity, [])]


def speed_kmh(entity: str, p: dict) -> float | None:
    """From the phone if it reports speed, else from the last two positions."""
    a = p.get("attributes") or {}
    for k in ("speed",):
        try:
            v = float(a[k])
            if v >= 0:
                return v * 3.6 if v < 70 else v          # phones report m/s; some trackers km/h
        except (KeyError, TypeError, ValueError):
            pass
    with _trail_lock:
        tr = _trails.get(entity, [])[-2:]
    if len(tr) == 2 and tr[1][0] - tr[0][0] > 5 and time.time() - tr[1][0] < 300:
        return distance_km(tr[0][1:], tr[1][1:]) / ((tr[1][0] - tr[0][0]) / 3600)
    return None


# ── asking the phone for a fresh position ────────────────────────────────────
_pinged: dict[str, float] = {}


def notify_service(p: dict) -> str:
    src = str((p.get("attributes") or {}).get("source") or "")
    return "mobile_app_" + src.split(".", 1)[1] if src.startswith("device_tracker.") else ""


def ping(p: dict, every: float = PING_EVERY) -> bool:
    svc = notify_service(p)
    if not svc or time.time() - _pinged.get(svc, 0) < every:
        return False
    _pinged[svc] = time.time()
    try:
        r = _ha("POST", f"/api/services/notify/{svc}", json={"message": "request_location_update"})
        return r.status_code < 300
    except Exception:  # noqa: BLE001
        return False


# ── the answer ───────────────────────────────────────────────────────────────
def snapshot(p: dict, states: list[dict], with_place: bool = True) -> dict:
    """Everything the voice answer and the live map need about one person."""
    a = p.get("attributes") or {}
    name = str(a.get("friendly_name") or p["entity_id"].split(".", 1)[1].replace("_", " ").title())
    pt = position(p)
    now = time.time()
    updated = _when(p.get("last_updated") or p.get("last_changed") or "")
    if pt:
        _remember(p["entity_id"], updated or now, pt)
    state = str(p.get("state") or "unknown")
    home = home_point(states)
    zone = None
    if state not in ("home", "not_home", "unknown", "unavailable"):
        zone = state
    out = {"entity": p["entity_id"], "name": name, "state": state, "at_home": state == "home",
           "lat": pt[0] if pt else None, "lon": pt[1] if pt else None, "acc": a.get("gps_accuracy"),
           "updated": updated, "ago": ago(now - updated) if updated else "", "zone": zone,
           "home": list(home) if home else None, "picture": a.get("entity_picture") if str(a.get("entity_picture") or "").startswith("https://") else ""}
    out["place"] = ("home" if state == "home" else zone or (place_name(*pt) if pt and with_place else ""))
    spd = speed_kmh(p["entity_id"], p)
    out["kmh"] = round(spd) if spd is not None else None
    out["moving"] = bool(spd and spd > 7)
    if pt and home and state != "home":
        out["km"] = round(distance_km(pt, home), 1)
        out["eta_min"] = drive_minutes(pt, home, p["entity_id"]) if out["km"] > .3 else 0
    return out


def describe(s: dict) -> str:
    if s["lat"] is None and not s["at_home"]:
        return f"I can't see where {s['name']} is — their phone isn't sharing a location with Home Assistant."
    if s["at_home"]:
        return f"{s['name']} is at home (updated {s['ago']})."
    where = f"at {s['zone']}" if s["zone"] else (f"near {s['place']}" if s["place"] else "out")
    bits = [f"{s['name']} is {where}"]
    if s["moving"]:
        bits.append(f"moving at about {s['kmh']} km/h")
    if s.get("km") is not None:
        bits.append(f"{s['km']:.0f} km from home, about {s['eta_min']} minutes' drive" if s["km"] >= 1
                    else f"{int(s['km'] * 1000)} m from home")
    return ", ".join(bits) + f" (updated {s['ago']})."


# ── live map links ───────────────────────────────────────────────────────────
@dataclass
class Link:
    entity: str
    until: float
    asked_by: str = ""
    made: float = field(default_factory=time.time)


_links: dict[str, Link] = {}
_links_lock = threading.Lock()


def issue(entity: str, asked_by: str = "", ttl: float = LINK_TTL) -> str:
    tok = secrets.token_urlsafe(18)
    now = time.time()
    with _links_lock:
        for k in [k for k, v in _links.items() if v.until < now]:
            _links.pop(k, None)
        _links[tok] = Link(entity, now + ttl, asked_by)
    return tok


def link(tok: str) -> Link | None:
    with _links_lock:
        v = _links.get(tok)
    return v if v and v.until > time.time() else None


def live(tok: str) -> dict | None:
    """What the map page polls: the latest position, trail and ETA (and keeps the phone reporting)."""
    ln = link(tok)
    if not ln or not store.get("tracking_enabled"):
        return None
    states = _ha("GET", "/api/states").json()
    p = next((s for s in states if s.get("entity_id") == ln.entity), None)
    if not p:
        return None
    seed_trail(ln.entity)
    s = snapshot(p, states)
    if s["at_home"]:
        with _links_lock:                 # they're home: the live view has done its job
            ln.until = min(ln.until, time.time() + 120)
    else:
        threading.Thread(target=ping, args=(p,), daemon=True).start()
    s["trail"] = trail(ln.entity)
    s["expires_in"] = int(ln.until - time.time())
    return s


def page(tok: str) -> str:
    from pathlib import Path
    html = (Path(__file__).parent / "static" / "track.html").read_text(encoding="utf-8")
    import re
    key = re.sub(r"[^A-Za-z0-9_\-]", "", str(store.get("google_maps_key") or ""))[:80]   # only key characters reach the page
    return html.replace("__TOKEN__", tok).replace("__GMAPS_KEY__", key)
