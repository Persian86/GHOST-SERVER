"""Resolve qualified weather locations without dropping country/state information."""
from __future__ import annotations

import math
import unicodedata

import httpx


_AU_STATES = {
    "vic": "Victoria", "nsw": "New South Wales", "qld": "Queensland",
    "sa": "South Australia", "wa": "Western Australia", "tas": "Tasmania",
    "nt": "Northern Territory", "act": "Australian Capital Territory",
}
_COUNTRIES = {"australia": "AU", "aus": "AU", "au": "AU",
              "united states": "US", "usa": "US", "us": "US",
              "united kingdom": "GB", "uk": "GB", "gb": "GB",
              "new zealand": "NZ", "nz": "NZ"}


def _normal(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", str(value))
                           if not unicodedata.combining(c)).casefold().split())


def resolve_weather_location(city: str) -> dict | None:
    parts = [p.strip() for p in city.split(",")]
    if not parts[0] or any(not p for p in parts) or len(parts) > 3:
        return None
    town = parts[0]
    region = country = country_code = None
    if len(parts) == 3:
        region, country = parts[1:]
    elif len(parts) == 2:
        qualifier = _normal(parts[1])
        if qualifier in _AU_STATES or qualifier in {_normal(v) for v in _AU_STATES.values()}:
            region, country = parts[1], "Australia"
        else:
            # The provider accepts country names and first-level region names.
            region = parts[1]
    if country:
        country_code = _COUNTRIES.get(_normal(country))
        if not country_code and len(country) == 2 and country.isalpha():
            country_code = country.upper()
    if region and country_code == "AU":
        region = _AU_STATES.get(_normal(region), region)
    qualifier = region or country
    params = {"name": town + (", " + qualifier if qualifier else ""), "count": 100, "language": "en"}
    if country_code:
        params["countryCode"] = country_code
    elif country:
        # Country filtering occurs before the result limit; validate the region below.
        params["name"] = town + ", " + country
    response = httpx.get("https://geocoding-api.open-meteo.com/v1/search", params=params, timeout=6)
    response.raise_for_status()
    for loc in response.json().get("results", []):
        # Do not silently accept a prefix match such as Sunbury-on-Thames for Sunbury.
        if len(parts) > 1 and _normal(loc.get("name", "")) != _normal(town) and not (
                town.isdigit() and town in loc.get("postcodes", [])):
            continue
        if country_code and str(loc.get("country_code", "")).upper() != country_code:
            continue
        if country and not country_code and _normal(loc.get("country", "")) != _normal(country):
            continue
        if region and _normal(region) not in {
                _normal(loc.get("admin1", "")), _normal(loc.get("country", "")),
                _normal(loc.get("country_code", "")),
                *{k for k, v in _COUNTRIES.items() if v == str(loc.get("country_code", "")).upper()}}:
            continue
        try:
            latitude, longitude = float(loc["latitude"]), float(loc["longitude"])
            if not (math.isfinite(latitude) and math.isfinite(longitude) and
                    -90 <= latitude <= 90 and -180 <= longitude <= 180):
                continue
        except (KeyError, ValueError, TypeError):
            continue
        return loc
    return None


def weather_location_label(loc: dict) -> str:
    return ", ".join(dict.fromkeys(str(loc.get(k) or "").strip()
                                 for k in ("name", "admin1", "country") if loc.get(k)))
