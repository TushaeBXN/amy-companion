"""Amy and Engram 1.6 corrections: Amy sees which memory is current and can correct one.

Run:  pytest tests/test_engram_bridge_corrections.py -v
Uses the real Engram (1.6+) with a throwaway château and the sqlite-vec
backend, so nothing touches ~/.engram and no model is downloaded.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

lineage = pytest.importorskip("engram.lineage")  # Engram 1.6+

import engram_bridge as eb  # noqa: E402

SQLITE = "Brian's app stores everything in SQLite because it is simple and local."
POSTGRES = "Brian's app moved from SQLite to PostgreSQL because it needs concurrent writers."


@pytest.fixture
def amy(tmp_path, monkeypatch):
    """engram_bridge wired to a temporary château."""
    from engram.backends import get_backend
    from engram.chateau import Chateau
    from engram.searcher import Searcher

    palace = Chateau(chateau_path=tmp_path / "chateau")
    backend = get_backend("sqlitevec", db_path=tmp_path / "vec.db")
    monkeypatch.setattr(eb, "_init", lambda: None)
    monkeypatch.setattr(eb, "_PALACE", palace)
    monkeypatch.setattr(eb, "_SEARCHER", Searcher(backend, palace, {"hybrid_weight": 0.3}))
    return eb


def test_add_returns_id_and_hints_at_memories_it_may_replace(amy):
    first = json.loads(amy.add_memory(SQLITE, room="projects"))
    assert first["stored"] and first["id"]
    second = json.loads(amy.add_memory(POSTGRES, room="projects"))
    assert [h["id"] for h in second["possible_updates"]] == [first["id"]]
    assert "correct_memory" in second["note"]


def test_after_a_correction_amy_sees_the_current_fact_first_and_the_old_one_labelled(amy):
    old = json.loads(amy.add_memory(SQLITE, room="projects"))["id"]
    fix = json.loads(amy.correct_memory(old[:8], POSTGRES, reason="needed concurrent writers"))
    assert fix["corrected"] and not fix["already_done"] and fix["old_id"] == old
    new = fix["new_id"]

    hits = json.loads(amy.search("what database does Brian's app use SQLite"))
    assert [h["id"] for h in hits[:2]] == [new, old]
    current, outdated = hits[0], hits[1]
    assert current["status"] == "CURRENT" and current["replaces"] == [old]
    assert "OUTDATED" not in current["text"]
    assert outdated["status"] == "OUTDATED" and outdated["replaced_by"] == new
    assert outdated["why"] == "needed concurrent writers"
    assert outdated["text"].startswith(f"[OUTDATED, replaced by {new[:8]}; do not use as current fact]")


def test_correction_chains_always_point_at_the_newest_fact(amy):
    a = json.loads(amy.add_memory("Brian's app database is SQLite.", room="projects"))["id"]
    b = json.loads(amy.correct_memory(a, "Brian's app database is now PostgreSQL."))["new_id"]
    c = json.loads(amy.correct_memory(b, "Brian's app database is now CockroachDB."))["new_id"]
    for query in ("app database SQLite", "app database PostgreSQL", "Brian's app database"):
        hits = json.loads(amy.search(query))
        assert hits[0]["id"] == c and hits[0].get("status") == "CURRENT"
        for h in hits[1:]:
            if h["id"] in (a, b):
                assert h["status"] == "OUTDATED" and h["replaced_by"] == c
    history = json.loads(amy.memory_history(a))
    assert history["status"] == "superseded" and history["current_id"] == c
    assert [s["now"] for s in history["history"]] == ["Brian's app database is now PostgreSQL.",
                                                      "Brian's app database is now CockroachDB."]


def test_retrying_a_correction_changes_nothing(amy):
    old = json.loads(amy.add_memory(SQLITE))["id"]
    first = json.loads(amy.correct_memory(old, POSTGRES))
    again = json.loads(amy.correct_memory(old, POSTGRES))
    assert again["corrected"] and again["already_done"] and again["new_id"] == first["new_id"]
    assert sum(1 for _ in amy._PALACE.iter_drawers()) == 2


def test_bad_corrections_report_an_error_instead_of_raising(amy):
    out = json.loads(amy.correct_memory("no-such-memory", "x"))
    assert out == {"corrected": False, "error": "no memory with ID 'no-such-memory'"}
    old = json.loads(amy.add_memory(SQLITE))["id"]
    amy.correct_memory(old, POSTGRES)
    out = json.loads(amy.correct_memory(old, "Something else entirely."))
    assert not out["corrected"] and "already replaced" in out["error"]


def test_secrets_are_still_redacted_in_corrections(amy):
    old = json.loads(amy.add_memory("The deploy key lives in the vault."))["id"]
    out = json.loads(amy.correct_memory(old, "Deploy key is ghp_" + "a" * 36))
    assert "ghp_" not in out["new_text"] and "[REDACTED]" in out["new_text"]


def test_search_without_corrections_looks_as_before(amy):
    amy.add_memory("Brian likes green tea in the morning.", room="prefs", hall="preferences")
    hit = json.loads(amy.search("what does Brian drink"))[0]
    assert set(hit) == {"id", "text", "score", "wing", "room", "hall"}
    assert hit["text"] == "Brian likes green tea in the morning."


def test_older_engram_gives_a_clear_message(amy, monkeypatch):
    monkeypatch.setitem(sys.modules, "engram.lineage", None)  # import fails, as on Engram < 1.6
    with pytest.raises(RuntimeError, match="Engram 1.6"):
        amy.correct_memory("abcd1234", "x")
    out = json.loads(amy.add_memory("Still stores fine."))
    assert out["stored"] and "possible_updates" not in out


def test_amy_tool_exposes_correct_and_history(monkeypatch):
    pytest.importorskip("ddgs")  # tools.py needs Amy's full dependencies
    import tools

    schema = next(t for t in tools.TOOLS if t["function"]["name"] == "engram_memory")
    actions = schema["function"]["parameters"]["properties"]["action"]["enum"]
    assert {"correct", "history"} <= set(actions)
    calls = []
    monkeypatch.setattr(eb, "correct_memory", lambda *a, **k: calls.append(("correct", a, k)) or "ok")
    monkeypatch.setattr(eb, "memory_history", lambda *a: calls.append(("history", a)) or "ok")
    tools.dispatch("engram_memory", {"action": "correct", "memory_id": "abc", "content": "new", "reason": "r"})
    tools.dispatch("engram_memory", {"action": "history", "memory_id": "abc"})
    assert calls == [("correct", ("abc", "new"), {"reason": "r"}), ("history", ("abc",))]
