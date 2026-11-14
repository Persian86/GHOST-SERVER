import datetime as dt
import threading
import time

import httpx
import pytest

from ghost_server import dashboard
from ghost_server.dashboard_cache import PanelCache, wait_briefly


def test_slow_news_does_not_hold_up_fresh_weather(monkeypatch):
    cache = PanelCache({}, threading.Lock())
    monkeypatch.setattr(dashboard, "_panels", cache)
    monkeypatch.setattr(dashboard, "wait_briefly", lambda events: wait_briefly(events, .08))
    monkeypatch.setattr(dashboard.store, "get", lambda key, default=None: {"city": "Melbourne"}.get(key, default))
    monkeypatch.setattr(dashboard, "_theme", lambda: {})
    monkeypatch.setattr(dashboard, "weather_data", lambda city: {"temp": 15, "code": 61, "desc": "Rain"})
    monkeypatch.setattr(dashboard, "calendar_data", lambda: {"connected": False})
    monkeypatch.setattr(dashboard, "home_tiles", lambda: {"connected": False, "tiles": []})
    release = threading.Event()
    calls = []
    def news():
        calls.append(1)
        release.wait(3)
        return [{"title": "A real result", "source": "Test publisher"}]
    monkeypatch.setattr(dashboard, "headlines", news)
    try:
        before = time.monotonic()
        result = dashboard.build(partial=True)
        assert time.monotonic() - before < .7
        assert result["weather"]["code"] == 61
        assert result["panel_status"]["headlines"] == "loading"
        assert result["weather_updated_at"] > 0
        done = cache.active["headlines"]
        dashboard.build(partial=True)
        assert len(calls) == 1
    finally:
        release.set()
        if "done" in locals():
            assert done.wait(2)
    result = dashboard.build(partial=True)
    assert result["headlines"][0]["title"] == "A real result"
    assert result["panel_status"]["headlines"] == "ready"


def test_failed_refresh_keeps_previous_report_and_bounds_retry():
    values = {"weather": (time.time() - 1000, {"code": 3})}
    cache = PanelCache(values, threading.Lock())
    calls = []
    def fail():
        calls.append(1)
        raise RuntimeError("an upstream error containing a private credential")
    event = cache.start("weather", 1, fail)
    assert event.wait(1)
    value, state, stamp = cache.snapshot("weather")
    assert value == {"code": 3} and state == "stale" and stamp == values["weather"][0]
    assert "credential" not in repr(cache.snapshot("weather"))
    assert cache.start("weather", 1, fail) is None
    assert len(calls) == 1


def test_empty_news_retries_earlier_than_successful_news(monkeypatch):
    from ghost_server import dashboard_cache
    clock = [1000.0]
    monkeypatch.setattr(dashboard_cache.time, "time", lambda: clock[0])
    cache = PanelCache({}, threading.Lock())
    assert cache.start("headlines", 1800, lambda: []).wait(1)
    clock[0] += 61
    assert cache.start("headlines", 1800, lambda: [{"title": "Recovered"}]).wait(1)
    assert cache.snapshot("headlines")[1] == "ready"


@pytest.mark.parametrize("current,expected", [
    ({"weather_code": 3, "rain": .2}, 61),
    ({"weather_code": 0, "precipitation": .1}, 61),
    ({"weather_code": 3, "showers": .3}, 61),
    ({"weather_code": 95, "rain": .2}, 95),
    ({"weather_code": 3, "snowfall": .1}, 73),
    ({"weather_code": 73, "rain": .2}, 73),
    ({"weather_code": 3, "precipitation_probability": 100}, 3),
    ({"weather_code": 3, "rain": 0, "precipitation": 0}, 3),
])
def test_current_precipitation_takes_priority_over_cloud_code(current, expected):
    assert dashboard.effective_weather_code(current) == expected


def test_weather_fetch_requests_current_rain_and_keeps_report_time(monkeypatch):
    def get(url, **kwargs):
        if "geocoding" in url:
            return httpx.Response(200, json={"results": [{"name": "Melbourne", "latitude": -37.8, "longitude": 145}]})
        assert "precipitation" in kwargs["params"]["current"] and "rain" in kwargs["params"]["current"]
        return httpx.Response(200, json={"utc_offset_seconds": 39600, "current": {
            "time": "2026-11-14T11:45", "temperature_2m": 15, "weather_code": 3,
            "rain": .3, "precipitation": .3, "is_day": 1}, "daily": {}})
    monkeypatch.setattr(httpx, "get", get)
    result = dashboard.weather_data("Melbourne")
    assert result["code"] == 61 and result["desc"] == "Rain"
    assert result["observed_at"] == dt.datetime(2026, 11, 14, 11, 45, tzinfo=dt.timezone(dt.timedelta(hours=11))).timestamp()


def test_partial_query_is_opt_in_for_existing_clients(monkeypatch):
    import asyncio
    from ghost_server.app import app, device_auth
    monkeypatch.setattr(dashboard, "build", lambda partial=False: {"partial": partial})
    monkeypatch.setattr(dashboard.store, "get", lambda key, default=None: default)
    monkeypatch.setitem(app.dependency_overrides, device_auth, lambda: {"id": "test-device"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/dashboard")).json() == {"partial": False}
            assert (await client.get("/api/v1/dashboard?partial=true")).json() == {"partial": True}
    asyncio.run(run())
