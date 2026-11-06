import datetime as dt

import pytest

from ghost_server import theme

_S: dict = {}


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch):
    _S.clear()
    monkeypatch.setattr(theme.store, "get", lambda k, d=None: _S.get(k, d))


def _set(**kv):
    _S.update(kv)


def test_easter_dates():
    assert theme.easter(2026) == dt.date(2026, 4, 5)
    assert theme.easter(2027) == dt.date(2027, 3, 28)
    assert theme.easter(2030) == dt.date(2030, 4, 21)


def test_holidays_and_seasons():
    _set(theme_mode="auto", theme_off="", theme_birthdays="", theme_seasons=True, theme_hemisphere="south")
    assert theme.current(dt.date(2026, 10, 31))["id"] == "halloween"
    assert theme.current(dt.date(2026, 12, 20))["id"] == "christmas"
    assert theme.current(dt.date(2026, 12, 31))["id"] == "new_year"
    assert theme.current(dt.date(2026, 4, 3))["id"] == "easter"          # Good Friday
    assert theme.current(dt.date(2026, 2, 14))["id"] == "valentines"
    assert theme.current(dt.date(2026, 5, 10))["id"] == "mothers_day"    # 2nd Sunday of May
    assert theme.current(dt.date(2026, 9, 6))["id"] == "fathers_day"     # 1st Sunday of September
    assert theme.current(dt.date(2026, 4, 25))["particles"] == "none"    # Anzac: respectful
    assert theme.current(dt.date(2026, 7, 10))["id"] == "winter"         # southern hemisphere
    _set(theme_hemisphere="north")
    assert theme.current(dt.date(2026, 7, 10))["id"] == "summer"
    _set(theme_seasons=False)
    assert theme.current(dt.date(2026, 7, 10))["id"] == ""


def test_birthday_off_and_preview():
    _set(theme_mode="auto", theme_off="halloween", theme_birthdays="Adam 14/03, Zoey 31/10", theme_seasons=False)
    t = theme.current(dt.date(2026, 3, 14))
    assert t["id"] == "birthday" and t["greeting"] == "Happy birthday, Adam"
    assert theme.current(dt.date(2026, 10, 31))["id"] == "birthday"      # birthday beats Halloween
    assert theme.current(dt.date(2026, 10, 30))["id"] == ""             # Halloween switched off
    _set(theme_mode="christmas")
    assert theme.current(dt.date(2026, 7, 1))["id"] == "christmas"
    _set(theme_mode="off")
    assert theme.current(dt.date(2026, 12, 25))["id"] == ""
