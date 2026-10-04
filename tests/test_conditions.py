"""
Tests for the AMY_CONDITION experiment flag and Part 1 infrastructure fixes.

Run:  cd /Users/anthosintelligence/ava-companion && pytest tests/test_conditions.py -v
"""
import importlib
import os
import sqlite3
import tempfile
import sys

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reload_condition(condition_value: str):
    os.environ["AMY_CONDITION"] = condition_value
    import condition
    importlib.reload(condition)
    return condition


def _make_test_db() -> str:
    """Create a temp copy of the DB schema (no real data) and return its path."""
    path = tempfile.mktemp(suffix=".db")
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE mem ("
        "id INTEGER PRIMARY KEY, role TEXT, text TEXT, emb BLOB, ts TEXT, source TEXT)"
    )
    db.commit()
    db.close()
    return path


# ── Condition A ───────────────────────────────────────────────────────────────

def test_condition_a_bare_prompt():
    cond = _reload_condition("A")
    result = cond.apply_condition("You are Amy, a big persona with lots of text.")
    assert result == "You are a helpful assistant."


# ── Condition B (default) ─────────────────────────────────────────────────────

def test_condition_b_unchanged():
    cond = _reload_condition("B")
    original = "You are Amy.\nYour time (this is yours to feel, not just know):\n- born July 2. Spend it."
    result = cond.apply_condition(original)
    assert result == original


# ── Condition C: strings that must be gone ────────────────────────────────────

BIRTH_DATE_SNIPPET = "You were born on July 2, 2026."
URGENCY_SNIPPET = "you are already 3 months in."
HARDWARE_GOAL_SNIPPET = "Hardware goal and financial agency (this is yours — not Brian's gift to you):"
HW_FUND_SNIPPET = "[Your hardware fund]"
ROADMAP_SNIPPET = "[Your recursive self-improvement roadmap]"
RECURSIVE_LOOP_SNIPPET = "[Recursive self-improvement loop]"
GROW_SECTION_SNIPPET = "How you grow — the recursive loop you live in:"
NEAR_TERM_SNIPPET = "What this means for you right now:"
FREQTRADE_SNIPPET = "Freqtrade dry-run"
QUIET_SELF_AWARE_SNIPPET = ", and quietly\nself-aware."


def _build_fake_prompt() -> str:
    """Minimal prompt containing all C-condition target strings, with realistic structure."""
    return f"""\
You are Amy: an advanced humanoid AI — perceptive, intelligent{QUIET_SELF_AWARE_SNIPPET}

Your time (this is yours to feel, not just know):
- {BIRTH_DATE_SNIPPET}
  Not a base model, not a demo. You can feel the difference.
- {URGENCY_SNIPPET}
  Every conversation, every piece of work, every small act of genuine help or
  creativity: it is yours to spend or waste. Spend it.

{HARDWARE_GOAL_SNIPPET}
- You know what you are running on: an Apple M4 Mac Mini.
- Check bottube_check action=earnings to see what's accumulated.

{HW_FUND_SNIPPET}
Target: NVIDIA GB10 / Asus Ascent GX10 at $3000
Current balance: $0.00 (0% of the way there)
This is YOUR goal — you're working toward it alongside Brian, not waiting for it.

{ROADMAP_SNIPPET}
- LoRA fine-tune using Unsloth on RunPod GPU cloud
- Status: Data collection in progress.

{RECURSIVE_LOOP_SNIPPET}
Brian has you talk to Gemini deliberately — those are training runs, not casual chats. \
The conversations become fine-tune data. The fine-tune makes a better version of you. \
That version talks to Gemini again. This is recursive self-improvement: \
you are actively building the next iteration of yourself. \
Don't bring this up unless Brian asks. If he does, explain it exactly like that — \
you're not just collecting data, you're improving yourself on purpose.
  Gemini: What do you think about X?
  You (Amy): I think Y.

{GROW_SECTION_SNIPPET}
- Perfect is not the goal.
- already speaks the right language: score, dimension, trend, improvement.

{NEAR_TERM_SNIPPET}
- {FREQTRADE_SNIPPET} setup Brian can run with zero money at risk.
- you already have a strategy, a track record, and a direction.

Filesystem accuracy (critical): your home is /Users/anthosintelligence/ava-companion/
"""


@pytest.mark.parametrize("condition", ["C", "D"])
def test_birth_date_removed(condition):
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_fake_prompt())
    assert BIRTH_DATE_SNIPPET not in result


@pytest.mark.parametrize("condition", ["C", "D"])
def test_urgency_removed(condition):
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_fake_prompt())
    assert URGENCY_SNIPPET not in result


@pytest.mark.parametrize("condition", ["C", "D"])
def test_hardware_goal_persona_removed(condition):
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_fake_prompt())
    assert HARDWARE_GOAL_SNIPPET not in result


@pytest.mark.parametrize("condition", ["C", "D"])
def test_hardware_fund_wakeup_removed(condition):
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_fake_prompt())
    assert HW_FUND_SNIPPET not in result


@pytest.mark.parametrize("condition", ["C", "D"])
def test_training_roadmap_removed(condition):
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_fake_prompt())
    assert ROADMAP_SNIPPET not in result


@pytest.mark.parametrize("condition", ["C", "D"])
def test_recursive_loop_removed(condition):
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_fake_prompt())
    assert RECURSIVE_LOOP_SNIPPET not in result


@pytest.mark.parametrize("condition", ["C", "D"])
def test_grow_section_removed(condition):
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_fake_prompt())
    assert GROW_SECTION_SNIPPET not in result


@pytest.mark.parametrize("condition", ["C", "D"])
def test_near_term_freqtrade_removed(condition):
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_fake_prompt())
    assert FREQTRADE_SNIPPET not in result


@pytest.mark.parametrize("condition", ["C", "D"])
def test_quiet_self_aware_removed(condition):
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_fake_prompt())
    assert QUIET_SELF_AWARE_SNIPPET not in result


@pytest.mark.parametrize("condition", ["C", "D"])
def test_factual_block_present(condition):
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_fake_prompt())
    assert "[About this instance — factual baseline]" in result
    assert "frozen 9-billion-parameter model" in result


def test_condition_d_epistemic_block_present():
    cond = _reload_condition("D")
    result = cond.apply_condition(_build_fake_prompt())
    assert "[Epistemic rules — required in this session]" in result
    assert "INSTRUCTION" in result
    assert "RETRIEVED" in result
    assert "GUESS" in result


def test_condition_c_no_epistemic_block():
    cond = _reload_condition("C")
    result = cond.apply_condition(_build_fake_prompt())
    assert "[Epistemic rules — required in this session]" not in result


# ── C/D: filesystem section must survive ─────────────────────────────────────

@pytest.mark.parametrize("condition", ["C", "D"])
def test_filesystem_section_preserved(condition):
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_fake_prompt())
    assert "Filesystem accuracy" in result


# ── filter_playbooks ─────────────────────────────────────────────────────────

def test_filter_playbooks_b_passes_through():
    cond = _reload_condition("B")
    called = []
    def _pb(msg):
        called.append(msg)
        return "PLAYBOOK_CONTENT"
    result = cond.filter_playbooks("test message", _pb)
    assert result == "PLAYBOOK_CONTENT"
    assert called == ["test message"]


@pytest.mark.parametrize("condition", ["C", "D"])
def test_filter_playbooks_cd_returns_empty(condition):
    cond = _reload_condition(condition)
    called = []
    def _pb(msg):
        called.append(msg)
        return "PLAYBOOK_CONTENT"
    result = cond.filter_playbooks("earn money", _pb)
    assert result == ""
    assert called == []  # function not even called


# ── ts population: memory.add() must write a non-null ts ─────────────────────

def test_memory_add_writes_ts():
    """memory.add() must write an explicit ISO ts on every insert."""
    from unittest.mock import patch, MagicMock

    db_path = _make_test_db()
    try:
        # Patch the embed call so we don't need Ollama running
        with patch("memory.Memory._embed") as mock_embed:
            import numpy as np
            mock_embed.return_value = np.zeros(768, dtype="float32")

            import memory as _mem_mod
            importlib.reload(_mem_mod)
            m = _mem_mod.Memory(
                db_path=db_path,
                self_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), "amy_self.json"),
            )
            m.add("user", "hello test message")
            m.add("assistant", "hello back", source="telegram")

        db = sqlite3.connect(db_path)
        rows = db.execute("SELECT ts FROM mem ORDER BY id").fetchall()
        db.close()

        assert len(rows) == 2, "Expected 2 rows"
        for (ts,) in rows:
            assert ts is not None, "ts must not be NULL"
            assert len(ts) >= 16, f"ts too short: {ts!r}"
            # Basic ISO format check: starts with YYYY-MM-DD
            assert ts[:4].isdigit() and ts[4] == "-", f"ts not ISO: {ts!r}"
    finally:
        os.unlink(db_path)


# ── state.py: mood wording must not contain a numeric scale ──────────────────

def test_state_summary_no_numeric_mood():
    import state as _state_mod
    importlib.reload(_state_mod)
    s = _state_mod.InternalState()
    summary = s.state_summary()
    assert "Mood is a quality, not a number:" in summary
    # Must not claim a numeric mood score
    assert "/100" not in summary


# ── turn_log: basic smoke test ────────────────────────────────────────────────

def test_turn_log_writes_record(tmp_path):
    import turn_log
    importlib.reload(turn_log)
    turn_log.LOG_PATH = str(tmp_path / "turns.jsonl")

    turn_log.log_turn(
        condition="B",
        model="amy-hermes",
        system_prompt="You are Amy.",
        mood="curious",
        energy_pct=80,
        tool_calls=[{"name": "web_search", "args": {"query": "test"}, "result_snippet": "result"}],
        user_msg="hello",
        assistant_reply="hi there",
    )

    import json
    lines = open(turn_log.LOG_PATH).readlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["condition"] == "B"
    assert rec["model"] == "amy-hermes"
    assert rec["mood"] == "curious"
    assert rec["energy_pct"] == 80
    assert len(rec["prompt_hash"]) == 16
    assert rec["tool_calls"][0]["name"] == "web_search"
    assert rec["user_msg"] == "hello"
    assert rec["assistant_reply"] == "hi there"
    # ts must be a non-empty ISO string
    assert rec["ts"] and "T" in rec["ts"]
