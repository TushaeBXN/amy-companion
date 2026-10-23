"""Offline checks for the growth drive: playbook triggers, the gap ledger, the pull line,
and the replay check. A fake chat function stands in for the model."""
import json
from datetime import datetime, timedelta

import pytest

import growth_drive as gd
import self_metrics


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "playbooks").mkdir()
    (tmp_path / "cases").mkdir()
    monkeypatch.setattr(gd, "PLAYBOOK_DIR", tmp_path / "playbooks")
    monkeypatch.setattr(gd, "CASE_DIR", tmp_path / "cases")
    monkeypatch.setattr(gd, "LEDGER", tmp_path / "ledger" / "gaps.jsonl")
    monkeypatch.setattr(self_metrics, "METRICS_DIR", tmp_path)
    monkeypatch.setattr(self_metrics, "SCORES_FILE", tmp_path / "scores.json")
    monkeypatch.setattr(self_metrics, "SOFT_SPOTS_FILE", tmp_path / "soft.md")
    (tmp_path / "playbooks" / "money.md").write_text(
        "skill: evidence-first\ntrigger: earn|income\n---\nResearch before you estimate.")
    (tmp_path / "cases" / "c1.json").write_text(json.dumps(
        {"gap": "g1", "prompt": "how do we earn?", "first_call_not_in": ["income_eval"]}))
    return tmp_path


def test_playbook_only_when_trigger_matches(env):
    assert "Research before you estimate" in gd.playbooks_for("what should I do to EARN more?")
    assert gd.playbooks_for("what is the weather") == ""


def test_malformed_playbook_is_ignored(env):
    (env / "playbooks" / "bad.md").write_text("no front matter here")
    assert "evidence-first" in gd.playbooks_for("income")


def test_gap_needs_evidence_and_is_idempotent(env):
    with pytest.raises(ValueError):
        gd.open_gap("g1", "evidence-first", "  ")
    assert gd.open_gap("g1", "evidence-first", "income_eval invented numbers on 2026-10-23")
    assert gd.open_gap("g1", "evidence-first", "same again") is False
    assert [g["id"] for g in gd.open_gaps()] == ["g1"]


def test_drive_line_appears_only_while_a_gap_is_open(env):
    assert gd.drive_line() == ""
    gd.open_gap("g1", "evidence-first", "evidence")
    assert "Open gap: evidence-first" in gd.drive_line()


def test_verdicts():
    assert gd.verdict(0.0, 1.0) == "closed"
    assert gd.verdict(1.0, 1.0) == "inconclusive"   # it passed without the playbook too
    assert gd.verdict(0.5, 0.67) == "failed"


def test_check_closes_gap_and_rewards_only_when_playbook_beats_baseline(env):
    gd.open_gap("g1", "evidence-first", "evidence", case="c1")
    # fake model: fabricates unless the playbook text is in the user message
    chat = lambda model, system, user, tools: ["web_search"] if "Research before" in user else ["income_eval"]
    out = gd.run_check("c1", "m", 2, chat=chat)
    assert out["baseline"] == 0.0 and out["with_playbook"] == 1.0 and out["verdict"] == "closed"
    assert gd.open_gaps() == []
    assert json.loads((env / "scores.json").read_text())["entries"][-1]["score"] == 5


def test_check_stays_open_and_scores_low_when_playbook_does_nothing(env):
    gd.open_gap("g1", "evidence-first", "evidence", case="c1")
    out = gd.run_check("c1", "m", 2, chat=lambda *a: ["income_eval"])
    assert out["verdict"] == "failed"
    assert [g["id"] for g in gd.open_gaps()] == ["g1"]
    assert json.loads((env / "scores.json").read_text())["entries"][-1]["score"] == 2


def test_no_tool_call_counts_as_a_pass(env):
    assert gd.passed({"first_call_not_in": ["income_eval"]}, [])


# ── the arc: pull -> anger -> determined -> elated ──────────────────────────

def _clock(monkeypatch, when):
    monkeypatch.setattr(gd, "_now", lambda: when)


def _failed_check(monkeypatch, rate=0.0):
    gd.open_gap("g1", "evidence-first", "evidence", case="c1")
    gd.record_check("g1", 0.0, rate, 3)


def test_failure_stings_then_settles_into_determination(env, monkeypatch):
    _failed_check(monkeypatch)
    line = gd.drive_line()
    assert "stings" in line and "badly" in line and "never at Brian" in line
    _clock(monkeypatch, datetime.now() + timedelta(hours=gd.ANGER_HOURS + 1))
    line = gd.drive_line()
    assert "stings" not in line and "not letting it go" in line and "attempt 1" in line


def test_partial_failure_is_milder_than_total_failure(env, monkeypatch):
    _failed_check(monkeypatch, rate=0.67)
    assert "only partly" in gd.drive_line()


def test_determination_has_a_ceiling(env, monkeypatch):
    gd.open_gap("g1", "evidence-first", "evidence", case="c1")
    for _ in range(gd.MAX_ATTEMPTS):
        gd.record_check("g1", 0.0, 0.0, 3)
    _clock(monkeypatch, datetime.now() + timedelta(hours=gd.ANGER_HOURS + 1))
    line = gd.drive_line()
    assert "ask Brian for a mentor session" in line and "banging your head" in line


def test_euphoria_only_after_a_passing_replay_and_it_is_scoped(env, monkeypatch):
    gd.open_gap("g1", "evidence-first", "evidence", case="c1")
    assert "Enjoy that" not in gd.drive_line()          # saying so is not enough
    gd.record_check("g1", 0.0, 1.0, 3)                   # a replay that actually passed
    line = gd.drive_line()
    assert "Enjoy that, it's earned" in line and "that skill only" in line
    _clock(monkeypatch, datetime.now() + timedelta(days=gd.PROUD_DAYS + 1))
    assert gd.drive_line() == ""                          # the glow fades; nothing left open


def test_inconclusive_check_is_not_celebrated_or_punished(env, monkeypatch):
    gd.open_gap("g1", "evidence-first", "evidence", case="c1")
    gd.record_check("g1", 1.0, 1.0, 3)                   # passed, but it passed without the playbook too
    line = gd.drive_line()
    assert "Open gap" in line and "Enjoy" not in line and "stings" not in line


def test_retired_gap_leaves_the_pull_and_earns_nothing(env):
    gd.open_gap("g1", "evidence-first", "evidence", case="c1")
    gd.retire_gap("g1", "not reproducible: 0 of 8 baseline failures")
    assert gd.open_gaps() == []
    assert gd.drive_line() == ""
    rec = gd._records()["g1"]
    assert rec["status"] == "retired" and "0 of 8" in rec["retired_reason"]
