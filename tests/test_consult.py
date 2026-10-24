"""consult: ask Claude or a local model, free local path included; answers are sources; capped per day."""
import json
import urllib.error

import pytest

import consult as C
import tools


@pytest.fixture(autouse=True)
def bench(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "WORKBENCH", tmp_path / "bench")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path / "bench"


Q = "My script raises KeyError on line 4 when the dict is empty, what should I change?"


def local_chat(**kw):
    local_chat.seen = kw
    return {"message": {"content": "Use dict.get with a default."}}


def test_a_local_model_works_with_no_key_and_no_paid_service(bench):
    out = C.ask(Q, "ollama:hf.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF:Q4_K_M", chat=local_chat)
    assert "Answer from hf.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF:Q4_K_M" in out and "source, not a fact" in out and "dict.get" in out
    assert local_chat.seen["keep_alive"] == 0 and local_chat.seen["model"].startswith("hf.co/")
    assert json.loads((bench / ".consult_log.jsonl").read_text())["model"].startswith("hf.co/")


def test_claude_needs_a_key_and_says_how_to_use_a_free_model_instead():
    with pytest.raises(C.ConsultError, match="ollama:<name>"):
        C.ask(Q, "claude")


def test_claude_is_called_with_the_key_and_the_text_comes_back(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen = {}

    def post(req):
        seen["key"] = req.get_header("X-api-key")
        seen["body"] = json.loads(req.data)
        return {"content": [{"type": "text", "text": "Try .get()"}]}
    out = C.ask(Q, "claude", post=post)
    assert seen["key"] == "test-key" and seen["body"]["messages"][0]["content"] == Q and "Try .get()" in out
    assert "test-key" not in out


def test_errors_are_plain(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    def boom(req):
        raise urllib.error.URLError("no internet")
    with pytest.raises(C.ConsultError, match="couldn't reach Claude"):
        C.ask(Q, "claude", post=boom)

    def bad(**kw):
        raise RuntimeError("model not found")
    with pytest.raises(C.ConsultError, match="ollama pull"):
        C.ask(Q, "ollama:nothing", chat=bad)
    with pytest.raises(C.ConsultError, match="real question"):
        C.ask("help", "claude")
    with pytest.raises(C.ConsultError, match="'claude' or 'ollama"):
        C.ask(Q, "gpt")


def test_a_daily_cap_stops_a_runaway_loop(monkeypatch):
    monkeypatch.setattr(C, "DAILY_CAP", 2)
    C.ask(Q, "ollama:x", chat=local_chat)
    C.ask(Q, "ollama:x", chat=local_chat)
    with pytest.raises(C.ConsultError, match="today's 2 consults"):
        C.ask(Q, "ollama:x", chat=local_chat)


def test_the_tool_is_wired_and_mentions_free_hugging_face_models():
    import worker
    t = next(t for t in tools.TOOLS if t["function"]["name"] == "consult")
    assert "hf.co" in t["function"]["description"] and "16 GB" in t["function"]["description"]
    assert "consult" in worker.ALLOWED_TOOLS
    assert tools.dispatch("consult", {"question": "short"}).startswith("consult: ")
