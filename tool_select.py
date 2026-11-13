"""Which tools Amy is shown for a chat message.

All 57 chat tools cost ~9.9k tokens of a 16K window on every turn, and a 9B model picks worse from a long menu.
She gets a small always-on core plus the groups the message (or the last few) points at. Showing fewer tools never
removes one: a tool she calls that wasn't shown is still declared, still runs, and is added for the next hop.
AMY_TOOL_SELECT=0 turns this off and sends everything.
"""
import os
import re

ENABLED = os.environ.get("AMY_TOOL_SELECT", "1") != "0"

# Always shown: looking things up, remembering, and the one that lets her use the machine.
CORE = {"web_search", "read_article", "remember_fact", "run_code", "task_push"}

# group -> (tools, trigger words). Triggers match whole words or word starts, case-insensitive.
GROUPS = {
    "memory": ({"engram_memory"},
               r"remember|recall|memor|forgot|forget|last time|earlier|we (talked|discussed)|before|what did|did i|"
               r"told you|you said|history|yesterday|last (week|night)"),
    "files": ({"read_file", "write_file", "list_files", "read_pdf", "write_knowledge_file", "read_deliverable",
               "list_work", "check_syntax", "git_status", "git_commit", "git_push"},
              r"file|folder|director|read|open|pdf|document|save|write|edit|code|script|repo|git|commit|push|branch|"
              r"workbench|project|deliverable|syntax|bug|fix|build"),
    "web": ({"browse", "research_current", "read_feed", "books", "search_books"},
            r"browse|website|web ?page|site|link|url|http|news|feed|latest|today|current|research|book|read about|"
            r"look up|find out|google"),
    "mail_cal": ({"check_email", "send_email", "check_calendar", "manage_channels"},
                 r"e-?mail|inbox|mail|calendar|schedule|meeting|appointment|event|remind|channel"),
    "social": ({"moltbook_check_in", "moltbook_post", "moltbook_engage", "moltbook_verify"},
               r"moltbook|post|social|feed|notification|reply to"),
    "goals": ({"add_goal", "complete_goal", "create_autonomous_goal", "list_autonomous_goals",
               "update_autonomous_goal", "task_list", "task_complete", "fund_status", "fund_update"},
              r"goal|task|todo|to-do|job|queue|plan|project|fund|gb10|budget|money|income|working on|status|progress|"
              r"background|overnight|later"),
    "self": ({"self_improve", "self_metrics", "update_persona", "anchor", "study", "read_ai_reference", "read_skill",
              "prune_memory", "system_status", "read_event_log", "consult", "journal_entry", "read_journal"},
             r"yourself|your (own )?(code|persona|prompt|memory|rules|model|tools?|logs?)|learn|study|improve|anchor|"
             r"persona|journal|diary|metrics|status|health|log|skill|memory|consult|reflect|how are you running"),
    "body": ({"speak", "avatar_expression", "avatar_gaze", "hardware_neck", "generate_image"},
             r"speak|say (it )?out loud|voice|avatar|face|look (at|up|left|right)|neck|turn your|image|picture|"
             r"draw|generate|paint|photo of"),
}

_COMPILED = {g: re.compile(r"\b(?:" + pat + r")", re.I) for g, (_, pat) in GROUPS.items()}
_TERSE = re.compile(r"^\s*(y|yes|yep|yeah|ok|okay|sure|do it|go ahead|go on|continue|please|no|nope|thanks?)\b[\s.!]*$", re.I)


def groups_for(text: str) -> set:
    return {g for g, rx in _COMPILED.items() if rx.search(text or "")}


def select(all_tools: list, user_text: str, recent_user_texts=()) -> list:
    """The subset of all_tools to show for this message. Unknown/terse messages fall back to the previous turns'."""
    if not ENABLED:
        return all_tools
    text = user_text or ""
    picked = groups_for(text)
    if _TERSE.match(text) or (not picked and len(text.split()) <= 4):
        # "yes" / "do it" belongs to whatever she was just doing, so keep what the last messages pointed at.
        for t in list(recent_user_texts)[-2:]:
            picked |= groups_for(t)
    names = set(CORE)
    for g in picked:
        names |= GROUPS[g][0]
    return [t for t in all_tools if t["function"]["name"] in names]
