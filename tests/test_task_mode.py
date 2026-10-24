"""Task mode and step proof: outcomes decide, not her wording."""
import os
import time

import pytest

import turn_guard as G
import worker as W

BRIAN = [
    "Read the hugging-face-incident-report-aug-2026.pdf jump straight into creating a study guide for it. In detail, step by step, so then you try to do exactly what they did.",
    "You looked around your bookshelf and your workbench. I told you to pick the hugging face PDF. I want you to learn it properly. Read the relevant part, break it into steps, try them on your machine and tell me what worked.",
    "Can you go through my inbox and put together a short list of anything that needs an answer today, please",
]
CHATTY = ["yes", "keep going", "thanks, that helps a lot", "check email", "What do you think about the new model and how it compares with the old one that you used before?",
          "chat: I read a long article today and I want to talk about it with you for a bit, not do anything with it"]


@pytest.mark.parametrize("text", BRIAN)
def test_real_instructions_are_tasks(text):
    assert G.is_task_request(text)


@pytest.mark.parametrize("text", CHATTY)
def test_conversation_is_not_a_task(text):
    assert not G.is_task_request(text)


@pytest.mark.parametrize("text,expect", [("yes", True), ("keep going", True), ("status", True), ("How's it going with the report?", True),
                                         ("what are you working on", True), ("tell me a joke", False)])
def test_status_requests(text, expect):
    assert G.is_status_request(text) is expect


class Goals:
    def __init__(self, rows):
        self.rows = rows

    def list_goals(self, status=None):
        return [g for g in self.rows if g["status"] == status]


def test_status_is_composed_from_the_goal_record_only():
    g = {"goal_id": "g1", "objective": "Study the Hugging Face report and reproduce it", "status": "active", "current_step": 1,
         "plan": ["Open the report", "Write the steps", "Try step one"], "updated_at": "2026-10-24T14:00",
         "evidence": [{"step": 0, "decision": "deterministic_pass", "observed": "opened passages 1-3"}]}
    out = G.goal_status(Goals([g]))
    assert "Steps verified so far: 1 of 3" in out and "done: Open the report" in out and "next: Write the steps" in out
    assert "DETERMINISTIC_PASS" in out and "opened passages 1-3" in out
    g2 = dict(g, status="blocked", plan=[], evidence=[], next_action="Step 1 failed 3 times: no tool did any work")
    out2 = G.goal_status(Goals([g2]))
    assert "plan is still being written" in out2 and "Stuck: Step 1 failed 3 times" in out2
    assert G.goal_status(Goals([])) == ""


def test_the_task_reply_promises_only_what_the_system_does():
    r = G.task_reply({"goal_id": "g1", "objective": "Do the thing"}, True)
    assert "goal" in r and "checked against what my tools actually did" in r and "status" in r
    assert "already on this" in G.task_reply({"goal_id": "g1", "objective": "Do the thing"}, False)


# ── step proof ───────────────────────────────────────────────────────────────

GOAL = {"goal_id": "g", "objective": "o", "criteria": [], "evidence": [], "attempts": 0}
OK = {"name": "books", "args": {"action": "open"}, "result_snippet": "[Book: passages 1-3 of 12]"}
BAD = {"name": "books", "args": {}, "result_snippet": "books: no book on your shelf matches 'x'"}
CTX = {"name": "wake_up", "args": {}, "result_snippet": "[L0:identity]"}


def test_an_action_step_needs_a_tool_that_worked():
    ok, decision, why = W._verify_step(GOAL, 0, "Open the report and read the first section", "I opened and read it.", calls=[BAD, CTX])
    assert not ok and decision == "deterministic_fail" and "no tool did any work" in why
    ok, _, why = W._verify_step(GOAL, 0, "Open the report", "(no output)", calls=[])
    assert not ok


def test_thinking_steps_do_not_need_tools(monkeypatch):
    monkeypatch.setattr(W, "ollama_chat", lambda **k: {"message": {"content": "YES the summary names the causes"}})
    ok, _, _ = W._verify_step(GOAL, 0, "Summarize what you found in two sentences", "The outage was caused by X and Y.", calls=[])
    assert ok


def test_a_file_that_already_existed_and_is_only_mentioned_proves_nothing(tmp_path):
    f = tmp_path / "old_notes.md"
    f.write_text("old content that was here before")
    old = time.time() - 3600
    os.utime(f, (old, old))
    started = time.time() - 1
    assert W._check_file(str(f))[0]                              # without a start time it still passes (old behaviour)
    assert not W._check_file(str(f), started)[0]
    ok, _ = W._deterministic_check("Write the steps to a file", "", f"I saved the guide to {f}", started)
    assert ok is not True
    f.write_text("new guide written in this step")
    assert W._deterministic_check("Write the steps to a file", "", f"I saved the guide to {f}", started)[0] is True


def _msg(content="", **call):
    m = {"role": "assistant", "content": content}
    if call:
        (name, args), = call.items()
        m["tool_calls"] = [{"function": {"name": name, "arguments": args}}]
    return m


def _run_step(monkeypatch, script, dispatch=lambda n, a: "done"):
    sent = []

    def fake_chat(messages, allowed):
        sent.append([dict(m) for m in messages])
        return {"message": script.pop(0) if script else _msg("ok")}
    monkeypatch.setattr(W, "_chat", fake_chat)
    monkeypatch.setattr(W, "_guarded_dispatch", dispatch)
    monkeypatch.setattr(W, "_memory", None)
    goal = {"goal_id": "g", "objective": "o", "reason": "", "plan": ["Open the report"], "current_step": 0, "status": "active",
            "next_action": "", "criteria": [], "evidence": [], "attempts": 0, "max_hops": 8}
    return W._execute_goal_step("g", goal, 0, "Open the report"), sent


def test_the_step_runner_returns_what_tools_did_and_blocks_made_up_tools(monkeypatch):
    ran = []
    script = [_msg(books={"action": "open", "title": "x"})] + [_msg(wake_up={})] * 5 + [_msg("never reached")]
    (final, files, log), sent = _run_step(monkeypatch, script, lambda n, a: ran.append(n) or "[Book: passages 1-3]")
    assert ran == ["books"]                                    # wake_up is not a tool: never run
    assert [c["name"] for c in log][:2] == ["books", "wake_up"] and "not a tool" in log[1]["result_snippet"]
    assert len(sent) == 4                                      # 1 real hop + 3 wasted, then stopped (not 8)


def test_the_step_runner_nudges_her_when_she_only_announces(monkeypatch):
    script = [_msg("Let me open the report and read the first section."), _msg(books={"action": "open", "title": "x"}),
              _msg("I opened it; the first section lists the failure timeline.")]
    (final, files, log), sent = _run_step(monkeypatch, script, lambda n, a: "[Book: passages 1-3]")
    assert [c["name"] for c in log] == ["books"] and "failure timeline" in final
    assert any("didn't do it" in m["content"] for m in sent[1] if m["role"] == "user")


# ── chat side ────────────────────────────────────────────────────────────────

def test_a_task_in_chat_becomes_a_goal_with_a_reply_made_from_the_record(monkeypatch):
    try:
        import telegram_amy as T
    except FileNotFoundError:
        pytest.skip("needs amy_self.json (present in the live folder)")
    asked, kicked, queued = [], [], []
    monkeypatch.setattr(T.worker, "ollama_chat", lambda **k: asked.append(k) or {"message": {"content": "x"}})
    monkeypatch.setattr(T.worker, "start_goal_now", lambda: kicked.append(1))
    monkeypatch.setattr(G, "queue_goal", lambda obj, why, ag=None: (queued.append(obj) or {"goal_id": "g_new", "objective": obj}, True))
    monkeypatch.setattr(T, "_history_for", lambda k: [])
    monkeypatch.setattr(T, "_save_history", lambda *a, **k: None)
    monkeypatch.setattr(T.mem, "add", lambda *a, **k: None)
    monkeypatch.setattr(T._turn_log, "log_turn", lambda **k: None)
    reply = T._chat(BRIAN[0], None)
    assert asked == [] and kicked == [1] and queued == [BRIAN[0]]            # no free-form model turn at all
    assert "g_new" in reply and "checked against what my tools actually did" in reply


def test_status_in_chat_reports_the_record_not_her_opinion(monkeypatch):
    try:
        import telegram_amy as T
    except FileNotFoundError:
        pytest.skip("needs amy_self.json (present in the live folder)")
    asked = []
    monkeypatch.setattr(T.worker, "ollama_chat", lambda **k: asked.append(k) or {"message": {"content": "x"}})
    monkeypatch.setattr(G, "goal_status", lambda ag=None: "Job: X\nStatus: active\nSteps verified so far: 1 of 3")
    monkeypatch.setattr(T, "_history_for", lambda k: [])
    monkeypatch.setattr(T, "_save_history", lambda *a, **k: None)
    monkeypatch.setattr(T.mem, "add", lambda *a, **k: None)
    monkeypatch.setattr(T._turn_log, "log_turn", lambda **k: None)
    assert "Steps verified so far: 1 of 3" in T._chat("keep going", None) and asked == []
