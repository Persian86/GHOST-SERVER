import datetime as dt

import pytest

from ghost_server import family_calendar as fc, store

ICS_FAMILY = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20261124T093000
SUMMARY:Dentist
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261125
SUMMARY:Bin night
END:VEVENT
BEGIN:VEVENT
DTSTART:20261123T170000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE
SUMMARY:Soccer
END:VEVENT
END:VCALENDAR"""
ICS_HAYLEY = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20261124T080000
SUMMARY:Work
END:VEVENT
END:VCALENDAR"""


@pytest.fixture
def cal(tmp_path, monkeypatch):
    store.init(tmp_path / "cal.db")
    store.put("calendars", "https://example.test/family.ics")
    fc._cache.clear()
    monkeypatch.setattr(fc, "sources", lambda: [
        {"name": "Family", "urls": ["f"], "colour": fc.PALETTE[0]},
        {"name": "Hayley", "urls": ["h"], "colour": fc.PALETTE[1]}])
    monkeypatch.setattr(fc, "_fetch", lambda url: {"f": ICS_FAMILY, "h": ICS_HAYLEY}[url])


def test_week_has_each_persons_events_in_their_colour(cal):
    w = fc.days(dt.date(2026, 11, 23), 7)
    assert w["connected"] and [m["name"] for m in w["members"]] == ["Family", "Hayley"]
    by = {d["date"]: d["events"] for d in w["days"]}
    assert len(w["days"]) == 7
    tue = by["2026-11-24"]
    assert [(e["time"], e["title"], e["who"]) for e in tue] == [("08:00", "Work", "Hayley"), ("09:30", "Dentist", "Family")]
    assert tue[0]["colour"] == fc.PALETTE[1]
    wed = by["2026-11-25"]
    assert wed[0]["all_day"] and wed[0]["title"] == "Bin night"          # all-day first
    assert [e["title"] for e in by["2026-11-23"]] == ["Soccer"] and [e["title"] for e in wed][1] == "Soccer"   # weekly repeats


def test_range_is_capped(cal):
    assert len(fc.days(dt.date(2026, 11, 1), 500)["days"]) == fc.MAX_DAYS
    assert len(fc.days(dt.date(2026, 11, 1), 0)["days"]) == 1


def test_sources_give_family_then_people_their_own_colours(tmp_path, monkeypatch):
    store.init(tmp_path / "src.db")
    store.put("calendars", "https://a.test/one.ics https://a.test/two.ics")
    from ghost_server import faces
    monkeypatch.setattr(faces, "people", lambda: [{"name": "Zoey", "calendar": ""}, {"name": "Adam", "calendar": "webcal://a.test/adam.ics"}])
    s = fc.sources()
    assert [x["name"] for x in s] == ["Family", "Adam"] and len(s[0]["urls"]) == 2
    assert s[0]["colour"] != s[1]["colour"]


def test_route(cal):
    from fastapi.testclient import TestClient
    from ghost_server import security
    from ghost_server.app import app
    security.reset_all(); store.put("approve_new_devices", False)
    _, key = store.redeem_pair_code(store.new_pair_code(), "Kitchen hub", "tablet")
    c = TestClient(app)
    h = {"Authorization": "Bearer " + key}
    r = c.get("/api/v1/calendar?start=2026-11-23&days=7", headers=h)
    assert r.status_code == 200 and r.json()["days"][1]["events"][0]["title"] == "Work"
    assert c.get("/api/v1/calendar?start=nope", headers=h).status_code == 400
    assert c.get("/api/v1/calendar").status_code == 401
