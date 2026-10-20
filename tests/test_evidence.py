"""Amy's views rest on evidence she can show (Engram 1.9+).

Run:  pytest tests/test_evidence.py -v
Uses the real Engram with a throwaway château and sqlite-vec; nothing is
downloaded and ~/.engram is not touched. The first test follows the
"14th Amendment source test": text, official explanation and Amy's own view
stay distinct, and a challenge is answered with the passage behind the view.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

pytest.importorskip("engram.relations")  # Engram 1.9+

import engram_bridge as eb  # noqa: E402
import library  # noqa: E402

TEXT = ("[Amendment XIV, Section 1 · U.S. Constitution]\nNo State shall make or enforce any law which shall "
        "abridge the privileges or immunities of citizens of the United States; nor shall any State deprive "
        "any person of life, liberty, or property, without due process of law; nor deny to any person within "
        "its jurisdiction the equal protection of the laws.")
CONAN = ("[Equal Protection: Intent Requirement · Constitution Annotated]\nThe Court has held that a law "
         "neutral on its face violates equal protection only if it was adopted with a discriminatory purpose; "
         "disproportionate impact alone is not enough (Washington v. Davis, 1976).")


@pytest.fixture
def amy(tmp_path, monkeypatch):
    from engram.backends import get_backend
    from engram.chateau import Chateau
    from engram.searcher import Searcher

    palace = Chateau(chateau_path=tmp_path / "chateau")
    backend = get_backend("sqlitevec", db_path=tmp_path / "vec.db")
    monkeypatch.setattr(eb, "_init", lambda: None)
    monkeypatch.setattr(eb, "_PALACE", palace)
    monkeypatch.setattr(eb, "_SEARCHER", Searcher(backend, palace, {"hybrid_weight": 0.3}))
    library.store([{"id": "lib-e28dfd982a25d6c5c3a7", "room": "constitution", "cite": "Amendment XIV, Section 1",
                    "content": TEXT}], "test", palace, backend)
    library.store([{"id": "lib-e28d0a1b2c3d4e5f6a7b", "room": "conan", "cite": "Equal Protection: Intent Requirement",
                    "content": CONAN}], "test", palace, backend, kind="secondary")
    return eb


def test_view_keeps_text_explanation_and_interpretation_apart(amy):
    rec = json.loads(amy.record_view(
        "equal protection and voting maps",
        "A map that dilutes minority votes should be unconstitutional even without proof of intent.",
        evidence=["lib-e28dfd98", "lib-e28d0a1b"]))   # the short IDs Amy is shown
    assert rec["recorded"] and rec["evidence"] == ["lib-e28dfd98", "lib-e28d0a1b"]

    why = json.loads(amy.explain_memory(rec["id"], query="does equal protection require intent?"))
    assert why["source"] == "inference" and why["kind"] == "inference"
    sources = {e["id"]: (e["source"], e.get("cite")) for e in why["rests_on"]}
    assert sources == {"lib-e28dfd98": ("primary", "Amendment XIV, Section 1"),
                       "lib-e28d0a1b": ("secondary", "Equal Protection: Intent Requirement")}
    assert "Washington v. Davis" in next(e["text"] for e in why["rests_on"] if e["id"] == "lib-e28d0a1b")
    assert why["needs_review"] == [] and why["relevance"]["rank"] >= 1

    view = json.loads(amy.get_views("equal protection and voting maps"))
    assert {e["id"] for e in view["rests_on"]} == {"lib-e28dfd98", "lib-e28d0a1b"}


def test_shown_library_ids_are_enough_to_find_the_passage_again(amy):
    # Library IDs are "lib-" + a hash. Cut to 8 characters, two passages can look the same ("lib-e28d").
    ambiguous = json.loads(amy.record_view("taxes", "Flat taxes are regressive.", evidence=["lib-e28d"]))
    assert not ambiguous["recorded"] and "matches 2 memories" in ambiguous["error"]
    shown = amy.library_context("equal protection of the laws, due process")
    assert "· id lib-e28dfd98]" in shown or "· id lib-e28d0a1b]" in shown
    ok = json.loads(amy.record_view("taxes", "Flat taxes are regressive.", evidence=["lib-e28dfd98"]))
    assert ok["recorded"] and ok["evidence"] == ["lib-e28dfd98"]


def test_corrected_evidence_is_flagged_and_revision_keeps_the_old_trail(amy):
    news = json.loads(amy.add_memory("Report: the state's new congressional map takes effect in 2026.",
                                     room="news", source="secondary", source_ref="example.com, 1 Oct"))
    rec = json.loads(amy.record_view("the new state map", "It will cost the state two competitive seats.",
                                     evidence=news["id"]))
    fix = json.loads(amy.correct_memory(news["id"], "Correction: a court blocked the new map; the old one stands.",
                                        reason="court order"))
    assert fix["corrected"]

    report = json.loads(amy.check_memory())
    assert not report["ok"] and report["evidence_changed"][0]["memory"]["id"] == rec["id"][:8]
    view = json.loads(amy.get_views("the new state map"))
    assert any("was corrected" in c for c in view["needs_review"])

    revised = json.loads(amy.record_view("the new state map", "The old map stands for now; no seats change yet.",
                                         reason="the map was blocked", evidence=[fix["new_id"]]))
    assert revised["changed"]
    view = json.loads(amy.get_views("the new state map"))
    assert view["rests_on"][0]["id"] == fix["new_id"][:8] and "needs_review" not in view
    assert view["earlier_views_rested_on"][rec["id"][:8]][0]["id"] == news["id"][:8]
    assert json.loads(amy.check_memory())["ok"]


def test_bad_evidence_is_refused_before_anything_is_saved(amy):
    out = json.loads(amy.record_view("taxes", "Flat taxes are regressive.", evidence=["no-such-id"]))
    assert not out["recorded"] and "no memory" in out["error"]
    assert json.loads(amy.get_views("taxes"))["view"] is None
    assert not json.loads(amy.add_memory("x", source="vibes"))["stored"]


def test_view_without_evidence_gets_a_nudge_and_links_can_be_added(amy):
    rec = json.loads(amy.record_view("judicial review", "Marbury settled it, but its reach is debated."))
    assert "No evidence linked" in rec["note"]
    assert json.loads(amy.check_memory())["no_evidence"][0]["id"] == rec["id"][:8]  # an inference, nothing behind it
    linked = json.loads(amy.link_memories(rec["id"], "based_on", "lib-e28dfd982a25d6c5c3a7", reason="test"))
    assert linked["linked"] and json.loads(amy.check_memory())["ok"]


def test_library_context_shows_ids_amy_can_cite(amy):
    ctx = amy.library_context("equal protection of the laws, due process")
    assert "· id lib-e28dfd98]" in ctx


def test_tool_actions_reach_the_bridge(monkeypatch):
    pytest.importorskip("ddgs")  # tools.py needs Amy's full dependencies
    import tools
    calls = []
    monkeypatch.setattr(eb, "explain_memory", lambda m, query="": calls.append(("why", m, query)) or "ok")
    monkeypatch.setattr(eb, "link_memories", lambda m, r, t, reason="": calls.append(("link", m, r, t)) or "ok")
    monkeypatch.setattr(eb, "check_memory", lambda: calls.append(("check",)) or "ok")
    monkeypatch.setattr(eb, "record_view", lambda t, c, reason="", evidence=None:
                        calls.append(("view", t, evidence)) or "ok")
    tools.dispatch("engram_memory", {"action": "why", "memory_id": "abcd1234", "query": "q"})
    tools.dispatch("engram_memory", {"action": "link", "memory_id": "a", "relation": "based_on", "target_id": "b"})
    tools.dispatch("engram_memory", {"action": "check"})
    tools.dispatch("engram_memory", {"action": "view_record", "topic": "t", "content": "v", "evidence": ["x"]})
    assert calls == [("why", "abcd1234", "q"), ("link", "a", "based_on", "b"), ("check",), ("view", "t", ["x"])]
