import time

import pytest

import thoughts as TH


class _State:
    busy = False

    def __init__(self, idle=600):
        self.idle = idle

    def seconds_idle(self):
        return self.idle


def _buffer(monkeypatch, autonomous, state=None):
    b = TH.ThoughtBuffer()
    b._amy_state = state or _State()
    b._history_ref = [{"role": "assistant", "content": "Yeah, I'm here."}]
    b._memory = None
    b._autonomous = autonomous
    ran = []
    monkeypatch.setattr(b, "_autonomous_session", lambda: ran.append("session"))
    monkeypatch.setattr(b, "_generate_thought", lambda: ran.append("thought"))
    return b, ran


def test_private_mode_never_runs_a_tool_session(monkeypatch):
    b, ran = _buffer(monkeypatch, autonomous=False)
    for _ in range(TH.AUTONOMOUS_EVERY_N * 2):
        b._tick()
    assert ran == ["thought"] * (TH.AUTONOMOUS_EVERY_N * 2)


def test_terminal_mode_still_runs_a_tool_session_every_nth_cycle(monkeypatch):
    b, ran = _buffer(monkeypatch, autonomous=True)
    for _ in range(TH.AUTONOMOUS_EVERY_N):
        b._tick()
    assert ran.count("session") == 1


@pytest.mark.parametrize("busy, idle", [(True, 600), (False, 10)])
def test_she_only_thinks_when_idle_and_not_mid_reply(monkeypatch, busy, idle):
    st = _State(idle)
    st.busy = busy
    b, ran = _buffer(monkeypatch, autonomous=False, state=st)
    b._tick()
    assert ran == []


def test_the_buffer_stops_filling_at_its_limit(monkeypatch):
    b, ran = _buffer(monkeypatch, autonomous=False)
    b._thoughts = [(time.time(), f"t{i}", "thought") for i in range(TH.MAX_BUFFER)]
    b._tick()
    assert ran == []


def test_thought_prompt_ends_on_a_user_turn(monkeypatch):
    # A history that ends on Amy's own reply made Qwen answer with nothing, so every thought was dropped.
    b = TH.ThoughtBuffer()
    b._amy_state, b._memory = _State(), None
    b._history_ref = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey"}]
    seen = {}

    def fake_call(fn, **kw):
        seen.update(kw)
        return {"message": {"content": "A quiet thought."}}

    monkeypatch.setattr(TH.heavy_job, "call", fake_call)
    monkeypatch.setattr(TH, "LOG_PATH", "/dev/null")
    b._generate_thought()
    assert seen["messages"][-1]["role"] == "user"
    assert seen["think"] is False
    assert [t[1] for t in b.drain_with_tags()] == ["A quiet thought."]


def test_prompts_name_her_real_hardware():
    text = "\n".join(TH._THOUGHT_PROMPTS)
    assert "2013 MacBook" not in text and "Jetson" not in text
    assert "GB10" in text


def test_thoughts_block_is_empty_without_thoughts_and_never_leaks_tags():
    import telegram_amy as T
    assert T._thoughts_block([]) == ""
    out = T._thoughts_block([(1.0, "The server hum is loud tonight.", "thought")])
    assert "The server hum is loud tonight." in out and "thought)" not in out


def test_chat_marks_her_busy_while_a_reply_runs_and_brian_active(monkeypatch):
    import telegram_amy as T
    seen = {}
    monkeypatch.setattr(T.amy_state, "last_activity", time.time() - 900)

    def fake_turn(*a, **k):
        seen["busy"] = T.amy_state.busy
        seen["idle"] = T.amy_state.seconds_idle()
        return "ok"

    monkeypatch.setattr(T, "_chat_turn", fake_turn)
    assert T._chat("hi") == "ok"
    assert seen["busy"] is True and seen["idle"] < 5
    assert T.amy_state.busy is False


def test_a_guest_message_marks_her_busy_but_does_not_reset_brians_idle_clock(monkeypatch):
    import telegram_amy as T
    monkeypatch.setattr(T.amy_state, "last_activity", time.time() - 900)
    monkeypatch.setattr(T, "_chat_turn", lambda *a, **k: "ok")
    T._chat("hi", person={"name": "Sam", "role": "guest"})
    assert T.amy_state.seconds_idle() > 800
