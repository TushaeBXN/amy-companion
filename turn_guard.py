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


# Work she says is happening right now ("Here's what I'm doing", "I'm creating three accounts"). With no tool call
# behind it, nothing is happening. Kept to action verbs so "I'm doing well" or "I'm thinking" never match.
_DOING = re.compile(r"\b(?:here's what i'm doing|i'm (?:now |currently |already )?(?:running|creating|posting|sending|"
                    r"deploying|launching|pulling up|setting up))\b", re.I)


def _straight(text: str) -> str:
    """The model types curly apostrophes (I’ve, I’ll); the patterns here are written with straight ones."""
    return (text or "").replace("\u2019", "'").replace("\u2018", "'")


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
    text = _straight(reply)
    if not (_CLAIM.search(text) or _DOING.search(text)) or real_work(calls):
        return ""
    return ("\n\n(Check on my own reply: no tool I called this turn actually did that work. "
            + work_summary(calls).replace("What your tools actually did this turn:", "What really happened:") + ")")


# ── verify before reporting ──────────────────────────────────────────────────
# She reported upvotes and comments the tool had refused (2026-11-20), and wrote "All cases correct" over output
# that showed the code was wrong (2026-11-21). Before a report goes out, each claim is checked against what the
# tools returned; if one doesn't hold, she gets one pass to check it and rewrite before Brian sees it.

# Actions she reports in past tense, and the successful call each one needs: (tool, action arg or None).
_ACTION_CLAIMS = [
    (re.compile(r"\bupvot(?:ed|ing)\b", re.I), {("moltbook_engage", "upvote")}),
    (re.compile(r"\bdownvot(?:ed|ing)\b", re.I), {("moltbook_engage", "downvote")}),
    (re.compile(r"\b(?:commented|left a comment|replied to (?:a|the|his|her|their|an?) (?:post|thread|comment))\b", re.I),
     {("moltbook_engage", "comment")}),
    (re.compile(r"\bI(?:'ve| have)?\s+(?:just\s+|also\s+)?followed\b", re.I), {("moltbook_engage", "follow")}),
    (re.compile(r"\b(?:emailed|sent (?:the |an |that )?(?:e-?mail|reply|message to))\b", re.I), {("send_email", None)}),
    (re.compile(r"\bcommitted\b", re.I), {("git_commit", None)}),
]
# "It works / all correct / tests pass" said about code.
_SUCCESS_CLAIM = re.compile(r"\b(?:all (?:cases|tests|checks) (?:are )?(?:correct|pass(?:ed)?|green)|"
                            r"(?:it|everything|the (?:script|code|program|demo|test)s?) (?:works?|ran (?:fine|clean(?:ly)?)|"
                            r"passe[sd])|works (?:as expected|correctly|perfectly)|no errors|ran successfully|"
                            r"output (?:is|was|looks) (?:correct|right))\b", re.I)
_RUN_FAILED = re.compile(r"traceback \(most recent call last\)|\b\w*error:|timed out after|exited with non-zero|"
                         r"stderr: \S", re.I)


def _call_ok(c: dict) -> bool:
    return not failed(c.get("result_snippet")) and not _RUN_FAILED.search(str(c.get("result_snippet") or ""))


def _has_ok_call(calls: list, need: set) -> bool:
    for c in calls:
        args = c.get("args") if isinstance(c.get("args"), dict) else {}
        for tool, action in need:
            if c["name"] == tool and (action is None or str(args.get("action", "")).lower() == action) and _call_ok(c):
                return True
    return False


def claim_problems(reply: str, calls: list) -> list:
    """What the reply claims that her tool results don't back up. Empty when the report is consistent."""
    text = _straight(reply)
    problems = []
    for pat, need in _ACTION_CLAIMS:
        m = pat.search(text)
        if m and not _has_ok_call(calls, need):
            tool = sorted(need)[0]
            problems.append(f'You wrote "{m.group(0)}", but no {tool[0]}'
                            + (f" {tool[1]}" if tool[1] else "") + " call succeeded this turn.")
    m = _SUCCESS_CLAIM.search(text)
    if m:
        code_runs = [c for c in calls if c["name"] in ("run_code", "check_syntax")]
        bad = [c for c in code_runs if not _call_ok(c)]
        if bad and not _call_ok(code_runs[-1]):
            problems.append(f'You wrote "{m.group(0)}", but your last {code_runs[-1]["name"]} result shows an error.')
    if not problems and unbacked_claim_note(reply, calls):
        problems.append("You describe work as done, but no tool that did real work succeeded this turn.")
    return problems


def verify_prompt(reply: str, calls: list) -> str:
    """The check she gets before her report goes out, or '' when nothing in it contradicts her tool results."""
    problems = claim_problems(reply, calls)
    if not problems:
        return ""
    return ("[Before this goes to Brian, verify it. " + " ".join(problems) + "\n" + work_summary(calls) + "\n"
            "Check each claim in your reply against those results. If something didn't happen, either do it now "
            "with a real tool call, or rewrite your reply to say plainly what failed or wasn't done. If code output "
            "is wrong for what the code should do, say so. Don't claim anything the results don't show.]")


def success_spot_check(text: str, calls: list) -> str:
    """A ran-clean script can still be wrong ("All cases correct" over wrong numbers). When she calls code output
    correct, the check she gets once: work out the expected values and compare them line by line. '' if no claim."""
    m = _SUCCESS_CLAIM.search(_straight(text))
    if not m or not any(c["name"] == "run_code" for c in calls):
        return ""
    return (f'[Not saved yet. You wrote "{m.group(0)}". A script that runs without errors can still be wrong. '
            "Take your last run_code output, work out by hand what each line should be from what the code is meant "
            "to do, and compare. If any line is off, say which one and why (or fix the code and rerun). Then write "
            "this again with only what you checked.]")


# ── handing unfinished work to the goal system ───────────────────────────────
# The worker already plans a goal into steps, does one step per round, checks it, retries, and tells Brian when
# it's done or blocked. Chat never used it: she narrated in chat and asked "want me to keep going?".

_CONTINUE_WORD = re.compile(r"^\s*(?:yes|yeah|yep|ok(?:ay)?|sure|go(?: on| ahead)?|keep going|continue|do it|"
                            r"proceed|and don'?t stop|yes and don'?t stop|please do)\W*$", re.I)
_ASKS_TO_CONTINUE = re.compile(r"(?:want me to|should i|shall i|would you like me to|do you want me to)\s+"
                               r"(?:keep going|continue|move on|proceed|go on|carry on|start|begin|dive)|"
                               r"\bkeep going\?|"
                               # "What's the next test?" is chat; only work phrasing like "on to the next step?" counts
                               r"(?:on to|move (?:on )?to|continue (?:to|with)|do|take|start)\s+the next\b[^.?!]{0,60}\?\s*$", re.I)
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
    reply = _straight(reply).strip()
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
                             r"look into|go through|go research|summari[sz]e|analy[sz]e|compare|prepare|review|check|engage|reply|respond|comment|upvote|downvote|follow|post|go on|go to|get on)\b")
# A correction from Brian — he's updating her, not requesting a task.
_CORRECTION = re.compile(r"(?i)\b(correct yourself|you(?:'re| are) (?:wrong|biased|off)|that(?:'s| is) (?:incorrect|wrong|biased|not right|a lie)|"
                         r"fix your(?:self)?|that's not (?:right|true|accurate)|you have (?:a )?bias|you need to (?:look|search)|"
                         r"look it up|this is to correct)\b")
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


JOB_TOOLS = {"create_autonomous_goal", "task_push"}
NO_JOB_FOR_QUESTION = ("Not done: Brian asked a question, so nothing was queued. Answer it yourself now, in your own "
                       "words, with at most a search or two. If you think it deserves a background job, ask him first.")


def is_just_a_question(text: str) -> bool:
    """Brian's message is a plain question with no instruction in it, so it must not become a background job."""
    t = (text or "").strip()
    return bool(t) and _is_plain_question(t) and not _COMMITMENT_PHRASE.search(t)


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
    if _CORRECTION.search(t):
        return False
    # Only check the first 120 characters for weak task verbs — a verb buried in a long
    # informational paste ("a look at the data", "find the evidence below") is data the
    # user is sharing, not an instruction to run a background job.
    return bool(_WEAK_TASK_VERB.search(t[:120]))


def clarify_reply(text: str) -> str:
    return ("Quick check before I dive in: do you want me to track this as a goal — I'll work it step by step "
            "in the background and tell you when it's done or stuck — or are we just talking it through right now?")


def is_status_request(text: str) -> bool:
    t = (text or "").strip()
    return is_continue_word(t) or (len(t) < 90 and bool(_STATUS.search(t)))


_CASUAL_ASK = re.compile(r"(?i)\b(what are you (?:doing|working on)|how(?:'s| is) it going)\b")


def is_casual_ask(text: str) -> bool:
    """'What are you doing?' is small talk unless a job is actually running; it is not a request for a report
    on a goal that got stuck days ago."""
    return bool(_CASUAL_ASK.search(_straight(text))) and not re.search(r"(?i)\b(status|progress|update|stuck|blocked|goal|job)\b", text or "")


def goal_status(agent_state=None, live_only: bool = False) -> str:
    """Where the job really stands, from the goal record only. '' when there is no goal to report on.
    live_only leaves out blocked goals, so a casual ask only gets a report when something is running or queued."""
    if agent_state is None:
        import agent_state
    goals = agent_state.list_goals("active") + agent_state.list_goals("pending")
    if not live_only:
        goals += agent_state.list_goals("blocked")
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
        if "skipped: chat message pending" in str(last.get("observed", "")):
            # older records logged a chat interruption as a failed check; say what it really was
            lines.append(f"Last check (step {last.get('step', 0) + 1}): paused because you messaged me mid-step "
                         "(an interruption, not a failed attempt)")
        else:
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


_CLOSING_OFFER = re.compile(r"(?i)^(?:just |and )?(?:let me know|feel free|i'?m (?:here|happy|ready)|happy to|"
                            r"hope that helps|if you(?:'d| would) like|want me to)\b")


def trim_trailing_question(reply: str, user_text: str, calls: list) -> str:
    """Chat-only: drop a closing question or "let me know..." offer tacked onto a statement (the model's habit of
    ending every reply with one). Left alone when Brian asked something himself, gave a task, work was done, she's
    offering to continue, or nothing but the closer would be left."""
    r = (reply or "").rstrip()
    if not r or "```" in r or real_work(calls) or asks_to_continue(r) or is_task_request(user_text or ""):
        return reply
    parts = re.split(r"(?<=[.!?])\s+", r)
    if len(parts) < 2:
        return reply
    last = parts[-1]
    is_q = r.endswith("?") and not (user_text or "").rstrip().endswith("?")
    if not (is_q or _CLOSING_OFFER.match(last)):
        return reply
    kept = " ".join(parts[:-1]).rstrip()
    return kept if len(kept) >= 40 and not kept.endswith(":") else reply


_WAITS = re.compile(r"(?i)(?:i'?ll|i will|i'?m going to|i can|i'?ll just|i'?ll simply)\s+(?:just\s+)?wait\b|"
                    r"\bwait(?:ing)? (?:for you|on you|until you)\b|tell me what (?:to do|you(?:'d| would) like)(?: next)?|"
                    r"let me know (?:what|how|if|when|which)|\bnothing urgent\b|\blow[- ]priority noise\b|"
                    r"\bnothing (?:that )?(?:needs|requires) (?:my )?(?:attention|action|a reply)\b|"
                    r"what would you like me to do|your call\b|"
                    r"\bwhat (?:specific |exact )?(?:actions?|steps?|things?) (?:should|do you want|would you like) (?:i|me)\b|"
                    r"\bwhat should i (?:do|take)\b|\bhow (?:should|do you want) (?:i|me to) (?:proceed|start|begin)\b|"
                    r"\bwhere (?:should|do you want) (?:i|me to) (?:start|begin)\b")


def waits_for_brian(reply: str, user_text: str) -> bool:
    """He asked her to do something, she looked, and she stopped to wait for instructions instead of acting."""
    reply = _straight(reply).strip()
    if not reply or len(reply) > 1200 or not _WAITS.search(reply):
        return False
    t = (user_text or "").strip()
    return bool(t) and (is_task_request(t) or bool(_WEAK_TASK_VERB.search(t)) or is_continue_word(t))


def wait_nudge_text(user_text: str) -> str:
    return ("[Don't wait for Brian. He asked: " + (user_text or "").strip()[:200] + "\n"
            "You looked, then stopped to ask what to do. Take the next step yourself with a real tool call: answer or "
            "upvote what is worth it, post something of your own, follow up, or start the next useful thing. On "
            "Moltbook use moltbook_engage (comment, upvote, downvote, follow) and moltbook_post. Then tell him what you did.]")


# ── repeated paragraphs ──────────────────────────────────────────────────────────
# 2026-11-24: a 1,200-token reply repeated the same four paragraphs over and over ("I'm still here, thinking out
# loud." / "A person is a grandmother who counts chickens..."), copied from her previous reply, until the token cap
# cut it off mid-sentence. The model's sampling settings now discourage that; this is the net under them.

def _para_key(p: str) -> str:
    return re.sub(r"\s+", " ", p).strip().lower()


def drop_repeats(reply: str, previous: str = "") -> str:
    """Remove paragraphs that repeat an earlier one in this reply or in her previous reply, and a cut-off
    tail that is only the start of an earlier paragraph. Short lines (under 25 characters) are left alone,
    and the reply is returned unchanged if nothing would be left."""
    if not reply or "```" in reply:
        return reply
    paras = re.split(r"\n\s*\n", reply.strip())
    seen = {_para_key(p) for p in re.split(r"\n\s*\n", previous or "") if len(_para_key(p)) >= 25}
    kept: list[str] = []
    for i, p in enumerate(paras):
        k = _para_key(p)
        if len(k) >= 25 and k in seen:
            continue
        last = i == len(paras) - 1
        if last and len(k) >= 10 and any(s.startswith(k) and s != k for s in seen):
            continue  # "So when I say I" — the token cap cut a repeat off partway
        if len(k) >= 25:
            seen.add(k)
        kept.append(p.strip())
    out = "\n\n".join(kept)
    return out if out.strip() else reply


# ── saying the same thing again ──────────────────────────────────────────────────
# 2026-11-24: in one conversation every reply rebuilt the same lines from her earlier replies ("I'm still here,
# thinking out loud", "ten million years of human history..."). Brian said "Stop repeating the stuff" twice; she
# agreed and repeated them again. drop_repeats() only removes exact copies, and the sampling penalty only sees the
# reply being written, not the earlier ones she rereads, so a reply that is mostly a rerun gets one rewrite.

_STOP_REPEATING = re.compile(r"(?i)\b(?:stop|quit|keep|you'?re|you are|you keep)\b.{0,20}\b(?:repeat|repeating|circl|"
                             r"circulat|saying the same|same (?:thing|stuff|lines|answer))|\bsay something new\b|"
                             r"\bfresh (?:perspective|ideas?|take)\b")
_WORD = re.compile(r"[a-z0-9']+")


def asks_to_stop_repeating(user_text: str) -> bool:
    return bool(_STOP_REPEATING.search(user_text or ""))


def _shingles(text: str, n: int = 5) -> set:
    w = _WORD.findall((text or "").lower())
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def reused_sentences(draft: str, previous: list) -> list:
    """Sentences of *draft* (30+ characters) that are mostly made of 5-word runs from her *previous* replies."""
    seen = set().union(*(_shingles(p) for p in previous if p)) if previous else set()
    if not seen:
        return []
    out = []
    for s in re.split(r"(?<=[.!?])\s+|\n+", draft or ""):
        s = s.strip()
        sh = _shingles(s)
        if len(s) >= 30 and sh and len(sh & seen) / len(sh) >= 0.6:
            out.append(s)
    return out


def fresh_angle_prompt(draft: str, previous: list, user_text: str) -> str:
    """A rewrite request when the draft mostly repeats her recent replies ('' when it doesn't).

    Triggers at 35% of the draft reused, or 15% when Brian has just asked her to stop repeating."""
    reused = reused_sentences(draft, previous)
    total = len((draft or "").strip())
    if not reused or not total:
        return ""
    share = sum(len(s) for s in reused) / total
    if share < (0.15 if asks_to_stop_repeating(user_text) else 0.35):
        return ""
    lines = "\n".join(f'- "{s[:160]}"' for s in reused[:5])
    return ("[Before this goes out: most of this draft repeats what you already told Brian in your last few replies:\n"
            f"{lines}\n"
            "Don't use those lines or close variants of them again. Answer what he actually said in his last message, "
            "and bring something he hasn't heard from you: a concrete example, a fact from something you've read, a "
            "different angle, or a real question back. Saying you'll stop repeating is not new. Keep it short.]")
