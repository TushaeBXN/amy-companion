"""Fixes from Amy's first live test: history that never saved, and memory she never looked at.

Run:  pytest tests/test_live_fixes.py -v
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import history_io  # noqa: E402


def test_history_with_tool_call_objects_can_be_saved(tmp_path):
    ollama = pytest.importorskip("ollama")
    call = ollama.Message.ToolCall(function=ollama.Message.ToolCall.Function(name="engram_memory", arguments={"action": "search"}))
    history = [{"role": "user", "content": "hi"},
               {"role": "assistant", "content": "", "tool_calls": [call]}]       # what the chat loop stores
    with pytest.raises(TypeError):
        json.dumps(history)                                                       # the old failure
    path = tmp_path / "h.json"
    path.write_text(json.dumps(history, default=history_io.json_default))
    saved = json.loads(path.read_text())
    assert saved[1]["tool_calls"][0]["function"]["name"] == "engram_memory"
    assert history_io.json_default(object()).startswith("<object")                # anything else becomes text


pytest.importorskip("engram.recall")  # Engram 1.7+

import engram_bridge as eb  # noqa: E402


@pytest.fixture
def amy(tmp_path, monkeypatch):
    from engram.backends import get_backend
    from engram.chateau import Chateau
    from engram.searcher import Searcher

    palace = Chateau(chateau_path=tmp_path / "chateau")
    backend = get_backend("sqlitevec", db_path=tmp_path / "vec.db")
    monkeypatch.setattr(eb, "_init", lambda: None)
    monkeypatch.setattr(eb, "_PALACE", palace)
    monkeypatch.setattr(eb, "_SEARCHER", Searcher(backend, palace, {"hybrid_weight": 0.3, "recall_min_relevance": 0.12,
                                                                    "keyword_search": False}))
    return eb


def test_a_message_about_coffee_brings_the_saved_coffee_preference(amy):
    old = json.loads(amy.add_memory("Brian's favorite coffee shop is Juniper & Oak. He orders a cortado with oat milk.",
                                    wing="brian", room="preferences", hall="preferences"))
    assert amy.memory_context("I'm meeting a friend for coffee tomorrow, where should we go?").count("Juniper") == 1
    amy.correct_memory(old["id"], "Brian's new favorite coffee shop is Copper Kettle. He orders a flat white with almond milk.",
                       reason="the old shop closed")
    # Word-count vectors (no embedding model here) score a longer question lower, so open the bar: this
    # part is about order and labels, not about how relevant is relevant enough.
    amy._SEARCHER.config["recall_min_relevance"] = 0.0
    note = amy.memory_context("I'm meeting a friend for coffee tomorrow, where should we go and what should I get?")
    lines = note.splitlines()
    first = next(i for i, ln in enumerate(lines) if "Copper Kettle" in ln)
    outdated = next(i for i, ln in enumerate(lines) if "Juniper" in ln)
    assert "CURRENT" in lines[first] and "OUTDATED" in lines[outdated] and first < outdated   # current first, old labelled


def test_nothing_is_added_when_no_memory_is_relevant_and_the_library_is_left_out(amy):
    amy.add_memory("Brian's favorite coffee shop is Copper Kettle.", wing="brian", room="preferences")
    amy.add_memory("Federalist No. 10 on the dangers of faction and coffee-house politics.", wing="library", room="federalist")
    assert amy.memory_context("explain how photosynthesis turns sunlight into sugar in plants") == ""
    assert "Federalist" not in amy.memory_context("coffee house politics and faction")


def test_memory_context_never_breaks_a_turn(monkeypatch):
    monkeypatch.setattr(eb, "_init", lambda: (_ for _ in ()).throw(RuntimeError("index unavailable")))
    assert eb.memory_context("coffee tomorrow with a friend, where should we go?") == ""


def test_a_failed_history_save_leaves_the_old_file_intact(tmp_path, monkeypatch):
    """Same write-then-swap as telegram_amy._save_history: a crash mid-write must not corrupt it."""
    path = tmp_path / "history.json"
    path.write_text(json.dumps([{"role": "user", "content": "kept"}]))
    tmp = f"{path}.tmp"
    with pytest.raises(TypeError):
        with open(tmp, "w") as f:
            json.dump([{"role": "assistant", "content": "x", "tool_calls": [object()]}], f)   # fails part-way
    assert json.loads(path.read_text()) == [{"role": "user", "content": "kept"}]               # untouched


def test_conversation_logs_in_the_default_wing_are_not_recalled_as_memories(amy):
    amy.add_memory("Brian's favorite bakery is Rosewater Bakery and he always gets the lemon scone.",
                   wing="brian", room="preference", hall="preferences")
    amy.add_memory("Working on the plan for tomorrow before work, where should we go next? Maybe the bakery.",
                   wing="default", room="chat", hall="discoveries")
    amy._SEARCHER.config["recall_min_relevance"] = 0.0
    note = amy.memory_context("Where should I go for a bakery treat tomorrow?")
    assert "Rosewater" in note and "Working on the plan" not in note
    assert amy.MEMORY_CONTEXT_SKIP_WINGS == ("library", "default")
