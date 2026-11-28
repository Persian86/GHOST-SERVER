import pytest

from ghost_server import firewatch, store
from ghost_server.tools import Ctx, TOOLS, load

HOME = (-37.5774, 144.7261)            # Sunbury


def feat(kind, lat, lon, **props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"feedType": kind, **props}}


def warning(lat, lon, level, event="Bushfire", poly=None, **props):
    geoms = [{"type": "Point", "coordinates": [lon, lat]}]
    if poly:
        geoms.append({"type": "Polygon", "coordinates": [poly]})
    return {"type": "Feature", "geometry": {"type": "GeometryCollection", "geometries": geoms},
            "properties": {"feedType": "warning", "sourceTitle": level, "category1": level, "category2": "Fire",
                           "cap": {"event": event}, **props}}


# a square around Sunbury, about 11 km across
AROUND_HOME = [[144.66, -37.63], [144.79, -37.63], [144.79, -37.52], [144.66, -37.52], [144.66, -37.63]]

FEED = [
    feat("incident", -37.53, 144.68, id=1, category1="Fire", category2="Grass Fire", location="Diggers Rest",
         status="Going", sizeFmt="4 Ha."),
    feat("incident", -37.60, 144.75, id=2, category1="Planned Burn", category2="Planned Burn", location="Jacksons Hill"),
    feat("incident", -37.58, 144.74, id=3, category1="Tree Down", category2="Tree Down", location="Main St"),
    feat("incident", -36.0, 146.0, id=4, category1="Fire", category2="Bushfire", location="Far away"),
    warning(-37.70, 144.60, "Watch and Act", id=10, location="Melton South"),
    warning(-37.90, 145.50, "Advice", event="Riverine Flood", id=11, location="Yarra Junction"),
    {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [AROUND_HOME]},
     "properties": {"feedType": "burn-area", "category1": "Burn Area", "location": "Old burn"}},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    store.init(tmp_path / "fire.db")
    monkeypatch.setattr(firewatch, "home", lambda: HOME)
    monkeypatch.setattr(firewatch, "fetch", lambda max_age=120: FEED)
    return FEED


def test_geometry_helpers():
    assert firewatch.distance_km(HOME, (-37.8136, 144.9631)) == pytest.approx(33, abs=2)   # Sunbury → Melbourne CBD
    assert firewatch.direction(HOME, (-37.8136, 144.9631)) == "south-east"
    assert firewatch.inside({"type": "Polygon", "coordinates": [AROUND_HOME]}, HOME)
    assert not firewatch.inside({"type": "Polygon", "coordinates": [AROUND_HOME]}, (-37.9, 145.0))


def test_items_near_keeps_fires_and_warnings_drops_noise(env):
    items = firewatch.items_near(20)
    ids = [i["id"] for i in items]
    assert "warning:10" in ids and "incident:1" in ids and "incident:2" in ids
    assert "incident:3" not in ids           # not a fire
    assert "incident:4" not in ids           # too far
    assert "warning:11" not in ids           # flood warning 70 km away
    assert not any(i["id"].startswith("burn") for i in items)
    assert items[0]["id"] == "warning:10"     # warnings first
    grass = next(i for i in items if i["id"] == "incident:1")
    assert grass["dir"] == "north-west" and 3 < grass["km"] < 8


def test_warning_area_covering_home_counts_even_beyond_radius(env):
    far = warning(-38.5, 146.0, "Emergency Warning", poly=AROUND_HOME, id=20, location="Big fire")
    items = firewatch.items_near(5, feats=[far], at=HOME)
    assert items and items[0]["covers_home"] and items[0]["level"] == 3
    assert "inside the warning area" in firewatch.describe(items[0])


def test_summary_reads_out_and_points_to_official_source(env):
    text = firewatch.summary(20)
    assert "Watch and Act: Bushfire at Melton South" in text
    assert "Grass Fire at Diggers Rest" in text and "status going" in text
    assert "planned burn" in text and "VicEmergency" in text


def test_flood_warning_named_by_its_event(env):
    flood = warning(-37.58, 144.73, "Advice", event="Riverine Flood", id=12, location="Jacksons Creek")
    flood["properties"]["category2"] = "Met"
    item = firewatch.items_near(20, feats=[flood], at=HOME)[0]
    assert item["what"] == "Riverine Flood" and not item["fire"]


def test_summary_when_quiet(env, monkeypatch):
    monkeypatch.setattr(firewatch, "fetch", lambda max_age=120: [])
    assert firewatch.summary(20).startswith("No fires or emergency warnings within 20 km")


def test_alerts_are_announced_once_and_again_when_escalated(env, monkeypatch):
    store.put("fire_radius_km", 20)                 # the sample warnings are 5–20 km out
    msgs = firewatch.check_once(now=1000)
    urgent = [m for m in msgs if m["urgent"]]
    calm = [m for m in msgs if not m["urgent"]]
    assert len(urgent) == 1 and "Watch and Act" in urgent[0]["text"]
    assert all(m["type"] == "notify" and "safety" not in m for m in msgs)  # notifications only, never read out
    assert urgent[0]["title"].startswith("⚠ VicEmergency")
    assert len(calm) == 1 and "Diggers Rest" in calm[0]["text"]          # the nearby grass fire, not urgent
    assert not any("Jacksons Hill" in m["text"] for m in msgs)            # planned burns are never announced
    assert firewatch.check_once(now=1300) == []                           # nothing new → silence
    escalated = [dict(f) for f in FEED]
    escalated[4] = warning(-37.70, 144.60, "Emergency Warning", id=10, location="Melton South")
    monkeypatch.setattr(firewatch, "fetch", lambda max_age=120: escalated)
    again = firewatch.check_once(now=1600)
    assert len(again) == 1 and again[0]["urgent"] and "Emergency Warning" in again[0]["text"]


def test_fires_beyond_alert_distance_are_not_announced_but_listed(env, monkeypatch):
    fire = feat("incident", -37.40, 144.55, id=30, category1="Fire", category2="Scrub Fire", location="Gisborne South")
    monkeypatch.setattr(firewatch, "fetch", lambda max_age=120: [fire])
    assert firewatch.items_near(30)                    # ~25 km away: listed when asked
    assert firewatch.check_once(now=1) == []           # but not announced on its own


def test_old_seen_items_are_forgotten(env):
    seen = {"incident:old": {"level": 0, "at": 0}}
    firewatch.to_announce([], seen, now=firewatch.SEEN_KEEP + 10)
    assert seen == {}


def test_home_setting_overrides_city(tmp_path, monkeypatch):
    store.init(tmp_path / "home.db")
    store.put("fire_home", "-37.6, 144.7")
    assert firewatch.home() == (-37.6, 144.7)


def test_tools(env):
    load()
    out = TOOLS["fires_near_me"].handler({"radius_km": 20, "fires_only": True}, Ctx())
    assert "Diggers Rest" in out and "Yarra" not in out
    off = TOOLS["fire_alerts"].handler({"on": False}, Ctx())
    assert "off" in off and store.get("fire_alerts") is False
    on = TOOLS["fire_alerts"].handler({"on": True, "radius_km": 30}, Ctx())
    assert "30 km" in on and store.get("fire_radius_km") == 30


CFA_SAMPLE = """<?xml version="1.0"?><rss><channel>
<item><title>Tuesday, 24 November 2026</title><description>&lt;p&gt;Today, Tue, 24 Nov 2026 is not currently a day of Total Fire Ban.&lt;br&gt;Mallee: NO - RESTRICTIONS MAY APPLY&lt;br&gt;Central: NO - RESTRICTIONS MAY APPLY&lt;br&gt;North Central: YES - TOTAL FIRE BAN IN FORCE&lt;/p&gt;&lt;p&gt;Fire Danger Ratings&lt;br&gt;Bureau of Meteorology forecast issued at: Monday, 23 November 2026 16:02 PM.&lt;br&gt;Mallee: HIGH&lt;br&gt;Central: MODERATE&lt;br&gt;North Central: EXTREME&lt;br&gt;West and South Gippsland: NO RATING&lt;/p&gt;</description></item>
<item><title>Wednesday, 25 November 2026</title><description>&lt;p&gt;Central: NO - RESTRICTIONS MAY APPLY&lt;/p&gt;&lt;p&gt;Fire Danger Ratings&lt;br&gt;Central: HIGH&lt;/p&gt;</description></item>
</channel></rss>"""


def test_cfa_rating_and_ban_for_a_district_today():
    import datetime as dt
    r = firewatch.parse_fdr(CFA_SAMPLE, "Central", dt.date(2026, 11, 24))
    assert r["rating"] == "Moderate" and r["total_fire_ban"] is False and r["district"] == "Central"
    r = firewatch.parse_fdr(CFA_SAMPLE, "north central", dt.date(2026, 11, 24))
    assert r["rating"] == "Extreme" and r["total_fire_ban"] is True and r["district"] == "North Central"


def test_cfa_picks_the_matching_day_and_handles_no_rating():
    import datetime as dt
    assert firewatch.parse_fdr(CFA_SAMPLE, "Central", dt.date(2026, 11, 25))["rating"] == "High"
    assert firewatch.parse_fdr(CFA_SAMPLE, "West and South Gippsland", dt.date(2026, 11, 24))["rating"] == ""
    assert firewatch.parse_fdr(CFA_SAMPLE, "Atlantis", dt.date(2026, 11, 24)) is None


def test_home_status_survives_feeds_being_down(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr(firewatch, "fetch_fdr", boom)
    monkeypatch.setattr(firewatch, "home", lambda: (-37.58, 144.73))
    monkeypatch.setattr(firewatch, "items_near", boom)
    out = firewatch.home_status()
    assert out["rating"] == "" and out["total_fire_ban"] is False and "near" not in out
