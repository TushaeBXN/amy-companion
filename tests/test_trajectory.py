"""Trajectory logger: Amy's own turns are recorded, and failed / limit-hit runs are kept with an outcome label."""
import json
import time

from ollama import Message

import trajectory as T


def _read(tmp_path):
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    return [json.loads(line) for line in files[0].read_text().splitlines()]


def _messages():
    call = Message.ToolCall(function=Message.ToolCall.Function(name="web_search", arguments={"q": "ollama"}))
    return [
        {"role": "system", "content": "private persona"},
        {"role": "user", "content": "find x"},
        Message(role="assistant", content="", tool_calls=[call]),
        {"role": "tool", "content": "result text"},
        Message(role="assistant", content="Done: found x."),
    ]


def _save(monkeypatch, tmp_path, **kw):
    monkeypatch.setattr(T, "TRAJ_DIR", str(tmp_path))
    args = dict(task_id="t1", messages=_messages(), tools_used=["web_search", "speak"], final="Done: found x.",
                started=time.time() - 5, steps=2, tool_errors=0, hit_limit=False)
    args.update(kw)
    T.save(**args)


def test_assistant_messages_and_tool_calls_are_recorded(monkeypatch, tmp_path):
    _save(monkeypatch, tmp_path)
    (rec,) = _read(tmp_path)
    assert rec["outcome"] == "success"
    assert [t["role"] for t in rec["turns"]] == ["user", "assistant", "tool", "assistant"]
    assert rec["turns"][1]["tool_calls"] == [{"name": "web_search", "arguments": {"q": "ollama"}}]
    assert rec["turns"][3]["content"] == "Done: found x."


def test_failed_and_limit_runs_are_kept_with_a_label(monkeypatch, tmp_path):
    _save(monkeypatch, tmp_path, final="(task failed: boom)", tools_used=["web_search"])
    _save(monkeypatch, tmp_path, hit_limit=True, tools_used=["web_search"])
    assert [r["outcome"] for r in _read(tmp_path)] == ["failed", "hit_limit"]


def test_trivial_success_is_still_skipped(monkeypatch, tmp_path):
    _save(monkeypatch, tmp_path, tools_used=["speak"])
    assert list(tmp_path.glob("*.jsonl")) == []
