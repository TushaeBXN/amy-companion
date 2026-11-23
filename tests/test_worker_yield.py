import time

import ollama
import pytest

import worker as W


def _chunk(text, done=False, calls=None):
    return ollama.ChatResponse(model="m", done=done,
                               message=ollama.Message(role="assistant", content=text, tool_calls=calls))


class _Stream:
    def __init__(self, chunks, on_chunk=None):
        self.chunks, self.on_chunk, self.closed = chunks, on_chunk, False

    def __iter__(self):
        for i, c in enumerate(self.chunks):
            if self.on_chunk:
                self.on_chunk(i)
            yield c

    def close(self):
        self.closed = True


def test_a_background_call_is_assembled_into_one_normal_response(monkeypatch):
    call = ollama.Message.ToolCall(function=ollama.Message.ToolCall.Function(name="web_search", arguments={"q": "x"}))
    st = _Stream([_chunk("Hel"), _chunk("lo"), _chunk("", done=True, calls=[call])])
    monkeypatch.setattr(W.ollama, "chat", lambda **kw: st)
    monkeypatch.setattr(W, "_last_user", 0.0)
    W._chat_pending.clear()
    r = W._chat_yielding_to_brian({"model": "m", "messages": []})
    assert r.message.content == "Hello" and r["message"]["content"] == "Hello"
    assert r.message.tool_calls[0].function.name == "web_search" and r.done and st.closed


def test_a_background_call_stops_when_brians_message_arrives(monkeypatch):
    st = _Stream([_chunk("a")] * 50, on_chunk=lambda i: setattr(W, "_last_user", time.time()) if i == 2 else None)
    monkeypatch.setattr(W.ollama, "chat", lambda **kw: st)
    monkeypatch.setattr(W, "_last_user", 0.0)
    W._chat_pending.clear()
    with pytest.raises(RuntimeError, match="skipped: chat message pending"):
        W._chat_yielding_to_brian({"model": "m", "messages": []})
    assert st.closed


def test_a_pending_chat_also_stops_it(monkeypatch):
    st = _Stream([_chunk("a")] * 5, on_chunk=lambda i: W._chat_pending.set() if i == 1 else None)
    monkeypatch.setattr(W.ollama, "chat", lambda **kw: st)
    monkeypatch.setattr(W, "_last_user", 0.0)
    try:
        with pytest.raises(RuntimeError, match="skipped: chat message pending"):
            W._chat_yielding_to_brian({"model": "m", "messages": []})
    finally:
        W._chat_pending.clear()


def test_chat_no_longer_unloads_the_model_when_a_task_is_running():
    import inspect
    assert "_evict_worker_model" not in inspect.getsource(W._ollama_chat)


def test_task_can_limit_its_tools():
    import json, os
    tasks = json.load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "tasks.json")))["tasks"]
    mb = next(t for t in tasks if t["id"] == "moltbook_checkin")
    import worker
    assert set(mb["tools"]) <= worker.ALLOWED_TOOLS
    assert "moltbook_engage" in mb["tools"] and len(mb["tools"]) <= 6


def test_must_run_step_is_enforced_in_source():
    # a task with must_run can't finish until run_code has been asked to run each required text
    import inspect, worker
    src = inspect.getsource(worker._run_task)
    assert "must_run" in src and "_code_ran" in src
