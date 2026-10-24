"""Chat sends a trimmed tool list (fewer schema tokens, faster prefill); nothing is actually removed —
background-only tools stay reachable through a goal via worker.ALLOWED_TOOLS."""
import json

import tools
import worker

# Every tool name ever logged as called directly in a chat turn (logs/turns.jsonl, 268 turns,
# 2026-10 history). A reduction must never drop one of these — that would be a real regression,
# not an efficiency win.
HISTORICALLY_USED_IN_CHAT = {
    "engram_memory", "moltbook_check_in", "web_search", "check_email", "list_files", "self_improve",
    "read_file", "read_skill", "read_article", "moltbook_engage", "browse", "moltbook_post", "write_file",
    "task_push", "run_code", "read_event_log", "read_feed", "system_status", "books", "moltbook_verify",
    "list_work", "send_email", "avatar_expression", "avatar_gaze", "read_journal", "git_status",
    "read_deliverable", "create_autonomous_goal", "fund_status", "check_syntax", "git_commit", "git_push",
    "task_list", "manage_channels",
}


def test_chat_tools_is_smaller_and_cheaper_than_the_full_list():
    full, chat = tools.TOOLS, tools.chat_tools()
    assert len(chat) < len(full)
    full_sz, chat_sz = len(json.dumps(full)), len(json.dumps(chat))
    assert chat_sz < full_sz * 0.8   # a real cut, not a token or two


def test_nothing_historically_used_in_chat_was_dropped():
    names = {t["function"]["name"] for t in tools.chat_tools()}
    assert HISTORICALLY_USED_IN_CHAT <= names


def test_every_trimmed_tool_stays_reachable_through_a_goal():
    chat_names = {t["function"]["name"] for t in tools.chat_tools()}
    dropped = {t["function"]["name"] for t in tools.TOOLS} - chat_names
    assert dropped == tools.CHAT_BACKGROUND_ONLY and dropped
    assert dropped <= worker.ALLOWED_TOOLS, dropped - worker.ALLOWED_TOOLS


def test_telegram_chat_uses_the_trimmed_list_for_the_owner():
    import telegram_amy as T
    import inspect
    src = inspect.getsource(T._chat)
    assert "tools.chat_tools()" in src and "tools.TOOLS if is_owner" not in src


def test_a_background_only_tool_still_dispatches_when_called(monkeypatch):
    """Dispatch itself doesn't care which list offered the tool — removing it from chat_tools()
    must not break the handler a goal would call."""
    seen = []
    monkeypatch.setattr("agent_comms.send_message" if False else "builtins.print", lambda *a, **k: seen.append(a))
    # income_eval and family_fund are background-only; confirm dispatch still has a real handler, not a fallback.
    out1 = tools.dispatch("family_fund", {"action": "status"}, None)
    assert out1 is not None and not str(out1).startswith("unknown tool")
