import time

import httpx
import pytest

from ghost_server import access, store, tracking
from ghost_server.tools import Ctx, TOOLS, load

HOME = (-37.5774, 144.7261)


def iso(t):
    import datetime as dt
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()


class FakeHA:
    def __init__(self):
        now = time.time()
        self.calls = []
        self.states = [
            {"entity_id": "zone.home", "state": "0", "attributes": {"latitude": HOME[0], "longitude": HOME[1], "friendly_name": "Home"}},
            {"entity_id": "zone.work", "state": "0", "attributes": {"latitude": -37.81, "longitude": 144.96, "friendly_name": "Work"}},
            {"entity_id": "person.adam", "state": "not_home", "last_updated": iso(now - 40),
             "attributes": {"friendly_name": "Adam", "latitude": -37.70, "longitude": 144.85, "gps_accuracy": 12,
                            "source": "device_tracker.adams_pixel", "speed": 22.0}},
            {"entity_id": "person.hayley", "state": "home", "last_updated": iso(now - 600),
             "attributes": {"friendly_name": "Hayley", "latitude": HOME[0], "longitude": HOME[1],
                            "source": "device_tracker.hayleys_iphone"}},
            {"entity_id": "person.zoey", "state": "Work", "last_updated": iso(now - 120),
             "attributes": {"friendly_name": "Zoey", "latitude": -37.81, "longitude": 144.96}},
        ]
        self.history = [[{"last_updated": iso(now - 900), "attributes": {"latitude": -37.80, "longitude": 144.95}},
                         {"last_updated": iso(now - 300), "attributes": {"latitude": -37.75, "longitude": 144.90}}]]

    def __call__(self, method, path, **kw):
        self.calls.append((method, path, kw))
        body = self.states if path == "/api/states" else self.history if path.startswith("/api/history") else {}
        return httpx.Response(200, json=body, request=httpx.Request(method, "http://ha" + path))


@pytest.fixture
def ha(tmp_path, monkeypatch):
    store.init(tmp_path / "track.db")
    fake = FakeHA()
    monkeypatch.setattr(tracking, "_ha", fake)
    tracking._trails.clear(); tracking._seeded.clear(); tracking._links.clear(); tracking._pinged.clear()
    tracking._eta_cache.clear(); tracking._geo_cache.clear()

    def web(url, **kw):
        if "nominatim" in url:
            return httpx.Response(200, json={"address": {"road": "Calder Freeway", "suburb": "Keilor"}}, request=httpx.Request("GET", url))
        if "osrm" in url:
            return httpx.Response(200, json={"routes": [{"duration": 14 * 60}]}, request=httpx.Request("GET", url))
        raise AssertionError(url)
    monkeypatch.setattr(httpx, "get", web)
    return fake


def test_find_person_by_name_and_first_name(ha):
    assert tracking.find_person("Adam", ha.states)["entity_id"] == "person.adam"
    assert tracking.find_person("my hayley", ha.states)["entity_id"] == "person.hayley"
    assert tracking.find_person("Bob", ha.states) is None


def test_describe_out_moving(ha):
    p = tracking.find_person("adam", ha.states)
    s = tracking.snapshot(p, ha.states)
    assert s["place"] == "Calder Freeway, Keilor" and s["moving"] and s["kmh"] == 79
    assert s["eta_min"] == 14 and 15 < s["km"] < 20
    text = tracking.describe(s)
    assert "Adam is near Calder Freeway, Keilor" in text and "79 km/h" in text and "14 minutes' drive" in text


def test_describe_home_and_zone(ha):
    assert tracking.describe(tracking.snapshot(tracking.find_person("Hayley", ha.states), ha.states)).startswith("Hayley is at home")
    assert "Zoey is at Work" in tracking.describe(tracking.snapshot(tracking.find_person("Zoey", ha.states), ha.states))


def test_where_is_tool_opens_live_map_and_pings_phone(ha):
    load()
    out = TOOLS["where_is"].handler({"person": "Adam"}, Ctx(device_id="", device_name="Kitchen tablet"))
    assert "Adam is near" in out
    assert ("POST", "/api/services/notify/mobile_app_adams_pixel") in [(m, p) for m, p, _ in ha.calls]
    tok = next(iter(tracking._links))
    live = tracking.live(tok)
    assert live["name"] == "Adam" and live["lat"] == -37.70 and len(live["trail"]) >= 3   # history + now
    assert live["home"] == list(HOME) and 0 < live["expires_in"] <= tracking.LINK_TTL
    assert store.recent_events(5, "location_viewed")


def test_everyone(ha):
    load()
    out = TOOLS["where_is"].handler({"person": "everyone"}, Ctx())
    assert "Adam" in out and "Hayley is at home" in out and "Zoey is at Work" in out


def test_link_ends_soon_after_arriving_home(ha):
    tok = tracking.issue("person.hayley")
    tracking.live(tok)
    assert tracking._links[tok].until <= time.time() + 121


def test_expired_and_disabled(ha):
    tok = tracking.issue("person.adam", ttl=-1)
    assert tracking.link(tok) is None and tracking.live(tok) is None
    tok = tracking.issue("person.adam")
    store.put("tracking_enabled", False)
    assert tracking.live(tok) is None
    load()
    assert "turned off" in TOOLS["where_is"].handler({"person": "Adam"}, Ctx())


def test_pings_are_rate_limited(ha):
    p = tracking.find_person("Adam", ha.states)
    assert tracking.ping(p) and not tracking.ping(p)


def test_routes(ha):
    from fastapi.testclient import TestClient
    from ghost_server.app import app
    client = TestClient(app)
    tok = tracking.issue("person.adam")
    page = client.get(f"/track/{tok}")
    assert page.status_code == 200 and tok in page.text and "maps.google.com" in page.text
    assert page.headers["cache-control"] == "no-store"
    live = client.get(f"/track/{tok}/live")
    assert live.status_code == 200 and live.json()["name"] == "Adam"
    assert client.get("/track/nope").status_code == 410
    assert client.get("/track/nope/live").status_code == 410


def test_children_can_ask_guests_cannot(ha, monkeypatch):
    store.put("household_access", True)
    access.schema()
    monkeypatch.setattr(store, "device", lambda did: {"revoked": 0, "approved": 1})
    for did, role in (("kid", "child"), ("visitor", "guest"), ("mum", "adult")):
        store.x("INSERT OR REPLACE INTO device_access VALUES (?,?)", (did, role))
    assert access.tool_allowed("kid", "where_is")
    assert not access.tool_allowed("visitor", "where_is")
    assert access.tool_allowed("mum", "where_is")
    assert not access.tool_allowed("kid", "fire_alerts")        # settings stay with adults


def test_phone_app_endpoints(ha, monkeypatch):
    from fastapi.testclient import TestClient
    from ghost_server import firewatch, security
    from ghost_server.app import app
    store.put("fire_radius_km", 20)                 # the sample fires are further than the 5 km default
    security.reset_all(); store.put("approve_new_devices", False)
    did, key = store.redeem_pair_code(store.new_pair_code(), "Adam's phone", "phone")
    h = {"Authorization": "Bearer " + key}
    c = TestClient(app)
    ppl = c.get("/api/v1/people", headers=h)
    assert ppl.status_code == 200
    names = {p["name"]: p for p in ppl.json()["people"]}
    assert names["Hayley"]["text"].startswith("Hayley is at home") and names["Adam"]["eta_min"] == 14
    tr = c.post("/api/v1/people/track", json={"person": "Adam"}, headers=h)
    assert tr.status_code == 200 and tr.json()["path"].startswith("/track/") and tr.json()["title"] == "Adam · live"
    assert c.get(tr.json()["path"] + "/live").json()["name"] == "Adam"
    assert c.post("/api/v1/people/track", json={"person": "Bob"}, headers=h).status_code == 404
    monkeypatch.setattr(firewatch, "home", lambda: HOME)
    monkeypatch.setattr(firewatch, "fetch", lambda max_age=120: [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [144.68, -37.53]},
         "properties": {"feedType": "incident", "id": 1, "category1": "Fire", "category2": "Grass Fire", "location": "Diggers Rest"}}])
    f = c.get("/api/v1/fires", headers=h).json()
    assert f["items"][0]["text"].startswith("Grass Fire at Diggers Rest") and f["alerts_on"]
    # guests can see fires but not family locations; children can see both
    store.put("household_access", True); access.schema()
    store.x("INSERT OR REPLACE INTO device_access VALUES (?,?)", (did, "guest"))
    assert c.get("/api/v1/people", headers=h).status_code == 403
    assert c.get("/api/v1/fires", headers=h).status_code == 200
    store.x("INSERT OR REPLACE INTO device_access VALUES (?,?)", (did, "child"))
    assert c.get("/api/v1/people", headers=h).status_code == 200
    store.put("tracking_enabled", False)
    assert c.get("/api/v1/people", headers=h).status_code == 403
