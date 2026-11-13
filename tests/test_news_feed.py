import json

import httpx
import pytest

from ghost_server import news_feed, store, dashboard


@pytest.fixture
def settings(monkeypatch):
    values = {"gnews_api_key": "private-key", "news_country": "Australia"}
    monkeypatch.setattr(store, "get", lambda k, default=None: values.get(k, default))
    return values


def test_gnews_key_only_used_server_side(settings, monkeypatch):
    def get(url, **kwargs):
        assert url == "https://gnews.io/api/v4/top-headlines"
        assert kwargs["params"]["apikey"] == "private-key"
        assert kwargs["params"]["country"] == "au"
        return httpx.Response(200, json={"articles": [
            {"title": "A headline", "url": "https://publisher.test/story", "description": "Highlights",
             "source": {"name": "Publisher"}, "image": "https://images.publisher.test/picture.jpg", "publishedAt": "2026-11-13T00:00:00Z"},
            {"title": "duplicate", "url": "https://publisher.test/story"},
            {"title": "bad", "url": "javascript:alert(1)"}]})
    monkeypatch.setattr(httpx, "get", get)
    rows = news_feed.top_headlines()
    assert len(rows) == 1 and rows[0]["source"] == "Publisher"
    assert rows[0]["image"] == "https://images.publisher.test/picture.jpg"
    assert rows[0]["summary"] == "Highlights" and rows[0]["provider"] == "GNews"
    assert "private-key" not in json.dumps(rows)
    assert dashboard.headlines() == rows
    assert "gnews_api_key" in store.SECRET_KEYS and store.DEFAULTS["gnews_api_key"] == ""


@pytest.mark.parametrize("failure", [401, 429, 500, "timeout", "invalid"])
def test_gnews_failure_falls_back_without_leaking_key(settings, monkeypatch, failure):
    def get(*a, **k):
        if failure == "timeout":
            raise httpx.ReadTimeout("private-key")
        if failure == "invalid":
            return httpx.Response(200, content=b"not JSON")
        return httpx.Response(failure)
    monkeypatch.setattr(httpx, "get", get)
    from ghost_server.tools import web
    monkeypatch.setattr(web, "ddg_news", lambda *a: [{"title": "Fallback", "source": "Publisher", "url": "https://publisher.test"}])
    rows = dashboard.headlines()
    assert rows[0]["title"] == "Fallback" and "private-key" not in json.dumps(rows)


def test_no_key_makes_no_external_gnews_request(settings, monkeypatch):
    settings["gnews_api_key"] = ""
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("No request without key"))
    assert news_feed.top_headlines() == []


def test_gnews_setting_is_masked(monkeypatch):
    monkeypatch.setattr(store, "q", lambda *a: [{"k": "gnews_api_key", "v": '"super-secret-gnews-key"'}])
    settings = store.all_settings()
    assert settings["gnews_api_key"] == "•••-key"
    assert "super-secret" not in json.dumps(settings)


@pytest.mark.parametrize("url", ["http://publisher.test/x", "javascript:alert(1)", "https://user:password@publisher.test/x"])
def test_thumbnail_metadata_accepts_only_https_without_credentials(url):
    assert news_feed.public_url(url) == ""
