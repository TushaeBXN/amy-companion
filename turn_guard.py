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


def wrapup_prompt(calls: list, goal_id: str = "") -> str:
    """What she is told when a turn can't finish in chat. Built from what her tools actually returned."""
    done = real_work(calls)
    handed = (f"The rest of this job has been handed to your background worker as goal {goal_id}: it plans the steps "
              "and does them one at a time, checks each, and messages Brian when a step is done or stuck. Say that. "
              if goal_id else "")
    return (f"[This message ran out of tool steps. {work_summary(calls)}\n{handed}"
            "Don't call any more tools. In a few sentences tell Brian only what is true from that list: what you "
            + ("got done, " if done else "") + "what failed and why, and the next step."
            + ("" if done else " You have not done the task yet, so say so plainly.")
            + " Do not describe anything you did not do. Do not ask whether to continue.]")


# Options of the memory tool that she sometimes calls as if they were tools of their own.
ENGRAM_ACTIONS = {"diary_read", "diary_write", "view_get", "view_record", "kg_add", "kg_query"}


def tool_names(tools_module) -> set:
    return {t["function"]["name"] for t in tools_module.TOOLS}


def not_a_tool(name: str, declared: set) -> str:
    """The answer to a call to something that isn't a tool (she turns menu options like wake_up into tool names)."""
    pick = ", ".join(sorted(n for n in declared if n in ("books", "study", "anchor", "run_code", "consult", "read_pdf",
                                                         "browse", "write_file", "read_file", "create_autonomous_goal")))
    return (f"[{name} is not a tool, so nothing was run. Your memory and identity are already loaded. "
            f"Do the task with a real tool, for example: {pick}. Or answer.]")


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


# ── announcing instead of doing ──────────────────────────────────────────────
# "My first instinct is to actually open that book and read through it" and then the turn just ended.

_ANNOUNCE = re.compile(r"(?i)\b(let me|i'?ll|i will|i'?m going to|i am going to|i'?m about to|i'?m ready to|next,? i|"
                       r"my (?:first|next) (?:instinct|step|move) is|i plan to|i want to|i should|first,? i(?:'ll| will| need))\b")


def announced_not_done(reply: str, user_text: str) -> bool:
    """She said what she would do next, on a real task, and stopped. (A question back to Brian is not a stall.)"""
    reply = (reply or "").strip()
    if not reply or "?" in reply or len(reply) > 900:
        return False
    task = is_continue_word(user_text) or len((user_text or "").strip()) >= SUBSTANTIAL
    return task and bool(_ANNOUNCE.search(reply))


def nudge_text(user_text: str, history: list) -> str:
    job = objective_for(user_text, history) or (user_text or "").strip()
    return ("[You said what you would do but didn't do it. Brian's request: " + job[:260] + "\n"
            "Do the next step now with a real tool call, not text. To read a book: books action=open title=<title>, "
            "then books action=read. To try something on this Mac: run_code. Then keep going until the task is done.]")


# ── task mode: outcomes decide, not her wording ──────────────────────────────
# A real instruction from Brian becomes a goal at once. The worker plans it, does one step at a time, and checks each
# step against what tools did and which files changed. Chat reports that record; she doesn't narrate her own progress.

# Strong verbs commit to a real, multi-step job on her own machine (build it, install it, reproduce it).
# A wrong "yes" here is not cheap any more: it skips the chat reply entirely. Weak verbs ("check", "look into",
# "read", "try") show up constantly in ordinary conversation too ("check out this site, what do you think"),
# so a weak verb alone is not enough to commit to a goal — see needs_clarifying_ask below.
_STRONG_TASK_VERB = re.compile(r"(?i)\b(build(?:s|ing|ed)?|creat(?:e|es|ed|ing)|install(?:s|ing|ed)?|set ?up|"
                               r"download(?:s|ing|ed)?|debug(?:s|ging|ged)?|fix(?:es|ing|ed)?|implement(?:s|ing|ed)?|"
                               r"reproduc(?:e|es|ed|ing)|put together|organi[sz](?:e|es|ed|ing)|"
                               r"writ(?:e|es|ing) (?:a|the|me|up) (?:file|script|guide|summary|report|notes))\b")
# Explicit multi-step or report-back commitment, even without a build/fix-style verb — "I want you to learn it
# properly... try them on your machine and tell me what worked" is clearly a real job, not small talk.
_COMMITMENT_PHRASE = re.compile(r"(?i)\b(step by step|break (?:it|this|them) (?:down\b|into )|one (?:step|thing) at a time|"
                                r"tell me what worked|what worked and what didn'?t|"
                                r"try (?:it|them|the steps)[^.?!]{0,25}\bmachine\b|"
                                r"i want you to (?:learn|try|build|figure|do|go|read|study)|"
                                r"try (?:it|them|this)(?: yourself)? on your (?:own )?machine)\b")
_WEAK_TASK_VERB = re.compile(r"(?i)\b(read|open|research|find|search|learn|study|try|run|figure out|work out|"
                             r"look into|go through|go research|summari[sz]e|analy[sz]e|compare|prepare|review|check)\b")
# Asking for her take, not a job to run: "tell me what you think" etc. This matters anywhere in the message,
# not just at the end — a trailing URL or afterthought after the "?" must not defeat it.
_OPINION_ASK = re.compile(r"(?i)\b(what(?:'s| is| do| did) your (?:take|opinion|view|thought)|your thoughts?\b|"
                          r"what (?:do|did) you (?:think|make of)|curious what you (?:think|make of)|"
                          r"how (?:does|did) (?:that|this|it) (?:sound|look|feel)|"
                          r"what'?s your take)\b")
_CHAT_ONLY = re.compile(r"(?i)^\s*(?:chat|just chat|just talk|no tools)\b")
_STATUS = re.compile(r"(?i)\b(status|progress|where (?:are|is) (?:you|it|that)|how(?:'s| is) it going|what are you "
                     r"(?:doing|working on)|any update|update me|did you finish|are you (?:done|finished))\b")


def _is_plain_question(text: str) -> bool:
    first = re.split(r"(?<=[.!])\s", text, maxsplit=1)[0]
    return bool(first.rstrip().endswith("?") and not re.search(r"(?i)\b(please|can you|could you|i want you to|go )", first))


def is_task_request(text: str) -> bool:
    """A real instruction committing to a job, not conversation. Only strong, unambiguous verbs qualify —
    a weak one goes to needs_clarifying_ask instead of a silent guess."""
    t = (text or "").strip()
    if len(t) < 80 or _CHAT_ONLY.match(t) or is_continue_word(t) or _OPINION_ASK.search(t):
        return False
    if _is_plain_question(t):
        return False
    return bool(_STRONG_TASK_VERB.search(t) or _COMMITMENT_PHRASE.search(t))


def needs_clarifying_ask(text: str) -> bool:
    """A message long enough and action-flavored enough that it *might* be a job, but not clearly one —
    ask plainly rather than silently deciding either way."""
    t = (text or "").strip()
    if is_task_request(t) or len(t) < 80 or _CHAT_ONLY.match(t) or is_continue_word(t) or _OPINION_ASK.search(t):
        return False
    if _is_plain_question(t):
        return False
    return bool(_WEAK_TASK_VERB.search(t))


def clarify_reply(text: str) -> str:
    return ("Quick check before I dive in: do you want me to track this as a goal — I'll work it step by step "
            "in the background and tell you when it's done or stuck — or are we just talking it through right now?")


def is_status_request(text: str) -> bool:
    t = (text or "").strip()
    return is_continue_word(t) or (len(t) < 90 and bool(_STATUS.search(t)))


def goal_status(agent_state=None) -> str:
    """Where the job really stands, from the goal record only. '' when there is no goal to report on."""
    if agent_state is None:
        import agent_state
    goals = agent_state.list_goals("active") + agent_state.list_goals("pending") + agent_state.list_goals("blocked")
    goals = sorted(goals, key=lambda g: g.get("updated_at", ""), reverse=True)
    if not goals:
        return ""
    g = goals[0]
    plan, step = g.get("plan") or [], g.get("current_step", 0)
    lines = [f"Job: {g['objective'][:200]}", f"Status: {g['status']}"]
    if not plan:
        lines.append("The plan is still being written; no step has been done yet.")
    else:
        done = min(step, len(plan))
        lines.append(f"Steps verified so far: {done} of {len(plan)}")
        lines += [f"  {'done' if i < done else 'next' if i == done else 'later'}: {s[:110]}" for i, s in enumerate(plan[:8])]
    ev = g.get("evidence") or []
    if ev:
        last = ev[-1]
        lines.append(f"Last check (step {last.get('step', 0) + 1}): {str(last.get('decision', '?')).upper()} - "
                     f"{str(last.get('observed', ''))[:160]}")
    if g["status"] == "blocked":
        lines.append(f"Stuck: {str(g.get('next_action') or 'see the last check')[:200]}")
    return "\n".join(lines)


def task_reply(goal: dict, created: bool) -> str:
    head = ("On it. I've made this a goal and my worker will plan it and do it one step at a time."
            if created else "I'm already on this, and it's running in the background.")
    return (f"{head}\n\nJob: {goal['objective'][:220]}\nGoal: {goal['goal_id']}\n\n"
            "Each step is checked against what my tools actually did before it counts, and I'll message you when a "
            "step is verified or when I'm stuck. Say \"status\" any time for where it stands.")


# ── proof that a step was done ───────────────────────────────────────────────

_ACTION_STEP = re.compile(r"(?i)\b(read|open|search|find|look up|run|try|test|reproduce|install|download|fetch|browse|"
                          r"create|write|save|build|check|list|scan|execute|measure|compare|call|query)\b")
_THINK_STEP = re.compile(r"(?i)^\s*(decide|think|reflect|plan|summari[sz]e|conclude|explain|report|tell|answer)\b")


def step_needs_tools(step_desc: str) -> bool:
    return bool(_ACTION_STEP.search(step_desc or "")) and not _THINK_STEP.match(step_desc or "")


def tool_proof_problem(step_desc: str, calls: list) -> str:
    """Why a step cannot count as done, or ''. Describing work is not doing it: a tool has to have worked."""
    if not step_needs_tools(step_desc) or real_work(calls):
        return ""
    return ("no tool did any work in this step (" + (", ".join(f"{c['name']}: {'failed' if failed(c.get('result_snippet')) else 'context only'}"
                                                          for c in calls[:4]) or "no tool calls") + "); describing work does not count")


def result_from_tools(final: str, calls: list, limit: int = 1500) -> str:
    """A step's result: her closing account, or if she ended without one, what her tools actually returned.
    (A tool that worked followed by no closing sentence was being scored "no output" and failed.)"""
    if (final or "").strip() and final.strip() != "(no output)":
        return final
    done = real_work(calls)
    if not done:
        return final or "(no output)"
    lines = [f"{c['name']}: {str(c.get('result_snippet') or '')[:300]}" for c in done]
    return ("(no closing summary; these are the results her tools returned)\n" + "\n".join(lines))[:limit]


# ── what Brian is told while a goal runs ─────────────────────────────────────

_CHECKED_BY = {"deterministic_pass": "a hard check (a tool worked and/or a file was written in this step)",
               "model_pass": "a judgment by the model, shown the real tool results"}


def checklist(plan: list, done: int) -> str:
    return "\n".join(f"  {'done' if i < done else 'next' if i == done else 'later'}: {i + 1}. {s[:120]}"
                     for i, s in enumerate(plan))


def plan_message(objective: str, plan: list) -> str:
    return (f"Here's my plan for: {objective[:140]}\n\n{checklist(plan, 0)}\n\n"
            "I'll do these one at a time and tell you when each is verified (or when I'm stuck). "
            "Say \"status\" any time.")


def step_message(step_idx: int, plan: list, result: str, calls: list, decision: str) -> str:
    """A verified step, with the evidence: which tools worked, how it was checked, what she found."""
    tools = ", ".join(sorted({c["name"] for c in real_work(calls)})) or "none"
    found = re.sub(r"\s+", " ", result or "").strip()[:320]
    nxt = (f"Next: {plan[step_idx + 1][:120]}" if step_idx + 1 < len(plan) else "That was the last step.")
    return (f"Step {step_idx + 1} of {len(plan)} done and verified: {plan[step_idx][:140]}\n"
            f"Tools that worked: {tools}\nChecked by: {_CHECKED_BY.get(decision, decision)}\n"
            f"What she reported: {found}\n\n{checklist(plan, step_idx + 1)}\n\n{nxt}")
