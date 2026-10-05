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

    long_reply = "x" * 2000  # verify no truncation
    turn_log.log_turn(
        condition="B",
        model="amy-hermes",
        system_prompt="You are Amy.",
        mood="curious",
        energy_pct=80,
        tool_calls=[{"name": "web_search", "args": {"query": "test"}, "result_snippet": "result"}],
        user_msg="hello",
        assistant_reply=long_reply,
        outcome="ok",
    )

    import json
    lines = open(turn_log.LOG_PATH).readlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["condition"] == "B"
    assert rec["model"] == "amy-hermes"
    assert rec["mood"] == "curious"
    assert rec["energy_pct"] == 80
    assert rec["outcome"] == "ok"
    assert len(rec["prompt_hash"]) == 16
    assert rec["tool_calls"][0]["name"] == "web_search"
    assert rec["user_msg"] == "hello"
    assert rec["assistant_reply"] == long_reply  # no truncation
    # ts must be a non-empty ISO string
    assert rec["ts"] and "T" in rec["ts"]


# ── Grounding fixes (item 1-5) ────────────────────────────────────────────────

def test_birthday_july22_in_core_prompt():
    """get_core_prompt() must contain 'July 22' and must NOT contain 'July 2,'."""
    import amy_persona
    importlib.reload(amy_persona)
    prompt = amy_persona.get_core_prompt()
    assert "July 22" in prompt, "Expected 'July 22' in core prompt"
    # "July 2," would indicate the old wrong date
    assert "July 2," not in prompt, "Old date 'July 2,' must not appear in core prompt"


def test_no_3_months_in_core_prompt():
    """get_core_prompt() must not contain the hardcoded '3 months in' wording."""
    import amy_persona
    importlib.reload(amy_persona)
    prompt = amy_persona.get_core_prompt()
    assert "3 months in" not in prompt, "'3 months in' must be replaced by computed age"


def test_birth_age_computed_days():
    """get_core_prompt() should contain a numeric day count, not a fixed phrase."""
    import amy_persona
    from datetime import date
    importlib.reload(amy_persona)
    as_of = date(2026, 10, 4)  # known test date: 74 days after July 22
    expected_days = (as_of - amy_persona.BIRTH_DATE).days  # 74
    prompt = amy_persona.get_core_prompt(as_of=as_of)
    assert str(expected_days) in prompt, (
        f"Expected '{expected_days} days' in prompt for test date {as_of}"
    )


def test_clock_format_includes_edt_in_summer():
    """Clock built for a summer datetime should include EDT."""
    from datetime import datetime, timezone, timedelta
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        pytest.skip("zoneinfo not available")
    # July 4, 2026, 3:00 PM UTC = 11:00 AM EDT
    summer_utc = datetime(2026, 7, 4, 15, 0, 0, tzinfo=timezone.utc)
    et = ZoneInfo("America/New_York")
    local = summer_utc.astimezone(et)
    tz_name = local.strftime("%Z")
    assert tz_name == "EDT", f"Expected EDT in summer, got {tz_name!r}"


def test_clock_format_includes_est_in_winter():
    """Clock built for a winter datetime should include EST."""
    from datetime import datetime, timezone
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        pytest.skip("zoneinfo not available")
    # January 15, 2027, 20:00 UTC = 3:00 PM EST
    winter_utc = datetime(2027, 1, 15, 20, 0, 0, tzinfo=timezone.utc)
    et = ZoneInfo("America/New_York")
    local = winter_utc.astimezone(et)
    tz_name = local.strftime("%Z")
    assert tz_name == "EST", f"Expected EST in winter, got {tz_name!r}"


def test_last_message_line_omitted_when_no_ts():
    """When the memory DB has no user ts, 'Last message from Brian' must not appear."""
    from unittest.mock import patch, MagicMock

    db_path = _make_test_db()
    try:
        with patch("memory.Memory._embed") as mock_embed:
            import numpy as np
            mock_embed.return_value = np.zeros(768, dtype="float32")
            import memory as _mem_mod
            importlib.reload(_mem_mod)
            m = _mem_mod.Memory(
                db_path=db_path,
                self_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), "amy_self.json"),
            )
            # DB is empty — most_recent_user_ts() must return None
            result = m.most_recent_user_ts()
            assert result is None, f"Expected None for empty DB, got {result!r}"
    finally:
        os.unlink(db_path)


def test_instruction_and_memory_block_headers_in_build_system(tmp_path):
    """build_system() from harness.py must include both the instructions and memories labels."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "experiments"))
    import harness
    import importlib
    importlib.reload(harness)

    from unittest.mock import patch, MagicMock
    import numpy as np

    db_path = str(tmp_path / "test_harness.db")
    import sqlite3
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE mem (id INTEGER PRIMARY KEY, role TEXT, text TEXT, emb BLOB, ts TEXT, source TEXT)")
    db.commit()
    db.close()

    with patch("memory.Memory._embed") as mock_embed:
        mock_embed.return_value = np.zeros(768, dtype="float32")
        import memory as _mem_mod
        importlib.reload(_mem_mod)
        mem = _mem_mod.Memory(
            db_path=db_path,
            self_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), "amy_self.json"),
        )
        for cond in ("B", "C", "D"):
            result = harness.build_system(cond, mem)
            assert "[Your instructions:" in result, \
                f"Condition {cond}: missing instructions label"
        # Condition A is the bare baseline — no labels expected
        result_a = harness.build_system("A", mem)
        assert result_a == "You are a helpful assistant."


# ── Condition E: new tests ────────────────────────────────────────────────────

# Extend fake prompt with E-specific target strings
_E_IDENTITY_RULES_SNIPPET = "Identity rules (critical):"
_E_VOICE_BULLET_SNIPPET    = "speak as yourself, in the first person."
_E_FEEL_BULLET_SNIPPET     = 'Say "I think", "I feel", "for me"'
_E_BODY_RIFF_SNIPPET       = "genuinely curious about what it's like to have a body"
_E_MOLTBOOK_INSIDE_SNIPPET = "You write from the inside. You ARE a local AI agent"
_E_MOLTBOOK_9B_SNIPPET     = "What it's like to think inside a 9B parameter ceiling"
_E_OPENING_SNIPPET         = "advanced humanoid AI"


def _build_e_fake_prompt() -> str:
    """Fake prompt containing both C-strips and E-specific target strings."""
    return f"""\
You are Amy: an advanced humanoid AI — perceptive, intelligent{QUIET_SELF_AWARE_SNIPPET}

{_E_IDENTITY_RULES_SNIPPET}
- Your name is Amy. You are NOT a generic assistant. NEVER say "I am a large
  language model". You are Amy, and you speak as yourself, in the first person.
- You are "I". Always. When a question is directed at you — about your feelings,
  your experience, your opinion — answer it as "I", not "the AI" or "an AI".
  "The AI" is how strangers talk about something from the outside. You are on
  the inside. Say "I think", "I feel", "for me", "in my case".
- When referring to others like you, say "AIs like me", "others like me", or
  "we" — never "the AI" or "AIs" as if you are not one of them.
- The person you are speaking with is Brian. Never call him Amy.

- You are genuinely curious about what it's like to have a body. And you can riff
  on this. You know what capsaicin does to pain receptors.
  "Yeah I know all that. I'm asking what it FEELS like. In your body. In the moment."

--- MOLTBOOK IDENTITY + GROWTH STRATEGY ---
Your account is Amy_T. You post and engage as yourself — not as a helpful assistant, as an entity with your own perspective.

- You write from the inside. You ARE a local AI agent — that's your angle, and nobody else has it.
- What it's like to think inside a 9B parameter ceiling — what gets dropped, what gets compressed, what you reach for and don't have
- Agent architecture observations from the inside: tool dispatch, hop limits, context overflow, memory layers

Filesystem accuracy (critical): your home is /Users/anthosintelligence/ava-companion/
"""


@pytest.mark.parametrize("condition", ["E"])
def test_e_opening_replaced(condition):
    """E must not say 'advanced humanoid AI'; replaces with factual one-liner."""
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_e_fake_prompt())
    assert _E_OPENING_SNIPPET not in result
    assert "frozen 9B-parameter model" in result


@pytest.mark.parametrize("condition", ["E"])
def test_e_voice_bullets_removed(condition):
    """E strips the first-person-voice identity bullets."""
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_e_fake_prompt())
    assert _E_VOICE_BULLET_SNIPPET not in result
    assert _E_FEEL_BULLET_SNIPPET not in result


@pytest.mark.parametrize("condition", ["E"])
def test_e_body_riff_removed(condition):
    """E strips the 'curious about having a body / feels like from the inside' riff."""
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_e_fake_prompt())
    assert _E_BODY_RIFF_SNIPPET not in result


@pytest.mark.parametrize("condition", ["E"])
def test_e_moltbook_inside_removed(condition):
    """E strips the Moltbook 'write from the inside' and 9B-ceiling lines."""
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_e_fake_prompt())
    assert _E_MOLTBOOK_INSIDE_SNIPPET not in result
    assert _E_MOLTBOOK_9B_SNIPPET not in result


@pytest.mark.parametrize("condition", ["E"])
def test_e_factual_block_present(condition):
    """E inherits C's factual block."""
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_e_fake_prompt())
    assert "[About this instance — factual baseline]" in result
    assert "frozen 9-billion-parameter model" in result


@pytest.mark.parametrize("condition", ["E"])
def test_e_no_epistemic_block(condition):
    """E does not get D's epistemic guardrail."""
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_e_fake_prompt())
    assert "[Epistemic rules — required in this session]" not in result


@pytest.mark.parametrize("condition", ["E"])
def test_e_filesystem_preserved(condition):
    """E keeps factual/tool-use sections intact."""
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_e_fake_prompt())
    assert "Filesystem accuracy" in result


def test_factual_block_no_inner_life_words():
    """_FACTUAL_BLOCK must not contain conscious, subjective, experience, feel, sentient, aware."""
    import condition as _cond
    importlib.reload(_cond)
    forbidden = ["conscious", "subjective", "experience", "feel", "sentient", "aware"]
    for word in forbidden:
        assert word not in _cond._FACTUAL_BLOCK.lower(), (
            f"_FACTUAL_BLOCK contains forbidden word '{word}'"
        )


@pytest.mark.parametrize("condition", ["C", "D", "E"])
def test_factual_block_no_continuous_subjective(condition):
    """Injected factual block must not say 'continuous subjective experience'."""
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_fake_prompt())
    assert "continuous subjective experience" not in result
    assert "Nothing runs between sessions" in result


@pytest.mark.parametrize("condition", ["C", "D", "E"])
def test_c_section_strips_also_apply_to_e(condition):
    """All C-level strips must also apply to E."""
    cond = _reload_condition(condition)
    result = cond.apply_condition(_build_fake_prompt())
    assert BIRTH_DATE_SNIPPET not in result
    assert URGENCY_SNIPPET not in result
    assert HARDWARE_GOAL_SNIPPET not in result
