"""Offline checks: startups reads Show HN, never Reddit; feeds.hacker_news picks the right list."""
import pytest

import feeds
import world


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class _FakeSession:
    def __init__(self):
        self.urls = []

    def get(self, url, timeout=0):
        self.urls.append(url)
        if url.endswith("stories.json"):
            return _Resp([1, 2])
        return _Resp({"type": "story", "title": f"Show HN: thing {url[-6:]}", "score": 9, "url": "https://x.example"})


def test_hacker_news_default_is_the_front_page(monkeypatch):
    s = _FakeSession()
    monkeypatch.setattr(feeds, "_S", s)
    feeds.hacker_news(limit=2)
    assert s.urls[0].endswith("/topstories.json")


def test_hacker_news_show_list(monkeypatch):
    s = _FakeSession()
    monkeypatch.setattr(feeds, "_S", s)
    feeds.hacker_news(limit=2, kind="show")
    assert s.urls[0].endswith("/showstories.json")


def test_unknown_list_is_rejected():
    with pytest.raises(ValueError):
        feeds.hacker_news(kind="nope")


def test_startups_uses_show_hn_and_never_touches_reddit(monkeypatch):
    def no_reddit(*a, **k):
        raise AssertionError("startups must not call Reddit")
    kinds = []
    monkeypatch.setattr(feeds, "reddit_hot", no_reddit)
    monkeypatch.setattr(feeds, "product_hunt_today", lambda: "- Launch One is a product (5 pts) https://ph.example")
    monkeypatch.setattr(feeds, "hacker_news", lambda limit=10, kind="top": kinds.append(kind) or
                        "- Show HN: a tool for founders (50 pts) https://hn.example\n- Show HN: another nice project (30 pts) https://hn2.example")
    items = world._fetch_startups()
    assert kinds == ["show"]
    assert any("Show HN" in i["title"] for i in items)
