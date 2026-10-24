"""Limits are ceilings, not squeezes: a plan, a criteria list or a judgment cut off mid-way fails the task."""
import re
from pathlib import Path

import pytest

import worker as W

ROOT = Path(__file__).resolve().parent.parent


def test_no_worker_call_is_capped_below_a_sentence_or_two():
    caps = [int(n) for n in re.findall(r'"num_predict":\s*(\d+)', (ROOT / "worker.py").read_text())]
    assert caps and min(caps) >= 100, sorted(caps)[:5]


def test_planning_and_criteria_have_room_for_a_real_plan():
    src = (ROOT / "worker.py").read_text()
    assert re.search(r'"num_predict": 3000, "temperature": 0\.5', src)       # the plan
    assert re.search(r'"num_predict": 3500, "temperature": 0\.3', src)       # the success criteria for every step


def test_a_step_gets_enough_tool_calls_to_read_try_and_fix():
    assert W.MAX_HOPS >= 12
    src = (ROOT / "worker.py").read_text()
    assert "goal_max_hops = 24" in src and "goal_max_hops = 14" in src and "goal_max_hops = 6\n            complexity = \"simple\"" in src


def test_her_own_instructions_no_longer_tell_her_to_stop_early():
    r = W.scope_rules(14)
    assert "Each tool once per turn" not in r
    assert "different arguments" in r and "carries on automatically" in r and "don't ask whether to continue" in r.lower()


def test_chat_replies_and_hops_are_generous():
    src = (ROOT / "telegram_amy.py").read_text()
    assert '"AMY_MAX_REPLY_TOKENS", "4096"' in src and re.search(r"^MAX_HOPS\s*=\s*16", src, re.M)
