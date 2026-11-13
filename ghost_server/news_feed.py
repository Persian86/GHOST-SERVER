"""GNews headlines. The credential stays in server settings, never in dashboard JSON."""
from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from . import store

# Register this integration with the existing masked settings form.
store.DEFAULTS.setdefault("gnews_api_key", "")
store.SECRET_KEYS.add("gnews_api_key")

COUNTRIES = {"australia": "au", "new zealand": "nz", "united kingdom": "gb", "uk": "gb",
             "united states": "us", "usa": "us", "canada": "ca", "india": "in", "ireland": "ie"}


def public_url(value) -> str:
    if not isinstance(value, str):
        return ""
    try:
        url = urlsplit(value)
        return value if url.scheme == "https" and url.hostname and not url.username and not url.password else ""
    except ValueError:
        return ""


def top_headlines() -> list[dict]:
    api_key = str(store.get("gnews_api_key") or "").strip()
    if not api_key:
        return []
    country = str(store.get("news_country") or "Australia").strip().lower()
    code = COUNTRIES.get(country, country if len(country) == 2 and country.isalpha() else "au")
    # Do not raise/log HTTP exceptions: their URLs contain the credential.
    try:
        response = httpx.get("https://gnews.io/api/v4/top-headlines", params={
            "apikey": api_key, "lang": "en", "country": code, "category": "general", "max": 6,
        }, timeout=8, follow_redirects=False, trust_env=False)
        if response.status_code != 200:
            return []
        articles = response.json().get("articles", [])
        if not isinstance(articles, list):
            return []
        rows, seen = [], set()
        for a in articles:
            if not isinstance(a, dict):
                continue
            title = str(a.get("title") or "").strip()[:300]
            url = public_url(a.get("url"))
            if not title or not url or url in seen:
                continue
            seen.add(url)
            source = a.get("source") or {}
            rows.append({"title": title, "url": url, "source": str(source.get("name") or "")[:100]
                         if isinstance(source, dict) else "", "published": str(a.get("publishedAt") or "")[:40],
                         "summary": str(a.get("description") or "")[:500], "provider": "GNews"})
        return rows[:6]
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return []
