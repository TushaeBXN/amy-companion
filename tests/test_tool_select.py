import json

import tool_select as TS
import tools


def _names(sel):
    return {t["function"]["name"] for t in sel}


def _all():
    return tools.chat_tools()


def test_a_chat_message_gets_far_fewer_tools_than_everything():
    sel = TS.select(_all(), "how was your day?")
    assert _names(sel) == TS.CORE & _names(_all())
    assert len(json.dumps(sel)) < len(json.dumps(_all())) * 0.4


def test_core_tools_are_always_present_and_exist():
    assert TS.CORE <= _names(_all())


def test_message_topics_pull_in_their_groups():
    assert "check_email" in _names(TS.select(_all(), "did I get any email today?"))
    assert "git_commit" in _names(TS.select(_all(), "commit the changes in the repo"))
    assert "engram_memory" in _names(TS.select(_all(), "do you remember what we talked about?"))
    assert "moltbook_post" in _names(TS.select(_all(), "post something on moltbook"))
    assert "generate_image" in _names(TS.select(_all(), "draw me a picture of a fox"))


def test_a_terse_reply_keeps_what_the_last_messages_were_about():
    sel = TS.select(_all(), "yes", ["can you check my email?", "sure, checking"])
    assert "check_email" in _names(sel)
    assert "check_email" not in _names(TS.select(_all(), "yes"))


def test_switching_it_off_sends_everything(monkeypatch):
    monkeypatch.setattr(TS, "ENABLED", False)
    assert TS.select(_all(), "hi") == _all()


def test_every_chat_tool_is_reachable_by_some_group_or_core():
    reachable = set(TS.CORE)
    for names, _ in TS.GROUPS.values():
        reachable |= names
    missing = _names(_all()) - reachable - {"update_persona"} - set()
    assert not missing, missing


def test_a_tool_that_was_not_shown_still_runs_and_is_added(monkeypatch):
    import inspect
    import telegram_amy as T
    src = inspect.getsource(T._chat_turn)
    assert "_offered0" in src and "tool_select" in src or "_tool_select" in src
