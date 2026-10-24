"""Thinking is off by default for every worker/chat model call: with it on, small token budgets come back empty."""
import ollama
import pytest

import worker as W


class Spy:
    def __init__(self, fail_think=False):
        self.calls, self.fail_think = [], fail_think

    def __call__(self, **kw):
        self.calls.append(dict(kw))
        if self.fail_think and "think" in kw:
            raise ollama.ResponseError("model does not support thinking")
        return {"message": {"content": "ok"}}


def test_thinking_is_off_unless_asked(monkeypatch):
    spy = Spy()
    monkeypatch.setattr(W.ollama, "chat", spy)
    W.ollama_chat(model="m", messages=[{"role": "user", "content": "hi"}])
    assert spy.calls[-1]["think"] is False
    W.ollama_chat(model="m", messages=[{"role": "user", "content": "hi"}], think=True)
    assert spy.calls[-1]["think"] is True


def test_a_model_that_rejects_the_think_option_is_asked_again_without_it(monkeypatch):
    spy = Spy(fail_think=True)
    monkeypatch.setattr(W.ollama, "chat", spy)
    r = W.ollama_chat(model="vision-model", messages=[{"role": "user", "content": "hi"}])
    assert r["message"]["content"] == "ok" and len(spy.calls) == 2 and "think" not in spy.calls[1]


def test_other_errors_are_not_swallowed(monkeypatch):
    def boom(**kw):
        raise ollama.ResponseError("model not found")
    monkeypatch.setattr(W.ollama, "chat", boom)
    with pytest.raises(ollama.ResponseError, match="not found"):
        W.ollama_chat(model="x", messages=[])


def test_planning_works_when_the_model_only_answers_with_thinking_off(monkeypatch):
    """A fake model that, like Gemma, returns nothing when thinking is on and its 600 tokens are used up."""
    def fake(**kw):
        if kw.get("think") is not False:
            return {"message": {"content": ""}}
        return {"message": {"content": "1. Open the report\n2. Read the first section\n3. Try step one\n4. Write what worked"}}
    monkeypatch.setattr(W.ollama, "chat", fake)
    monkeypatch.setattr(W, "_memory", None)
    updates = {}
    monkeypatch.setattr(W._goals, "set_plan", lambda gid, plan, criteria=None: updates.update(plan=plan) or {})
    monkeypatch.setattr(W._goals, "_update", lambda gid, **f: updates.update(f))
    W._execute_goal_planning("g1", {"objective": "Study the report", "reason": ""})
    assert len(updates.get("plan", [])) == 4 and "planning returned no steps" not in str(updates.get("next_action", ""))
