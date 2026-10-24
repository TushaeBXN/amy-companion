"""Keeps a chat turn honest and unstuck: what her tools really did, and what to say when she runs out of steps.

Two things went wrong in her Telegram turns (2026-10-24): she spent all 10 steps re-calling one tool, and the
wrap-up then asked her to say "what you did", so with nothing done she described work she never did."""
import re

# Calls that only load context or look at the machine. They are not the work Brian asked for.
META_TOOLS = {"wake_up", "engram_memory", "diary_read", "diary_write", "read_event_log", "system_status",
              "moltbook_check_in", "read_skill", "view_get", "view_set", "task_list", "list_work", "self_metrics"}

_FAILED = ("books:", "consult:", "study:", "anchor:", "read_pdf:", "browse:", "[tool_error]", "[already called",
           "not done", "unknown ", "error", "failed", "could not", "file not found", "no such", "no book",
           "blocked", "that tool is only", "nothing ")
_CLAIM = re.compile(r"\bI(?:'ve| have|'m| am)?\s+(?:just\s+|now\s+|already\s+)?(?:opened|read|reviewed|finished|completed|"
                    r"mapped(?: out)?|drafted|created|wrote|written|built|ran|tested|tried|looked (?:into|at|through)|"
                    r"scanned|identified|analy[sz]ed|studied|worked through|broke down|broken down|summari[sz]ed)\b", re.I)


def failed(result) -> bool:
    return str(result or "").strip().lower().startswith(_FAILED)


def real_work(calls: list) -> list:
    """Calls that did something and worked."""
    return [c for c in calls if c["name"] not in META_TOOLS and not failed(c.get("result_snippet"))]


def work_summary(calls: list, limit: int = 12) -> str:
    """What actually happened this turn, from the tool results themselves."""
    if not calls:
        return "You called no tools."
    lines = []
    for c in calls[-limit:]:
        args = c.get("args")
        a = ", ".join(f"{k}={str(v)[:40]}" for k, v in args.items()) if isinstance(args, dict) else str(args)[:60]
        res = str(c.get("result_snippet") or "").replace("\n", " ")[:110]
        lines.append(f"- {c['name']}({a}) -> {'FAILED: ' if failed(res) else ''}{res}")
    return "What your tools actually did this turn:\n" + "\n".join(lines)


def wrapup_prompt(calls: list) -> str:
    """The message that makes her give an honest account when she runs out of steps."""
    done = real_work(calls)
    return (f"[You've used all your tool steps for this message. {work_summary(calls)}\n"
            "Don't call any more tools. In a few sentences, tell Brian only what is true from that list: what you "
            + ("got done, " if done else "") +
            "what failed and why, and the single next step you would take. "
            + ("" if done else "You have not done the task yet, so say so plainly. ") +
            "Do not describe anything you did not do. Do not ask whether to continue: name the next step.]")


def unbacked_claim_note(reply: str, calls: list) -> str:
    """If the reply says she did work but no working tool call did it, a plain note to append. Else ''."""
    if not _CLAIM.search(reply or "") or real_work(calls):
        return ""
    return ("\n\n(Check on my own reply: no tool I called this turn actually did that work. "
            + work_summary(calls).replace("What your tools actually did this turn:", "What really happened:") + ")")


# ── handing unfinished work to the goal system ───────────────────────────────
# The worker already plans a goal into steps, does one step per round, checks it, retries, and tells Brian when
# it's done or blocked. Chat never used it: she narrated in chat and asked "want me to keep going?".

_CONTINUE_WORD = re.compile(r"^\s*(?:yes|yeah|yep|ok(?:ay)?|sure|go(?: on| ahead)?|keep going|continue|do it|"
                            r"proceed|and don'?t stop|yes and don'?t stop|please do)\W*$", re.I)
_ASKS_TO_CONTINUE = re.compile(r"(?:want me to|should i|shall i|would you like me to|do you want me to)\s+"
                               r"(?:keep going|continue|move on|proceed|go on|carry on|start|begin|dive)|"
                               r"\bkeep going\?|\bnext\b[^.?!]{0,60}\?\s*$", re.I)
SUBSTANTIAL = 60          # characters: a real request, not "yes"


def is_continue_word(text: str) -> bool:
    return bool(_CONTINUE_WORD.match(text or ""))


def asks_to_continue(reply: str) -> bool:
    return bool(_ASKS_TO_CONTINUE.search(reply or ""))


def objective_for(user_text: str, history: list) -> str:
    """The job Brian actually asked for: his message, or for "yes"/"keep going" his last real request."""
    if not is_continue_word(user_text) and len((user_text or "").strip()) >= SUBSTANTIAL:
        return user_text.strip()
    for m in reversed(history or []):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            text = m["content"].strip()
            if len(text) >= SUBSTANTIAL and not is_continue_word(text) and not text.startswith("["):
                return text
    return ""


def _same(a: str, b: str) -> bool:
    n = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()[:70]
    return bool(n(a)) and n(a) == n(b)


def queue_goal(objective: str, reason: str, agent_state=None):
    """Make sure the worker owns this job. Returns (goal, created); (None, False) if there's no objective."""
    if not objective:
        return None, False
    if agent_state is None:
        import agent_state
    for g in agent_state.list_goals("active") + agent_state.list_goals("pending"):
        if _same(g["objective"], objective):
            return g, False
    return agent_state.create_goal(objective[:600], reason=reason), True


def goal_context(agent_state=None) -> str:
    """One short block so "yes"/"keep going" has something to point at."""
    if agent_state is None:
        import agent_state
    g = agent_state.get_active_goal()
    if not g:
        return ""
    plan, step = g.get("plan") or [], g.get("current_step", 0)
    now = plan[step] if step < len(plan) else "(still being planned)"
    return ("[The job you are working on]\n"
            f"{g['objective'][:220]}\n"
            f"Status {g['status']}; step {min(step + 1, max(len(plan), 1))} of {len(plan) or '?'}: {now[:160]}\n"
            "Your background worker is carrying this out while Brian is quiet and will message him when a step "
            "is done or stuck. If he says 'yes' or 'keep going', tell him where it stands; don't start over.")
