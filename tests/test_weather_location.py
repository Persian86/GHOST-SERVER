import httpx
import pytest

from ghost_server.weather_location import resolve_weather_location


SUNBURY = {"name": "Sunbury", "admin1": "Victoria", "country": "Australia", "country_code": "AU",
           "latitude": -37.57742, "longitude": 144.72607, "timezone": "Australia/Melbourne"}


def mock_locations(monkeypatch, results):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request("GET", url), json={"results": results})
    monkeypatch.setattr(httpx, "get", get)
    return calls


@pytest.mark.parametrize("city", ["Sunbury, Victoria, Australia", "Sunbury, VIC, AU",
                                     " Sunbury , victoria , australia ", "Sunbury, VIC", "Sunbury, Victoria"])
def test_country_and_state_are_honoured_even_when_wrong_place_is_first(monkeypatch, city):
    usa = dict(SUNBURY, admin1="Pennsylvania", country="United States", country_code="US")
    nsw = dict(SUNBURY, admin1="New South Wales")
    calls = mock_locations(monkeypatch, [usa, nsw, SUNBURY])
    assert resolve_weather_location(city) == SUNBURY
    assert calls[0][1]["params"]["countryCode"] == "AU"
    assert calls[0][1]["params"]["name"].casefold() == "sunbury, victoria"


@pytest.mark.parametrize("city", ["Sunbury, Australia", "Sunbury, AU"])
def test_country_only_qualifier(monkeypatch, city):
    mock_locations(monkeypatch, [dict(SUNBURY, country="United Kingdom", country_code="GB"), SUNBURY])
    assert resolve_weather_location(city) == SUNBURY


@pytest.mark.parametrize("bad", [dict(SUNBURY, country_code="US"), dict(SUNBURY, admin1="New South Wales"),
                                  dict(SUNBURY, name="Sunbury Heights"), dict(SUNBURY, latitude="nan")])
def test_no_match_does_not_fall_back_to_another_location(monkeypatch, bad):
    mock_locations(monkeypatch, [bad])
    assert resolve_weather_location("Sunbury, Victoria, Australia") is None


def test_plain_city_compatibility(monkeypatch):
    # Preserve the provider's traditional first result for an unqualified query.
    first = dict(SUNBURY, name="Melbourne")
    mock_locations(monkeypatch, [first])
    assert resolve_weather_location("Melbourne") == first


@pytest.mark.parametrize("city", ["", "Sunbury,,Australia", "Sunbury, Victoria, Australia, nowhere"])
def test_invalid_qualified_input_does_not_issue_a_broader_search(monkeypatch, city):
    calls = mock_locations(monkeypatch, [SUNBURY])
    assert resolve_weather_location(city) is None
    assert calls == []


def test_dashboard_and_spoken_weather_use_the_same_verified_coordinates(monkeypatch):
    from ghost_server import dashboard
    from ghost_server.tools.web import weather_text
    calls = []
    def get(url, **kwargs):
        if "geocoding" in url:
            data = {"results": [dict(SUNBURY, country_code="US"), SUNBURY]}
        else:
            params = kwargs["params"]
            calls.append((params["latitude"], params["longitude"]))
            data = {"current": {"temperature_2m": 15, "weather_code": 3}, "daily": {
                "time": ["2026-11-14"], "weather_code": [3], "temperature_2m_min": [10],
                "temperature_2m_max": [18], "precipitation_probability_max": [50]}}
        return httpx.Response(200, request=httpx.Request("GET", url), json=data)
    monkeypatch.setattr(httpx, "get", get)
    city = "Sunbury, Victoria, Australia"
    data = dashboard.weather_data(city)
    assert data["city"] == "Sunbury" and data["region"] == "Victoria" and data["country"] == "Australia"
    assert "Sunbury, Victoria, Australia" in weather_text(city)
    assert calls == [(SUNBURY["latitude"], SUNBURY["longitude"])] * 2


def test_wrong_state_never_reaches_forecast(monkeypatch):
    from ghost_server import dashboard
    calls = mock_locations(monkeypatch, [dict(SUNBURY, admin1="Queensland")])
    assert dashboard.weather_data("Sunbury, Victoria, Australia") is None
    assert len(calls) == 1
