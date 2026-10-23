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


def _no_reddit(*a, **k):
    raise AssertionError("Reddit must not be called")


def test_startups_reads_the_hn_front_page_after_the_techs_top_three(monkeypatch):
    kinds = []
    monkeypatch.setattr(feeds, "reddit_hot", _no_reddit)
    monkeypatch.setattr(feeds, "product_hunt_today", lambda: "- Launch One is a product (5 pts) https://ph.example")
    monkeypatch.setattr(feeds, "hacker_news", lambda limit=10, kind="top": kinds.append(kind) or
                        "\n".join(f"- Front page story number {n} here ({50 - n} pts) https://hn{n}.example" for n in range(1, 9)))
    titles = [i["title"] for i in world._fetch_startups()]
    assert kinds == ["top"]
    assert "Front page story number 4 here" in titles and "Front page story number 5 here" in titles
    assert not any(f"story number {n} " in t for t in titles for n in (1, 2, 3))   # the tech area has those


def test_ai_area_never_calls_reddit(monkeypatch):
    monkeypatch.setattr(feeds, "reddit_hot", _no_reddit)
    monkeypatch.setattr(feeds, "arxiv_papers", lambda category="ai", limit=5: "- A paper about agents and tools\n\n- Another paper on memory")
    items = world._fetch_ai()
    assert [i["title"] for i in items] == ["A paper about agents and tools", "Another paper on memory"]


def test_read_feed_tool_no_longer_offers_or_serves_reddit(monkeypatch):
    import tools
    monkeypatch.setattr(feeds, "reddit_hot", _no_reddit)
    spec = next(t for t in tools.TOOLS if t["function"]["name"] == "read_feed")["function"]
    assert "reddit" not in spec["parameters"]["properties"]["source"]["enum"]
    assert "reddit" not in spec["description"].lower()
    assert "Reddit isn't used anymore" in tools.dispatch("read_feed", {"source": "reddit"}, None)


_ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>1</id><link rel="alternate" type="text/html" href="https://www.producthunt.com/products/polylane"/>
    <title>Polylane for Cloudflare</title>
    <content type="html">&lt;p&gt; An AI on-call engineer for your Cloudflare apps &lt;/p&gt; &lt;p&gt;&lt;a href="x"&gt;Discussion&lt;/a&gt; | &lt;a href="y"&gt;Link&lt;/a&gt;&lt;/p&gt;</content></entry>
  <entry><id>2</id><link rel="alternate" type="text/html" href="https://www.producthunt.com/products/other"/>
    <title>Another Launch</title><content type="html">&lt;p&gt;Does a second thing&lt;/p&gt;</content></entry>
</feed>"""


class _TextResp:
    def __init__(self, text):
        self.text = text


def test_product_hunt_reads_the_atom_feed(monkeypatch):
    class S:
        def get(self, url, timeout=0):
            return _TextResp(_ATOM_FEED)
    monkeypatch.setattr(feeds, "_S", S())
    out = feeds.product_hunt_today()
    assert out.splitlines()[0] == "- Polylane for Cloudflare: An AI on-call engineer for your Cloudflare apps  https://www.producthunt.com/products/polylane"
    assert len(out.splitlines()) == 2 and "No products found" not in out


def test_empty_feed_message_is_not_turned_into_a_story():
    assert world._parse_bullets("No products found.") == []
    assert world._parse_bullets("No stories found.") == []
    assert [i["title"] for i in world._parse_bullets("- A real story title here (5 pts) https://x.example")] == ["A real story title here"]


def test_startups_has_no_placeholder_item_when_product_hunt_is_empty(monkeypatch):
    monkeypatch.setattr(feeds, "reddit_hot", _no_reddit)
    monkeypatch.setattr(feeds, "product_hunt_today", lambda: "No products found.")
    monkeypatch.setattr(feeds, "hacker_news", lambda limit=10, kind="top": "\n".join(
        f"- Front page story number {n} here ({50 - n} pts) https://hn{n}.example" for n in range(1, 9)))
    assert not any("No products" in i["title"] for i in world._fetch_startups())
