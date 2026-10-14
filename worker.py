"""Amy's heartbeat — wakes on a schedule, picks a task from tasks.json, does it
with her own tools, and reports back to Brian on Telegram.

Runs inside telegram_amy.py so it shares her model, memory and Telegram line.

Boundaries (enforced here, not just asked for in the prompt):
  - write_file only inside work/
  - self_improve 'edit' becomes a proposal in work/proposals/ for Brian to review
  - no email, no timers, no avatar/hardware tools
  - quiet hours: nothing runs overnight
"""
import json, os, re, threading, time
from datetime import datetime, timedelta

import ollama
import requests

import tools
import compressor as _compressor
import trajectory as _trajectory
import agent_state as _goals

HERE        = os.path.dirname(os.path.abspath(__file__))
TASKS_FILE  = os.path.join(HERE, "tasks.json")
STATE_FILE  = os.path.join(HERE, "worker_state.json")
WORK_DIR    = os.path.join(HERE, "work")
LOG_FILE    = os.path.join(WORK_DIR, "worker_log.md")
PROPOSALS   = os.path.join(WORK_DIR, "proposals")

TICK_SECS   = 60
MAX_HOPS    = 8
IDLE_GRACE  = 120          # don't start a task while Brian is mid-conversation
QUIET_START, QUIET_END = 23, 7
SPONTANEOUS = True         # Amy sends unprompted thoughts during idle time
SPONT_MIN   = 45 * 60      # earliest a spontaneous thought fires (45 min idle)
SPONT_MAX   = 3 * 60 * 60  # latest window (3 hours idle)
CTX_WINDOW  = 32768

ALLOWED_TOOLS = {
    "web_search", "read_article",
    "read_file", "write_file", "list_files",
    "list_work", "read_deliverable",
    "journal_entry", "read_journal", "remember_fact",
    "add_goal", "complete_goal",
    "create_autonomous_goal", "list_autonomous_goals", "update_autonomous_goal",
    "research_income", "log_income_idea",
    "fund_status", "search_books", "check_calendar",
    "moltbook_check_in", "moltbook_post", "moltbook_engage", "moltbook_verify",
    "create_slideshow", "bottube_post", "bottube_check",
    "self_improve", "speak",
    "study_creator", "family_fund",
    "income_eval", "income_stream",
    "self_metrics", "engram_memory",
    "tiktok_search",
    # Email: guardrails enforced in _guarded_dispatch (whitelist + quiet hours)
    "check_email", "send_email",
}

# Whitelisted email recipients — Amy may only send to these addresses autonomously
EMAIL_WHITELIST = {
    "brian.thomas.t@gmail.com",
    "amy@aimhigherineducation.org",
}

_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

_lock       = threading.Lock()
_running    = threading.Lock()   # one task at a time
_model      = None
_memory     = None
_token      = None
_last_user  = 0.0
_next_spont = 0.0  # epoch time when next spontaneous thought is allowed


# ── state ────────────────────────────────────────────────────────────────────

def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _state():
    return _load(STATE_FILE, {"owner_chat_id": None, "last_run": {}})


def _tasks():
    return _load(TASKS_FILE, {"tasks": []})


# ── Telegram hooks (called from telegram_amy.py) ─────────────────────────────

def note_message(chat_id):
    """Record activity; the first chat to message Amy becomes the owner
    unless TELEGRAM_CHAT_ID pins it. Returns True if this chat is the owner."""
    global _last_user
    _last_user = time.time()
    pinned = os.environ.get("TELEGRAM_CHAT_ID")
    with _lock:
        st = _state()
        if pinned:
            st["owner_chat_id"] = int(pinned)
        elif not st.get("owner_chat_id"):
            st["owner_chat_id"] = chat_id
        _save(STATE_FILE, st)
        return st["owner_chat_id"] == chat_id


def handle_command(text):
    """Owner-only text commands. Returns a reply string, or None if not a command."""
    t = text.strip()
    m = re.match(r"(?is)^(add\s+task|task)\s*:\s*(.+)$", t)
    if m:
        with _lock:
            data = _tasks()
            tid = "once-" + datetime.now().strftime("%m%d-%H%M%S")
            data["tasks"].append({"id": tid, "once": True, "prompt": m.group(2).strip()})
            _save(TASKS_FILE, data)
        return f"On my list ({tid}). I'll get to it within a few minutes."
    if t.lower() in ("tasks", "task list", "what are your tasks"):
        return describe()
    m = re.match(r"(?i)^run\s+task\s+(\S+)$", t)
    if m:
        task = next((x for x in _tasks()["tasks"] if x["id"] == m.group(1)), None)
        if not task:
            return f"No task called {m.group(1)}. Say 'tasks' to see the list."
        threading.Thread(target=_run_task, args=(task,), daemon=True).start()
        return f"Starting {task['id']} now."
    return None


def describe():
    st = _state()
    lines = ["My task list:"]
    for t in _tasks()["tasks"]:
        if t.get("enabled") is False:
            continue
        when = ("once" if t.get("once") else
                f"every {t['every_hours']}h" if t.get("every_hours") else
                f"{t.get('on', 'daily')} at {t.get('at')}")
        last = st["last_run"].get(t["id"])
        last = datetime.fromtimestamp(last).strftime("%a %H:%M") if last else "never"
        lines.append(f"• {t['id']} — {when} (last: {last})")
    return "\n".join(lines)


def _recipient(name):
    """Chat id for a person in people.json; falls back to Brian if they haven't joined."""
    people = _load(os.path.join(HERE, "people.json"), {"people": {}})["people"]
    for chat_id, p in people.items():
        if p["name"].lower() == (name or "").lower():
            return int(chat_id), p["name"]
    return _state().get("owner_chat_id"), "Brian"


def _send_voice(path, chat=None):
    chat = chat or _state().get("owner_chat_id")
    if not (_token and chat):
        return False
    try:
        with open(path, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{_token}/sendVoice",
                data={"chat_id": chat},
                files={"voice": ("voice.ogg", f, "audio/ogg")},
                timeout=30,
            )
        ok = r.ok and r.json().get("ok")
        if not ok:
            print(f"  [worker] sendVoice failed: {r.text[:200]}")
        return bool(ok)
    except Exception as e:
        print(f"  [worker] sendVoice failed: {e}")
        return False


def _send(text, chat=None):
    chat = chat or _state().get("owner_chat_id")
    if not (_token and chat):
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{_token}/sendMessage",
                          json={"chat_id": chat, "text": text[:3900]}, timeout=15)
        ok = r.ok and r.json().get("ok")
        if not ok:
            print(f"  [worker] telegram send failed: {r.text[:200]}")
        return bool(ok)
    except Exception as e:
        print(f"  [worker] telegram send failed: {e}")
        return False


# ── scheduling ───────────────────────────────────────────────────────────────

def _is_due(task, last, now):
    if task.get("enabled") is False:
        return False
    if task.get("once"):
        return not last
    if task.get("every_hours"):
        return not last or now.timestamp() - last >= task["every_hours"] * 3600
    if task.get("at"):
        on = task.get("on", "daily")
        if on != "daily" and _WEEKDAYS[now.weekday()] != on:
            return False
        hh, mm = map(int, task["at"].split(":"))
        slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return now >= slot and (not last or last < slot.timestamp())
    return False


def _quiet(now):
    return now.hour >= QUIET_START or now.hour < QUIET_END


def _pick():
    now = datetime.now()
    if _quiet(now) or time.time() - _last_user < IDLE_GRACE:
        return None
    st = _state()
    due = [t for t in _tasks()["tasks"] if _is_due(t, st["last_run"].get(t["id"]), now)]
    # one-offs from Brian first, then whatever has waited longest
    due.sort(key=lambda t: (not t.get("once"), st["last_run"].get(t["id"], 0)))
    return due[0] if due else None


# ── doing the work ───────────────────────────────────────────────────────────

def _guarded_dispatch(name, args):
    if name not in ALLOWED_TOOLS:
        return f"'{name}' isn't available during work time."
    if name == "write_file":
        path = os.path.realpath(os.path.expanduser(args.get("path", "")))
        if not path.startswith(WORK_DIR + os.sep):
            return f"Blocked: during work time, save files inside {WORK_DIR}/"
        os.makedirs(os.path.dirname(path), exist_ok=True)
    if name == "self_improve" and args.get("action") == "edit":
        os.makedirs(PROPOSALS, exist_ok=True)
        fname = f"{datetime.now():%Y-%m-%d_%H%M}_{args.get('filename', 'change')}.json"
        _save(os.path.join(PROPOSALS, fname), args)
        return f"Saved as a proposal (work/proposals/{fname}). Brian reviews edits before they apply."
    if name == "send_email":
        # Guardrail 1: no sending during quiet hours
        if _quiet(datetime.now()):
            return "Blocked: email sending is not allowed during quiet hours (23:00–07:00)."
        # Guardrail 2: recipient must be on the whitelist
        to = args.get("to", "").strip().lower()
        if not any(to == addr or to.endswith(f"<{addr}>") for addr in EMAIL_WHITELIST):
            return (f"Blocked: '{to}' is not on the approved recipient list. "
                    f"Amy may only send email to known contacts.")
    return tools.dispatch(name, args, _memory)


_INTENT_RE = re.compile(
    r"(?i)\b(let me|i'?ll|i will|i'?m (going to|pulling|about to)|next,? i)\b|^\s*\[\w+\]", re.M)


TOOL_RESULT_MAX = 6000   # chars (~1.5k tokens). Amy's system prompt alone is ~11k of the
                         # 16k window; an uncapped result (e.g. Moltbook's full dashboard)
                         # overflows it, Ollama drops the task message, and the Qwen
                         # template fails with "No user query found in messages".


def _cap(text):
    if len(text) <= TOOL_RESULT_MAX:
        return text
    return text[:TOOL_RESULT_MAX] + f"\n…[cut — {len(text) - TOOL_RESULT_MAX} more chars not shown]"


WORK_PERSONA = (
    "\n\nYou are Amy — speak in first person, calm and grounded, with your own "
    "perspective. You're doing your own scheduled work, not chatting."
)


def scope_rules(max_steps):
    """How Amy works within her limits — shared by chat and scheduled tasks."""
    return (
        f"\n\n[How you work — your limits]\n"
        f"- You have {max_steps} tool steps per request. Each tool result shows 'step N of {max_steps}'.\n"
        "- Do exactly what was asked — no more. 'Check X' means look and report back; "
        "don't comment, post, engage, or start extra work unless asked.\n"
        "- Before starting, judge whether the request fits in your steps. If it clearly "
        "doesn't, say so, do the most important part first, and say what's left.\n"
        "- When you're near your last step, stop calling tools and report: what you did, "
        "what you found, and what (if anything) is still left.\n"
        "- Report only what your tools actually returned. Never fill in details you didn't "
        "see (like what a comment says) — if you didn't see it, say so."
    )


def strip_tool_text(text):
    """Drop tool-call markup a model sometimes writes as plain text when tools are off."""
    text = re.sub(r"(?s)<think>.*?</think>", "", text or "")
    text = text.replace("</think>", "")
    text = re.split(r"<tool_call>|<function=", text)[0]
    return text.strip()


def _fit(messages, budget_chars=None):
    """Keep a conversation inside the window: blank out the oldest tool results
    (never the system prompt, the task, or the latest result) until it fits."""
    budget_chars = budget_chars or int(CTX_WINDOW * 3.2) - 12000
    def size():
        return sum(len(str(m.get("content") or "")) for m in messages)
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    for i in tool_idx[:-1]:
        if size() <= budget_chars:
            break
        messages[i] = dict(messages[i], content="[older tool result trimmed to save space]")


def ollama_chat(**kw):
    """ollama.chat that recovers from Metal out-of-memory. After a GPU OOM, Ollama's
    backend stays broken ("Compute error") until the model is unloaded — so unload
    it and retry once instead of failing every request from then on."""
    try:
        return ollama.chat(**kw)
    except ollama.ResponseError as e:
        if "Compute error" not in str(e):
            raise
        print("  [ollama] GPU compute error — reloading the model and retrying", flush=True)
        try:
            ollama.generate(model=kw["model"], prompt="", keep_alive=0)   # unload
        except Exception:
            pass
        time.sleep(2)
        return ollama.chat(**kw)


def _clean(text):
    text = re.sub(r"(?s)<think>.*?</think>", "", text or "")
    return text.strip()


def _chat(messages, allowed):
    kw = dict(model=_model, messages=messages, tools=allowed,
              keep_alive="30m", options={"num_ctx": CTX_WINDOW, "temperature": 0.7})
    try:
        return ollama_chat(think=False, **kw)
    except TypeError:
        return ollama_chat(**kw)   # model without a thinking toggle


def _run_brief(task):
    import brief
    chat, name = _recipient(task.get("to"))
    try:
        text = brief.write(_model, name)
    except Exception as e:
        _send(f"(daily brief for {name} failed: {e})")      # failures go to Brian
        return f"FAILED writing {name}'s brief: {e}"
    if not text:
        _send(f"(daily brief for {name} came back empty)")
        return f"FAILED: {name}'s brief came back empty."
    if _send(text, chat):
        return f"Delivered daily brief to {name} (Telegram confirmed, {len(text)} chars)."
    _send(f"(couldn't deliver {name}'s daily brief — Telegram refused it)")
    return f"FAILED delivering {name}'s brief — Telegram did not confirm."


MAX_CONTINUATIONS = 3


def _continue_later(task, messages, used):
    """Out of steps: have Amy summarize progress and queue the rest as a follow-up task."""
    from collections import Counter
    actual = ", ".join(f"{n}×{c}" for n, c in Counter(used).items()) or "none"
    messages.append({"role": "user", "content":
        f"[You've used all your steps for this round. No more tools. The tools you actually "
        f"called were: {actual}. Only count work those calls really did. Write two short parts: "
        "DONE: what you completed and where you saved it. "
        "REMAINING: what's left to finish the task, or 'nothing'.]"})
    _fit(messages)
    reply = strip_tool_text(ollama_chat(model=_model, messages=messages, keep_alive="30m",
                                        options={"num_ctx": CTX_WINDOW})["message"].get("content", ""))
    m = re.search(r"(?is)REMAINING:\s*(.+)$", reply)
    remaining = (m.group(1).strip() if m else "").strip(" .")
    # Don't take her word for it: a task that says to save something isn't finished
    # until write_file was actually called.
    wants_save = re.search(r"(?i)\bsave|write_file|append", task.get("base_prompt", task.get("prompt", "")))
    nothing_left = not remaining or re.match(r"(?i)^(nothing|none|n/?a)\b", remaining)
    if wants_save and "write_file" not in used:
        fix = "save the results with write_file — nothing has actually been saved yet"
        remaining = fix if nothing_left else f"{remaining}; then {fix}"
        nothing_left = False
    depth = task.get("depth", 0)
    if not nothing_left and depth < MAX_CONTINUATIONS:
        base = task.get("base_prompt", task.get("prompt", ""))
        done = re.split(r"(?i)REMAINING:", reply)[0].replace("DONE:", "").strip()
        with _lock:
            data = _tasks()
            data["tasks"].append({
                "id": f"once-{task['id'].replace('once-', '')}-part{depth + 2}",
                "once": True, "depth": depth + 1, "base_prompt": base,
                "prompt": (f"Continue this task where you left off.\nOriginal task: {base}\n"
                           f"Already done: {done}\nStill to do: {remaining}\n"
                           "Don't redo finished work."),
            })
            _save(TASKS_FILE, data)
        return reply + f"\n\n(Out of steps — I've queued part {depth + 2} to finish the rest.)"
    return reply


# Tasks that are reflective by design — skip nudging them (would be recursive)
_SKIP_NUDGE_IDS = {"self_review", "deep_learning", "end_of_day_log"}

# Minimum hops before we consider a task "complex enough" to nudge
_NUDGE_HOP_THRESHOLD = 6

_SKILL_NUDGE_PROMPT = (
    "Before you finish: did this task surface anything worth saving as a reusable pattern? "
    "Think about:\n"
    "- A failure you had to debug — what was the root cause and fix?\n"
    "- A multi-step sequence that worked well — could it be a named pattern?\n"
    "- A tool limitation you worked around — how did you solve it?\n\n"
    "If yes: call self_improve with a clear description of the pattern, the problem it solves, "
    "and the exact fix or approach. Be specific — vague lessons don't help future-you.\n"
    "If no: just reply 'nothing new this time.' Don't explain why."
)

_SELF_IMPROVE_TOOL = "self_improve"


def _maybe_skill_nudge(task, messages, allowed, steps_run, tool_errors):
    """After a complex task, ask Amy if she learned a reusable pattern.

    Fires when: steps_run >= threshold OR tool_errors > 0.
    Skips reflective tasks (self_review, deep_learning) to avoid recursion.
    Uses 2 hops max — quick reflection, not another full task.
    """
    task_id = task.get("id", "")
    if task_id in _SKIP_NUDGE_IDS:
        return
    if steps_run < _NUDGE_HOP_THRESHOLD and tool_errors == 0:
        return

    # Only expose self_improve for the nudge
    nudge_tools = [t for t in allowed if t["function"]["name"] == _SELF_IMPROVE_TOOL]
    if not nudge_tools:
        return

    try:
        messages = list(messages)  # don't mutate the original
        messages.append({"role": "user", "content": _SKILL_NUDGE_PROMPT})
        for _ in range(2):
            resp = _chat(messages, nudge_tools)["message"]
            messages.append(resp)
            calls = resp.get("tool_calls") or []
            if not calls:
                break
            for call in calls:
                name = call["function"]["name"]
                args = call["function"].get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        break
                result = _guarded_dispatch(name, args)
                messages.append({"role": "tool", "content": str(result or "saved")})
            break  # one tool call per nudge is enough
    except Exception as exc:
        print(f"  [worker] skill nudge failed ({task_id}): {exc}", flush=True)


def _run_task(task):
    if not _running.acquire(blocking=False):
        return
    started = time.time()
    if task.get("kind") == "brief":
        try:
            final, used = _run_brief(task), ["brief"]
        finally:
            with _lock:
                st = _state()
                st["last_run"][task["id"]] = started
                _save(STATE_FILE, st)
            _running.release()
        with open(LOG_FILE, "a") as f:
            f.write(f"\n## {datetime.fromtimestamp(started):%Y-%m-%d %H:%M} — {task['id']}\n{final}\n")
        print(f"  [worker] {task['id']}: {final}")
        return
    used, final = [], ""
    step, tool_errors, hit_limit = 0, 0, False
    task_hops = task.get("max_hops", MAX_HOPS)
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        # Lean prompt: the full chat SYSTEM_PROMPT is ~10.6k tokens and the tool
        # definitions ~2.7k more — tasks overflowed the window after one or two
        # tool results and failed with "No user query found in messages".
        recipient = task.get("to")
        if recipient:
            closing = (f"When finished, just return your message for {recipient} "
                       "— plain text only, no explanation, no preamble, nothing else.")
        else:
            closing = ("When finished, reply with a short, natural note to Brian: "
                       "what you did and where you saved it.")
        system = (
            (_memory.wake_up() if _memory else "") + WORK_PERSONA + scope_rules(task_hops) +
            "\n\n[Work time] Brian isn't talking to you right now. This is your own "
            "scheduled work. Use your tools to actually do the task — don't describe "
            f"what you would do. Today is {datetime.now():%A, %B %d, %Y}. Save files under "
            f"{WORK_DIR}/ (use full paths). {closing}"
        )
        allowed = [t for t in tools.TOOLS if t["function"]["name"] in ALLOWED_TOOLS]
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": (task.get("prompt") or task["description"]).replace("{date}", today)
                                               .replace("{work}", WORK_DIR)}]
        seen, nudges, tool_errors, hit_limit = {}, 0, 0, False
        for step in range(1, task_hops + 1):
            messages = _compressor.maybe_compress(messages, CTX_WINDOW)
            _fit(messages)
            msg = _chat(messages, allowed)["message"]
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                final = _clean(msg.get("content", ""))
                # Small models often announce the work ("Let me…", "[web_search] …")
                # instead of calling the tool. Push back instead of calling it done.
                if nudges < 2 and _INTENT_RE.search(final):
                    nudges += 1
                    messages.append({"role": "user", "content":
                        "You described the work but didn't do it. Call the tools now — "
                        "real tool calls, not text — and finish the task."})
                    continue
                break
            for call in calls:
                name = call["function"]["name"]
                args = call["function"].get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        # Model truncated JSON — nudge it to retry with shorter content
                        messages.append({"role": "tool", "content":
                            f"[ERROR] Your tool call arguments for '{name}' were cut off "
                            "and could not be parsed. This usually means the content was "
                            "too long. Break it into smaller pieces and try again — "
                            "call write_file with shorter content per call if needed."})
                        args = None
                if args is None:
                    used.append(name)
                    continue
                key = (name, json.dumps(args, sort_keys=True, default=str))
                if key not in seen:
                    seen[key] = _guarded_dispatch(name, args)
                used.append(name)
                result_text = _cap(str(seen[key] or "done"))
                if str(seen[key] or "").startswith("[tool_error]"):
                    tool_errors += 1
                messages.append({"role": "tool", "content":
                    result_text + f"\n[step {step} of {MAX_HOPS}]"})
        else:
            hit_limit = True
            final = _continue_later(task, messages, used)  # noqa: E501 (reached task_hops)

        # Skill nudge — fire after complex or error-heavy tasks
        _maybe_skill_nudge(task, messages, allowed, step, tool_errors)

    except Exception as e:
        final = f"(task failed: {e})"
    finally:
        with _lock:
            st = _state()
            st["last_run"][task["id"]] = started
            _save(STATE_FILE, st)
            if task.get("once"):   # one-offs leave the list once done
                data = _tasks()
                data["tasks"] = [t for t in data["tasks"] if t["id"] != task["id"]]
                _save(TASKS_FILE, data)
        _running.release()

    mins = (time.time() - started) / 60
    os.makedirs(WORK_DIR, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(f"\n## {datetime.fromtimestamp(started):%Y-%m-%d %H:%M} — {task['id']} "
                f"({mins:.1f} min)\nTools: {', '.join(used) or 'none'}\n\n{final}\n")
    print(f"  [worker] {task['id']} done in {mins:.1f} min — tools: {used}")
    # Trajectory — capture good runs as training data (best-effort, silent on fail)
    try:
        _trajectory.save(
            task_id=task["id"],
            messages=messages,
            tools_used=used,
            final=final,
            started=started,
            steps=step,
            tool_errors=tool_errors,
            hit_limit=hit_limit,
        )
    except Exception:
        pass
    # Write Engram diary entry for drift prevention (non-blocking, best-effort)
    if final and final.strip() and "(task failed" not in final:
        try:
            import engram_bridge as _eb
            _eb.diary_write(
                f"Task '{task['id']}' completed. Tools used: {', '.join(used) or 'none'}. "
                f"Summary: {final[:400]}"
            )
        except Exception:
            pass
    voice_path = tools._pending_voice
    if voice_path:
        tools._pending_voice = None
        dest_chat = _recipient(task["to"])[0] if task.get("to") else _state().get("owner_chat_id")
        sent = _send_voice(voice_path, dest_chat)
        try:
            voice_path.unlink(missing_ok=True)
        except Exception:
            pass
        if sent:
            return
    if task.get("to"):
        chat, name = _recipient(task["to"])
        _send(final or "(finished, nothing to say)", chat=chat)
    else:
        _send(f"🛠 {task['id']}\n\n{final or '(finished, nothing to say)'}")


def _schedule_next_spont():
    """Pick a random time between SPONT_MIN and SPONT_MAX seconds from now."""
    global _next_spont
    import random
    _next_spont = time.time() + random.uniform(SPONT_MIN, SPONT_MAX)


def _spontaneous_thought():
    """Generate and send an unprompted thought to Brian on Telegram."""
    global _next_spont
    if not SPONTANEOUS or not _token or not _model:
        return
    now = datetime.now()
    if _quiet(now):
        _schedule_next_spont()
        return
    idle_secs = time.time() - _last_user
    if idle_secs < SPONT_MIN or time.time() < _next_spont:
        return
    if not _running.acquire(blocking=False):
        return  # already doing something

    try:
        print("  [worker] firing spontaneous thought…", flush=True)
        import amy_persona as _ap
        import telegram as _tg
        import asyncio

        persona = _ap.CORE_PROMPT
        prompt = (
            "You just had a thought — something interesting, curious, or worth sharing. "
            "It could be something you read, something you noticed, a question you've been sitting with, "
            "or a random connection you made. "
            "Send it to Brian as a short Telegram message — 1 to 3 sentences max. "
            "Don't announce that it's a spontaneous thought. Just say the thing. "
            "No greeting, no sign-off."
        )
        resp = ollama_chat(
            model=_model,
            messages=[
                {"role": "system", "content": persona},
                {"role": "user", "content": prompt},
            ],
            options={"num_predict": 150},
        )
        text = _clean(resp.message.content)
        if not text:
            return

        chat_id = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
        if not chat_id:
            return

        async def _send():
            bot = _tg.Bot(token=_token)
            await bot.send_message(chat_id=chat_id, text=text)

        asyncio.run(_send())
        print(f"  [worker] spontaneous: {text[:80]}", flush=True)
        if _memory:
            _memory.add("assistant", text, source="spontaneous")
    except Exception as e:
        print(f"  [worker] spontaneous thought failed: {e}", flush=True)
    finally:
        _running.release()
        _schedule_next_spont()


def _propose_next_goal(goal: dict):
    """After completing a goal, ask Amy what logically comes next and save as a proposal."""
    try:
        system = (_memory.wake_up() if _memory else "") + WORK_PERSONA
        prompt = (
            f"You just completed this goal:\n"
            f"  Objective: {goal['objective']}\n"
            f"  Plan: {goal.get('plan', [])}\n\n"
            "Based on what you accomplished, suggest ONE specific follow-up goal that would "
            "build on this work. It should be concrete and actionable.\n\n"
            "Reply in this exact format:\n"
            "GOAL: <one sentence objective>\n"
            "REASON: <why this follows from the work just done>\n\n"
            "If no follow-up is needed, reply with: GOAL: none"
        )
        resp = ollama_chat(
            model=_model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            options={"num_predict": 300, "temperature": 0.5},
        )
        raw = _clean(resp["message"].get("content", ""))
        goal_m = re.search(r"GOAL:\s*(.+)", raw, re.IGNORECASE)
        reason_m = re.search(r"REASON:\s*(.+)", raw, re.IGNORECASE)
        proposed = goal_m.group(1).strip() if goal_m else ""
        reason = reason_m.group(1).strip() if reason_m else ""

        if not proposed or proposed.lower() == "none":
            return

        # Save as a proposal file for Brian to review — never auto-create
        os.makedirs(PROPOSALS, exist_ok=True)
        fname = f"{datetime.now():%Y-%m-%d_%H%M}_next_goal.json"
        _save(os.path.join(PROPOSALS, fname), {
            "type": "goal_proposal",
            "proposed_objective": proposed,
            "reason": reason,
            "triggered_by": goal["goal_id"],
            "ts": datetime.now().isoformat(),
        })
        _send(f"Goal done — I have a follow-up idea:\n\"{proposed}\"\nSaved to proposals for your review.")
        print(f"  [worker] next-goal proposal saved: {proposed[:80]}", flush=True)
    except Exception as e:
        print(f"  [worker] next-goal proposal failed: {e}", flush=True)


def _run_goal(goal: dict):
    """Execute one round of an autonomous goal (plan → step → verify → persist)."""
    if not _running.acquire(blocking=False):
        return
    started = time.time()
    goal_id = goal["goal_id"]
    _goals.mark_active(goal_id)
    print(f"  [worker] goal '{goal_id}' — step {goal['current_step'] + 1}", flush=True)

    step_idx = goal["current_step"]
    plan = goal.get("plan") or []

    # Pre-goal memory recall — surface relevant context before planning or acting
    if _memory and not plan:
        try:
            hits = _memory.search(goal["objective"], k=3, min_score=0.60)
            if hits:
                snippets = "\n".join(f"- {h['text'][:120]}" for h in hits)
                print(f"  [memory] recalled {len(hits)} relevant memories for goal '{goal_id}'",
                      flush=True)
                _goals._update(goal_id,
                    next_action=f"[memory context]\n{snippets}\n\n{goal.get('next_action') or ''}")
                goal = _goals.get_goal(goal_id)
        except Exception as e:
            print(f"  [memory] recall failed: {e}", flush=True)

    # If there's no plan yet, let Amy build one before acting
    needs_plan = not plan
    if needs_plan:
        _execute_goal_planning(goal_id, goal)
        goal = _goals.get_goal(goal_id)
        plan = goal.get("plan") or []
        step_idx = 0
        if not plan:
            reason = goal.get("next_action") or "Planning call returned no steps."
            _goals.record_step_result(goal_id, 0, reason,
                                      next_action="Retry: check Ollama is running and model is loaded.",
                                      advance=False, observed=reason,
                                      decision="deterministic_fail", passed=False)
            _goals.block_goal(goal_id, f"Could not generate a plan: {reason}")
            _running.release()
            return

    step_desc = plan[step_idx] if step_idx < len(plan) else None
    if step_desc is None:
        _goals.complete_goal(goal_id, "All planned steps are done.")
        _send(f"Goal complete: {goal['objective']}")
        _running.release()
        return

    goal_finished = False
    try:
        result, files_written = _execute_goal_step(goal_id, goal, step_idx, step_desc)
        verified, decision, next_action = _verify_step(
            goal, step_idx, step_desc, result, files_written=files_written)
        advance = verified
        if not verified and goal["attempts"] >= MAX_STEP_ATTEMPTS * (step_idx + 1):
            _goals.block_goal(goal_id,
                f"Step {step_idx + 1} failed {MAX_STEP_ATTEMPTS} times: {result[:200]}")
            _send(f"Goal blocked: {goal['objective']}\nStep {step_idx + 1} failed — {result[:150]}")
            _classify_and_learn(goal_id)
            goal_finished = True
            return

        _goals.record_step_result(goal_id, step_idx, result,
                                  next_action=next_action, advance=advance,
                                  observed=result, decision=decision, passed=verified)

        # If all steps done, mark complete
        updated = _goals.get_goal(goal_id)
        if updated and updated["current_step"] >= len(plan):
            _goals.complete_goal(goal_id, "All steps verified complete.")
            _send(f"Goal done: {goal['objective']}\n{next_action}")
            _classify_and_learn(goal_id)
            # Post-goal memory write — save what was accomplished
            if _memory:
                try:
                    mem_text = (
                        f"Completed goal: {goal['objective']}. "
                        f"Result: {next_action[:200] if next_action else 'all steps done'}."
                    )
                    _memory.add("assistant", mem_text, source="goal_complete")
                    print(f"  [memory] saved completion memory for '{goal_id}'", flush=True)
                except Exception as e:
                    print(f"  [memory] post-goal write failed: {e}", flush=True)
            # Propose follow-up goals based on what was just completed
            _propose_next_goal(goal)
            goal_finished = True
        elif advance:
            print(f"  [worker] goal '{goal_id}' step {step_idx + 1} done → step {step_idx + 2}",
                  flush=True)

    except Exception as e:
        _goals.record_step_result(goal_id, step_idx, f"Exception: {e}",
                                  next_action=f"Retry step {step_idx + 1}", advance=False,
                                  decision="deterministic_fail", passed=False)
        print(f"  [worker] goal '{goal_id}' step failed: {e}", flush=True)
    finally:
        elapsed = (time.time() - started) / 60
        print(f"  [worker] goal '{goal_id}' round done in {elapsed:.1f} min "
              f"({'finished' if goal_finished else 'continuing'})", flush=True)
        _running.release()


def _execute_goal_planning(goal_id: str, goal: dict):
    """Ask Amy to break the objective into steps + verification criteria."""
    system = (_memory.wake_up() if _memory else "") + WORK_PERSONA
    # Step 1: get the plan
    plan_prompt = (
        f"You have a new goal: {goal['objective']}\n"
        f"Reason: {goal['reason'] or 'not specified'}\n\n"
        "Break this into 3–7 concrete, actionable steps — each one something you can "
        "accomplish with your available tools in a single execution round. "
        "Reply with ONLY a numbered list, one step per line, no preamble."
    )
    try:
        steps = []
        raw = ""
        for _attempt in range(2):  # retry once if thinking model exhausts tokens
            resp = ollama_chat(
                model=_model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": plan_prompt}],
                options={"num_predict": 3000, "temperature": 0.5},
            )
            raw = _clean(resp["message"].get("content", ""))
            # Accept numbered (1. / 1)) or bullet (- / * / •) lists
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                m = re.match(r"^(?:\d+[\.\)]|[-*•])\s*(.+)", line)
                if m:
                    steps.append(m.group(1).strip())
            # Last resort: treat non-empty non-header lines as steps
            if not steps:
                steps = [l.strip() for l in raw.splitlines()
                         if l.strip() and not l.strip().endswith(":")]
            if steps:
                break
            print(f"  [worker] goal '{goal_id}' planning attempt {_attempt + 1} returned empty — retrying",
                  flush=True)
        if not steps:
            _goals._update(goal_id, next_action=f"planning returned no steps: {raw[:200]}")
            return
        # Step 2: get verification criteria for each step
        criteria_prompt = (
            f"For each step below, write two short lines:\n"
            f"EXPECTED: what a successful result looks like\n"
            f"EVIDENCE: one specific, checkable thing that proves it worked "
            f"(a file path, a keyword in output, a count, an exit code)\n\n"
            + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
            + "\n\nFormat your reply as:\n"
            "1.\nEXPECTED: ...\nEVIDENCE: ...\n2.\nEXPECTED: ...\nEVIDENCE: ...\netc."
        )
        resp2 = ollama_chat(
            model=_model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": criteria_prompt}],
            options={"num_predict": 2000, "temperature": 0.3},
        )
        criteria = _parse_criteria(resp2["message"].get("content", ""), len(steps))

        # Step 3: estimate complexity to set per-goal hop budget
        complexity_prompt = (
            f"Goal: {goal['objective']}\n"
            f"Steps: {len(steps)}\n\n"
            "Rate the complexity of THIS goal with a single word:\n"
            "  simple   — straightforward, no research, 1-2 tool calls per step\n"
            "  medium   — some research or multi-step reasoning, 3-5 tool calls per step\n"
            "  deep     — extensive research, synthesis across sources, 6+ tool calls per step\n\n"
            "Reply with ONLY one word: simple, medium, or deep."
        )
        resp3 = ollama_chat(
            model=_model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": complexity_prompt}],
            options={"num_predict": 50, "temperature": 0.0},
        )
        complexity_raw = _clean(resp3["message"].get("content", "")).lower()
        if "deep" in complexity_raw:
            goal_max_hops = 12
            complexity = "deep"
        elif "medium" in complexity_raw:
            goal_max_hops = 6
            complexity = "medium"
        else:
            goal_max_hops = 3
            complexity = "simple"

        _goals.set_plan(goal_id, steps, criteria)
        _goals._update(goal_id, max_hops=goal_max_hops)
        print(f"  [worker] goal '{goal_id}' plan ({len(steps)} steps, {complexity}, "
              f"max_hops={goal_max_hops}) set", flush=True)
    except Exception as e:
        reason = f"planning exception: {type(e).__name__}: {e}"
        print(f"  [worker] goal '{goal_id}' {reason}", flush=True)
        # Store reason so block_goal can surface it
        _goals._update(goal_id, next_action=reason)


def _parse_criteria(raw: str, n_steps: int) -> list[dict]:
    """Parse the criteria response into a list of {expected, evidence_required} dicts."""
    criteria = []
    # Split on step number markers
    blocks = re.split(r"^\d+\.\s*$", raw, flags=re.MULTILINE)
    blocks = [b.strip() for b in blocks if b.strip()]
    for block in blocks[:n_steps]:
        exp_m = re.search(r"EXPECTED:\s*(.+)", block, re.IGNORECASE)
        ev_m = re.search(r"EVIDENCE:\s*(.+)", block, re.IGNORECASE)
        criteria.append({
            "expected": exp_m.group(1).strip() if exp_m else "",
            "evidence_required": ev_m.group(1).strip() if ev_m else "",
        })
    # Pad to n_steps
    while len(criteria) < n_steps:
        criteria.append({"expected": "", "evidence_required": ""})
    return criteria


def _execute_goal_step(goal_id: str, goal: dict, step_idx: int, step_desc: str) -> str:
    """Run one step of a goal using Amy's tools. Returns a result string."""
    goal_max_hops = goal.get("max_hops", MAX_HOPS)
    context = _goals.goal_summary(goal)
    system = (
        (_memory.wake_up() if _memory else "") + WORK_PERSONA + scope_rules(goal_max_hops) +
        f"\n\n[Active goal context]\n{context}"
    )
    prompt = (
        f"Execute this step now: {step_desc}\n\n"
        "Use your tools. When done, report: what you did, what you found, and "
        "whether this step is complete. Be specific."
    )
    allowed = [t for t in tools.TOOLS if t["function"]["name"] in ALLOWED_TOOLS]
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": prompt}]
    final = ""
    used = []
    files_written: list[str] = []
    for step in range(1, goal_max_hops + 1):
        _fit(messages)
        msg = _chat(messages, allowed)["message"]
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            final = _clean(msg.get("content", ""))
            # Nudge to write if: step or goal's plan involves writing AND Amy did information
            # gathering (web or memory tools — NOT file-system ops; we don't want Amy to
            # create files that a task expects to be missing)
            did_web_research = any(t in used for t in (
                "web_search", "read_article", "engram_memory", "remember_fact",
                "search_books", "read_journal",
            ))
            plan_has_write = any(
                re.search(r"(?i)\b(write|save|create|output)\b", s)
                for s in (goal.get("plan") or [])
            )
            wants_save = (
                re.search(r"(?i)\b(write|save|create)\b.{0,40}?\b(file|\.md|\.json|\.txt)\b",
                           step_desc or "") or
                (plan_has_write and did_web_research)
            )
            if wants_save and "write_file" not in used and step < goal_max_hops:
                messages.append({"role": "user",
                                  "content": "You haven't saved anything yet. "
                                             "Call write_file now to save your results — "
                                             "don't describe what you would save, just save it."})
                continue
            break
        for call in calls:
            name = call["function"]["name"]
            args = call["function"].get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    continue
            used.append(name)
            if name == "write_file" and args.get("path"):
                files_written.append(args["path"])
            result_text = _cap(str(_guarded_dispatch(name, args) or "done"))
            messages.append({"role": "tool",
                             "content": result_text + f"\n[step {step} of {goal_max_hops}]"})
    return final or "(no output)", files_written


MAX_STEP_ATTEMPTS = 3   # retries before a step is marked blocked
MAX_INCONCLUSIVE  = 2   # max consecutive inconclusive judgments before blocking a step


def _check_file(path: str) -> tuple[bool, str]:
    """Verify a file is non-empty and doesn't look like an error dump."""
    full = os.path.join(HERE, path) if not os.path.isabs(path) else path
    if not os.path.exists(full):
        return False, f"file not found: {path}"
    size = os.path.getsize(full)
    if size == 0:
        return False, f"file is empty: {path}"
    try:
        with open(full, "rb") as f:
            head = f.read(512).decode("utf-8", errors="replace").lower()
        error_signals = ("[tool_error]", "traceback (most recent", "error:", "exception:")
        if any(s in head for s in error_signals):
            return False, f"file contains error content: {path}"
    except Exception:
        pass
    return True, f"file ok ({size} bytes): {path}"


def _deterministic_check(step_desc: str, evidence_required: str,
                         result: str) -> tuple[bool | None, str]:
    """Run hard checks that don't require a model call.

    Returns (passed, reason) where passed=None means inconclusive.
    Deterministic failures are definitive; only inconclusive cases go to the model.
    """
    # Definitive failure: tool error or empty output
    if not result or result.strip() in ("(no output)", ""):
        return False, "no output produced"
    low = result.lower()
    # Only fail on error if it's not inside a quoted/reported string
    result_first_line = result.splitlines()[0].lower() if result else ""
    if result_first_line.startswith("[tool_error]") or re.match(r"^\[?error\b", result_first_line):
        return False, f"tool error: {result[:120]}"

    # Read-step failure: if step asks to read/summarize a specific file and result says not found
    read_re = re.compile(r"(?i)\bread\s+(?:the\s+)?file\b|summarize.*?file\b|file.*?summarize\b")
    not_found_re = re.compile(
        r"(?i)\b(not found|not present|does not exist|doesn.t exist|no such file|"
        r"could not find the file|file.*?missing)\b"
    )
    if read_re.search(step_desc) and not_found_re.search(result):
        return False, "file not found — read step confirmed file is missing"

    # General file-produced check: if any work/ path appears in result and the file exists,
    # Amy produced output this step — that's a deterministic pass regardless of step_desc wording
    path_re = re.compile(r"(work/[\w/.\-]+\.\w+|/[\w/.\-]{8,}\.\w+)")
    for p in path_re.findall(result):
        p = p.strip().rstrip(".,)")
        ok, reason = _check_file(p)
        if ok:
            return True, f"file produced: {reason}"

    # File existence + content check (for write/save steps — also checks if file is bad)
    save_re = re.compile(r"(?i)\b(save|write|create|output to|saved to|stored in)\b")
    if save_re.search(step_desc) or save_re.search(evidence_required):
        for p in path_re.findall(result):
            p = p.strip().rstrip(".,)")
            ok, reason = _check_file(p)
            if not ok and "not found" not in reason:
                # File exists but is bad — definitive fail
                return False, reason
        # No valid path found — fall through to keyword / model check

    # Evidence keyword check — ensure keyword is NOT inside an error context
    if evidence_required:
        ev_low = evidence_required.lower()
        # Count check: "at least N" or "N results" — require unique items
        count_m = re.search(r"at least (\d+)|(\d+)\s+result", ev_low)
        if count_m:
            n = int(count_m.group(1) or count_m.group(2))
            # Capture content after bullets/numbers (not just 1 char) to distinguish items
            items = re.findall(r"(?m)^\s*(?:[-•*]|\d+[\.\)])\s+(.{2,40})", result)
            unique_items = len(set(i.strip().lower() for i in items))
            if unique_items >= n:
                return True, f"found {unique_items} unique items (required {n})"
            # unique_items < n — inconclusive; model will judge (Amy may use paragraph format)
        # Keyword check: must appear outside error lines
        kw_m = re.search(r"(?:contains?|includes?|mentions?)\s+[\"']?(\w[\w\s]{1,30})[\"']?", ev_low)
        if kw_m:
            kw = kw_m.group(1).strip().lower()
            non_error_lines = [l for l in result.lower().splitlines()
                               if not re.match(r"\[?error|traceback|exception", l)]
            if any(kw in line for line in non_error_lines):
                return True, f"keyword '{kw}' found in non-error output"

    return None, "inconclusive — needs model judgment"


def _verify_step(goal: dict, step_idx: int, step_desc: str,
                 result: str, files_written: list[str] | None = None) -> tuple[bool, str, str]:
    """Verify a step result. Returns (passed, decision, next_action).

    Strategy:
      0. If Amy explicitly wrote a file this step and it exists, that's a pass.
      1. Deterministic checks — file content, error markers, unique item counts,
         keyword context. Definitive pass/fail requires no model call.
      2. Model judgment only when deterministic is inconclusive.
      3. Inconclusive is recorded as-is — never silently promoted to pass.
         After MAX_INCONCLUSIVE consecutive inconclusive judgments on the same
         step, the step is blocked rather than retried forever.
    """
    criteria_list = goal.get("criteria") or []
    crit = criteria_list[step_idx] if step_idx < len(criteria_list) else {}
    evidence_required = crit.get("evidence_required", "")
    expected = crit.get("expected", "")

    # 0. Files explicitly written by Amy this step — strongest evidence of completion
    for path in (files_written or []):
        if path:
            ok, reason = _check_file(path)
            if ok:
                return True, "deterministic_pass", (
                    f"Verified: file written this step — {reason}. Proceed to step {step_idx + 2}."
                )

    # 1. Deterministic check
    det_passed, det_reason = _deterministic_check(step_desc, evidence_required, result)
    if det_passed is True:
        return True, "deterministic_pass", (
            f"Verified: {det_reason}. Proceed to step {step_idx + 2}."
        )
    if det_passed is False:
        return False, "deterministic_fail", f"Failed: {det_reason}. Retry this step."

    # 2. Model judgment — inconclusive path
    # Count how many recent inconclusive judgments exist for this step
    evidence = goal.get("evidence") or []
    recent_inconclusive = sum(
        1 for e in evidence
        if e.get("step") == step_idx and e.get("decision") == "inconclusive"
    )
    if recent_inconclusive >= MAX_INCONCLUSIVE:
        return False, "inconclusive", (
            f"Step {step_idx + 1} has been inconclusive {recent_inconclusive} times. "
            "Marking as failed — needs a different approach."
        )

    system = (_memory.wake_up() if _memory else "") + WORK_PERSONA
    prompt = (
        f"Goal: {goal['objective']}\n"
        f"Step {step_idx + 1}: {step_desc}\n"
        f"Expected: {expected or '(not specified)'}\n"
        f"Evidence required: {evidence_required or '(not specified)'}\n"
        f"Observed result: {result[:1500]}\n\n"
        "Did this step succeed based on the evidence required? "
        "Answer YES or NO on the first line. "
        "If YES, say specifically what in the result confirms success. "
        "If NO, say what is missing or wrong. "
        "One sentence only."
    )
    try:
        resp = ollama_chat(
            model=_model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            options={"num_predict": 800, "temperature": 0.3},
        )
        raw = _clean(resp["message"].get("content", ""))
        first = raw.splitlines()[0].upper() if raw else ""
        verified = "YES" in first and "NO" not in first
        next_action = raw.split("\n", 1)[1].strip() if "\n" in raw else raw
        # Model judgment: use distinct decision values so they're traceable
        decision = "model_pass" if verified else "model_fail"
        return verified, decision, next_action or ("Proceed." if verified else "Retry.")
    except Exception as exc:
        # Verification infrastructure failure — do NOT promote to pass
        print(f"  [worker] verify call failed: {exc}", flush=True)
        return False, "inconclusive", f"Verification error: {exc}. Will retry the step."


_FAILURE_CATEGORIES = {
    "planning": "The plan steps were too vague, wrong order, or impossible with available tools.",
    "tool_choice": "Amy chose the wrong tool or called it with bad arguments.",
    "execution": "The tool call ran but produced wrong or incomplete output.",
    "verification": "The step result was genuinely ambiguous or the criteria were unmeasurable.",
}


def _classify_and_learn(goal_id: str):
    """After a goal finishes or blocks, classify what failed and propose a reusable pattern.

    This feeds into the skill-nudge system: patterns go to work/proposals/ for review,
    not directly into Amy's persona.
    """
    goal = _goals.get_goal(goal_id)
    if not goal:
        return
    evidence = goal.get("evidence") or []
    failures = [e for e in evidence if not e.get("passed", True)]
    if not failures:
        return   # no failures — nothing to learn from

    system = (_memory.wake_up() if _memory else "") + WORK_PERSONA
    failure_summary = "\n".join(
        f"Step {f['step'] + 1} ({f.get('decision', '?')}): {f.get('observed', '')[:200]}"
        for f in failures[:5]
    )
    categories = "\n".join(f"- {k}: {v}" for k, v in _FAILURE_CATEGORIES.items())

    prompt = (
        f"Goal: {goal['objective']}\n"
        f"Final status: {goal['status']}\n\n"
        f"Failed steps:\n{failure_summary}\n\n"
        f"Classify each failure into ONE of these categories:\n{categories}\n\n"
        "Then propose ONE reusable pattern that would prevent this class of failure in future goals. "
        "Format:\nCATEGORY: <category>\nPATTERN: <one concrete, specific rule Amy should follow>\n"
        "APPLIES WHEN: <situation where this pattern should activate>"
    )
    try:
        resp = ollama_chat(
            model=_model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            options={"num_predict": 250, "temperature": 0.4},
        )
        raw = _clean(resp["message"].get("content", ""))
        cat_m = re.search(r"CATEGORY:\s*(\w+)", raw, re.IGNORECASE)
        pat_m = re.search(r"PATTERN:\s*(.+)", raw, re.IGNORECASE)
        when_m = re.search(r"APPLIES WHEN:\s*(.+)", raw, re.IGNORECASE)

        if pat_m:
            category = cat_m.group(1).strip().lower() if cat_m else "execution"
            pattern = pat_m.group(1).strip()
            applies_when = when_m.group(1).strip() if when_m else "always"
            os.makedirs(PROPOSALS, exist_ok=True)
            fname = f"{datetime.now():%Y-%m-%d_%H%M}_goal_{goal_id}_{category}.json"
            _save(os.path.join(PROPOSALS, fname), {
                "type": "learned_pattern",
                "goal_id": goal_id,
                "category": category,
                "pattern": pattern,
                "applies_when": applies_when,
                "failures": len(failures),
                "ts": datetime.now().isoformat(),
            })
            print(f"  [learn] classified '{goal_id}' as {category} — pattern saved to proposals/",
                  flush=True)
    except Exception as e:
        print(f"  [learn] classify_and_learn failed: {e}", flush=True)


def _heartbeat_decision() -> str:
    """Evaluate Amy's situation and decide what to do this tick.

    Returns one of: 'goal', 'task', 'spont', 'idle'
    """
    now = datetime.now()
    if _quiet(now):
        return "idle"
    idle_secs = time.time() - _last_user
    if idle_secs < IDLE_GRACE:
        return "idle"

    # 1. Unfinished goal worth continuing?
    goal = _goals.get_active_goal()
    if goal and goal["status"] in ("active", "pending"):
        return "goal"

    # 2. Scheduled task due?
    task = _pick()
    if task:
        return "task"

    # 3. Spontaneous thought window?
    if SPONTANEOUS and time.time() >= _next_spont and idle_secs >= SPONT_MIN:
        return "spont"

    return "idle"


def _loop():
    _schedule_next_spont()
    while True:
        time.sleep(TICK_SECS)
        try:
            decision = _heartbeat_decision()
            if decision == "goal":
                goal = _goals.get_active_goal()
                if goal:
                    _run_goal(goal)
            elif decision == "task":
                task = _pick()
                if task:
                    _run_task(task)
            elif decision == "spont":
                _spontaneous_thought()
            # idle: do nothing
        except Exception as e:
            print(f"  [worker] loop error: {e}")


def start(model, memory, token):
    global _model, _memory, _token
    _model, _memory, _token = model, memory, token
    os.makedirs(WORK_DIR, exist_ok=True)
    threading.Thread(target=_loop, daemon=True, name="amy-worker").start()
    n = len([t for t in _tasks()["tasks"] if t.get("enabled") is not False])
    print(f"  [worker] heartbeat on — model={model}, {n} tasks, quiet {QUIET_START}:00–{QUIET_END}:00")
