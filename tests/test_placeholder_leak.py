"""
Tests for Amy grounding invariants: no birth-age placeholder, day count in prompt,
last-message timing fix, owner-only filtering.

Run:  cd /Users/anthosintelligence/ava-companion && pytest tests/test_placeholder_leak.py -v
"""
import importlib
import os
import sqlite3
import tempfile
import sys
import types
import unittest.mock as mock
from datetime import datetime, timezone, timedelta, date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

PLACEHOLDER = "__BIRTH_AGE__"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_test_db(user_ts=None, source="telegram") -> str:
    """Create a temp DB. If user_ts provided, insert a user row with that ts."""
    path = tempfile.mktemp(suffix=".db")
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE mem ("
        "id INTEGER PRIMARY KEY, role TEXT, text TEXT, emb BLOB, ts TEXT, source TEXT)"
    )
    if user_ts:
        db.execute(
            "INSERT INTO mem (role, text, ts, source) VALUES (?, ?, ?, ?)",
            ("user", "hello", user_ts, source),
        )
    db.commit()
    db.close()
    return path


def _make_mem(db_path):
    """Return a Memory instance backed by the given DB (no Ollama calls)."""
    from memory import Memory
    return Memory(db_path=db_path)


# ── 1. Placeholder is gone from all source files and prompts ─────────────────

def test_core_prompt_no_placeholder():
    """CORE_PROMPT must NOT contain the placeholder — it was removed."""
    from amy_persona import CORE_PROMPT
    assert PLACEHOLDER not in CORE_PROMPT, (
        "CORE_PROMPT still contains __BIRTH_AGE__; the placeholder should have been removed"
    )


def test_get_core_prompt_no_placeholder():
    """get_core_prompt() must not return __BIRTH_AGE__ verbatim."""
    from amy_persona import get_core_prompt
    result = get_core_prompt()
    assert PLACEHOLDER not in result, "get_core_prompt() returned __BIRTH_AGE__ verbatim"


def test_persona_source_file_no_placeholder():
    """The amy_persona.py source file itself must not contain __BIRTH_AGE__."""
    import inspect
    from amy_persona import CORE_PROMPT
    import amy_persona
    src = inspect.getsource(amy_persona)
    assert PLACEHOLDER not in src, (
        "amy_persona.py source still contains __BIRTH_AGE__"
    )


def test_persona_contains_birth_date_text():
    """Persona must still carry the fixed birth-date statement."""
    from amy_persona import CORE_PROMPT
    assert "July 22, 2026" in CORE_PROMPT, (
        "CORE_PROMPT should state 'July 22, 2026' as the fixed setup date"
    )


# ── 2. build_context must not contain __BIRTH_AGE__ ─────────────────────────

def test_build_context_no_placeholder():
    """companion.build_context system prompt must not contain __BIRTH_AGE__."""
    db_path = _make_test_db()
    try:
        mem = _make_mem(db_path)
        with mock.patch.object(mem, "wake_up", return_value="[wake_up: test]"):
            import companion
            importlib.reload(companion)
            msgs = companion.build_context(mem, "hello")
            system = msgs[0]["content"]
            assert PLACEHOLDER not in system, (
                f"build_context system prompt contains {PLACEHOLDER}"
            )
    finally:
        os.unlink(db_path)


# ── 3. telegram _build_system must not contain __BIRTH_AGE__ ─────────────────

def test_telegram_build_system_no_placeholder():
    """telegram_amy._build_system system prompt must not contain __BIRTH_AGE__."""
    db_path = _make_test_db()
    try:
        import telegram_amy as ta
        importlib.reload(ta)
        with mock.patch.object(ta.mem, "wake_up", return_value="[wake_up: test]"):
            result = ta._build_system(history_len=0)
            assert PLACEHOLDER not in result, (
                f"telegram_amy._build_system returned {PLACEHOLDER}"
            )
    except Exception as e:
        pytest.skip(f"telegram_amy not importable in test env: {e}")
    finally:
        os.unlink(db_path)


# ── 4. journal / spontaneous / warmup paths must not contain __BIRTH_AGE__ ───

def test_journal_prompt_no_placeholder(monkeypatch):
    """The journal_prompt built in companion must not contain __BIRTH_AGE__."""
    import companion
    importlib.reload(companion)
    import inspect
    full_source = inspect.getsource(companion)
    idx = full_source.find("journal_prompt")
    assert idx != -1, "_write_journal / journal_prompt block not found in companion.py"
    snippet = full_source[idx:idx + 500]
    assert PLACEHOLDER not in snippet, "journal_prompt block still references __BIRTH_AGE__"
    assert "get_core_prompt()" in snippet, (
        "journal_prompt block must call get_core_prompt(), not raw CORE_PROMPT"
    )


def test_spontaneous_thought_no_placeholder():
    """_spontaneous_thought must use get_core_prompt(), not raw CORE_PROMPT."""
    import inspect
    import companion
    source = inspect.getsource(companion._spontaneous_thought)
    assert "get_core_prompt()" in source, (
        "_spontaneous_thought must call get_core_prompt()"
    )
    assert PLACEHOLDER not in source, (
        "_spontaneous_thought source still contains __BIRTH_AGE__"
    )


def test_warmup_ping_no_placeholder():
    """KV warmup ping must use get_core_prompt(), not raw CORE_PROMPT."""
    import inspect
    import companion
    warmup_src = ""
    for name in dir(companion):
        if "warm" in name.lower() or "ping" in name.lower():
            try:
                warmup_src = inspect.getsource(getattr(companion, name))
                break
            except (TypeError, OSError):
                pass
    if not warmup_src:
        full = inspect.getsource(companion)
        idx = full.find("KV cache primed")
        assert idx != -1, "warmup/ping block not found in companion.py"
        warmup_src = full[max(0, idx - 500):idx + 200]
    assert PLACEHOLDER not in warmup_src, (
        "warmup ping still references __BIRTH_AGE__"
    )
    assert "CORE_PROMPT" not in warmup_src, (
        "warmup ping still uses raw CORE_PROMPT — should use get_core_prompt()"
    )


# ── 5. Day count appears in per-turn context ──────────────────────────────────

def test_build_context_day_count():
    """build_context [Current time] block must contain 'Days since you were set up:'."""
    db_path = _make_test_db()
    try:
        mem = _make_mem(db_path)
        with mock.patch.object(mem, "wake_up", return_value="[wake_up: test]"):
            import companion
            importlib.reload(companion)
            msgs = companion.build_context(mem, "hello")
            system = msgs[0]["content"]
            ct_idx = system.find("[Current time]")
            assert ct_idx != -1, "[Current time] block missing"
            time_block = system[ct_idx:ct_idx + 400]
            assert "Days since you were set up:" in time_block, (
                "Day count line missing from [Current time] block"
            )
            # Value should match today's actual count
            from amy_persona import BIRTH_DATE
            expected = (date.today() - BIRTH_DATE).days
            assert str(expected) in time_block, (
                f"Expected day count {expected} not found in [Current time] block"
            )
    finally:
        os.unlink(db_path)


# ── 6. last-message timing: prev_user_ts is used when supplied ───────────────

def test_build_context_uses_prev_user_ts():
    """build_context must report the prev_user_ts gap, not the freshly saved one."""
    db_path = _make_test_db()
    try:
        mem = _make_mem(db_path)
        prev_ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()

        with mock.patch.object(mem, "wake_up", return_value="[wake_up: test]"):
            import companion
            importlib.reload(companion)
            msgs = companion.build_context(mem, "current message", prev_user_ts=prev_ts)
            system = msgs[0]["content"]
            ct_idx = system.find("[Current time]")
            assert ct_idx != -1, "[Current time] block missing from system prompt"
            time_block = system[ct_idx:ct_idx + 300]
            assert "Last message from Brian:" in time_block
            assert "Last message from Brian: 0 min ago" not in time_block, (
                "build_context reported 0 min ago in [Current time] even with prev_user_ts set"
            )
    finally:
        os.unlink(db_path)


def test_build_context_skips_last_msg_when_no_prev():
    """When prev_user_ts=None and DB is empty, no last-message line appears."""
    db_path = _make_test_db()  # no user rows
    try:
        mem = _make_mem(db_path)
        with mock.patch.object(mem, "wake_up", return_value="[wake_up: test]"):
            import companion
            importlib.reload(companion)
            msgs = companion.build_context(mem, "hello", prev_user_ts=None)
            system = msgs[0]["content"]
            assert "Last message from Brian:" not in system
    finally:
        os.unlink(db_path)


def test_build_context_zero_gap_not_reported_from_live_db():
    """When the current message is already in the DB (race), prev_user_ts prevents 0 min."""
    db_path = _make_test_db()
    try:
        mem = _make_mem(db_path)
        now_ts = datetime.now(timezone.utc).isoformat()
        mem.db.execute("INSERT INTO mem (role, text, ts, source) VALUES (?, ?, ?, ?)",
                       ("user", "just now", now_ts, "telegram"))
        mem.db.commit()

        with mock.patch.object(mem, "wake_up", return_value=""):
            import companion
            importlib.reload(companion)
            # Override with a real prev ts → line appears with non-zero gap
            thirty_ago = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
            msgs3 = companion.build_context(mem, "just now", prev_user_ts=thirty_ago)
            system3 = msgs3[0]["content"]
            ct_idx3 = system3.find("[Current time]")
            assert ct_idx3 != -1, "[Current time] block missing from system prompt"
            time_block3 = system3[ct_idx3:ct_idx3 + 300]
            assert "Last message from Brian:" in time_block3
            assert "Last message from Brian: 0 min ago" not in time_block3, (
                "build_context reported 0 min ago in [Current time] when prev_user_ts was 30 min ago"
            )
    finally:
        os.unlink(db_path)


# ── 7. most_recent_user_ts filters to owner (source='telegram') ──────────────

def test_most_recent_user_ts_ignores_guest_messages():
    """most_recent_user_ts() must not return a guest message timestamp."""
    # Insert a guest message (recent) and an owner message (older)
    path = tempfile.mktemp(suffix=".db")
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE mem ("
        "id INTEGER PRIMARY KEY, role TEXT, text TEXT, emb BLOB, ts TEXT, source TEXT)"
    )
    owner_ts = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
    guest_ts = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    db.execute("INSERT INTO mem (role, text, ts, source) VALUES (?, ?, ?, ?)",
               ("user", "owner msg", owner_ts, "telegram"))
    db.execute("INSERT INTO mem (role, text, ts, source) VALUES (?, ?, ?, ?)",
               ("user", "guest msg", guest_ts, "telegram:guest123"))
    db.commit()
    db.close()
    try:
        from memory import Memory
        m = Memory(db_path=path)
        result = m.most_recent_user_ts()
        assert result == owner_ts, (
            f"most_recent_user_ts returned guest ts {result!r}, expected owner ts {owner_ts!r}"
        )
    finally:
        os.unlink(path)


def test_most_recent_user_ts_returns_none_when_only_guest():
    """most_recent_user_ts() returns None when the DB has only guest messages."""
    path = tempfile.mktemp(suffix=".db")
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE mem ("
        "id INTEGER PRIMARY KEY, role TEXT, text TEXT, emb BLOB, ts TEXT, source TEXT)"
    )
    guest_ts = datetime.now(timezone.utc).isoformat()
    db.execute("INSERT INTO mem (role, text, ts, source) VALUES (?, ?, ?, ?)",
               ("user", "guest msg", guest_ts, "telegram:guest123"))
    db.commit()
    db.close()
    try:
        from memory import Memory
        m = Memory(db_path=path)
        result = m.most_recent_user_ts()
        assert result is None, (
            f"most_recent_user_ts should return None for guest-only DB, got {result!r}"
        )
    finally:
        os.unlink(path)
