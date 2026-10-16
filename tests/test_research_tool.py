"""research_current: Amy looks up what is happening now instead of relying on training.

Run:  pytest tests/test_research_tool.py -v
The search engine is faked; nothing goes to the network.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

pytest.importorskip("ddgs")
pytest.importorskip("bs4")

import web  # noqa: E402

NEWS = [
    {"date": "2025-11-18T10:00:00+00:00", "title": "Court blocks Texas map", "body": "A federal panel ruled...",
     "url": "https://example.org/blocked", "source": "AP"},
    {"date": "2025-12-04T15:00:00+00:00", "title": "Supreme Court lets Texas use new map", "body": "6-3 order...",
     "url": "https://example.org/scotus", "source": "Reuters"},
]
TEXT = [
    {"title": "Texas redistricting explained", "href": "https://example.org/explainer", "body": "Background..."},
    {"title": "Duplicate of the AP story", "href": "https://example.org/blocked", "body": "same url"},
]


class FakeDDGS:
    news_error = None

    def news(self, query, timelimit=None, max_results=None):
        if FakeDDGS.news_error:
            raise FakeDDGS.news_error
        return NEWS[:max_results]

    def text(self, query, max_results=None):
        return TEXT[:max_results]


@pytest.fixture(autouse=True)
def fake_search(monkeypatch):
    FakeDDGS.news_error = None
    monkeypatch.setattr(web, "DDGS", FakeDDGS)


def test_research_puts_newest_news_first_then_web_without_duplicates():
    found = web.research("Texas redistricting")
    assert [r["url"] for r in found] == ["https://example.org/scotus", "https://example.org/blocked",
                                         "https://example.org/explainer"]
    assert found[0]["date"] == "2025-12-04" and found[0]["source"] == "Reuters"


def test_research_still_works_when_news_search_fails():
    FakeDDGS.news_error = RuntimeError("rate limited")
    found = web.research("Texas redistricting")
    assert [r["url"] for r in found] == ["https://example.org/explainer", "https://example.org/blocked"]


def test_research_current_tool_cites_dates_and_sources(monkeypatch):
    import tools

    monkeypatch.setattr(tools, "_net_cache", {})
    out = tools.dispatch("research_current", {"topic": "Texas redistricting map court ruling"})
    lines = out.splitlines()
    assert lines[0].startswith("Sources (newest news first). Cite at least two")
    assert lines[1] == ("- [2025-12-04 · Reuters] Supreme Court lets Texas use new map: 6-3 order... "
                        "(https://example.org/scotus)")
    assert lines[-1].startswith("- Texas redistricting explained")
    assert tools.dispatch("research_current", {"topic": ""}) == "no topic given"
    schema = next(t for t in tools.TOOLS if t["function"]["name"] == "research_current")
    assert "BEFORE stating anything current" in schema["function"]["description"]


def test_amy_is_told_to_search_before_answering_about_current_events():
    import amy_persona as ap

    for msg in ("what's the latest on the Texas election map?", "they're changing election maps in Texas",
                "any news on the Venezuela strikes?"):
        pb = ap.playbooks_for(msg)
        assert "call research_current first" in pb, msg
    assert "Proposition 50" in ap.playbooks_for("Texas redistricting election map")
    assert "research_current" not in ap.playbooks_for("good morning amy")
