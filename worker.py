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
CTX_WINDOW  = 32768

ALLOWED_TOOLS = {
    "web_search", "read_article",
    "read_file", "write_file", "list_files",
    "list_work", "read_deliverable",
    "journal_entry", "read_journal", "remember_fact",
    "add_goal", "complete_goal",
    "research_income", "log_income_idea",
    "fund_status", "search_books",
    "moltbook_check_in", "moltbook_post", "moltbook_engage", "moltbook_verify",
    "create_slideshow", "bottube_post", "bottube_check",
    "self_improve", "speak",
    "study_creator", "family_fund",
    "income_eval", "income_stream",
    "self_metrics", "engram_memory",
    "tiktok_search",
}

_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

_lock       = threading.Lock()
_running    = threading.Lock()   # one task at a time
_model      = None
_memory     = None
_token      = None
_last_user  = 0.0


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
                    {"role": "user", "content": task["prompt"].replace("{date}", today)
                                               .replace("{work}", WORK_DIR)}]
        seen, nudges = {}, 0
        for step in range(1, task_hops + 1):
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
                messages.append({"role": "tool", "content":
                    _cap(str(seen[key] or "done")) + f"\n[step {step} of {MAX_HOPS}]"})
        else:
            final = _continue_later(task, messages, used)  # noqa: E501 (reached task_hops)
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


def _loop():
    while True:
        time.sleep(TICK_SECS)
        try:
            task = _pick()
            if task:
                _run_task(task)
        except Exception as e:
            print(f"  [worker] loop error: {e}")


def start(model, memory, token):
    global _model, _memory, _token
    _model, _memory, _token = model, memory, token
    os.makedirs(WORK_DIR, exist_ok=True)
    threading.Thread(target=_loop, daemon=True, name="amy-worker").start()
    n = len([t for t in _tasks()["tasks"] if t.get("enabled") is not False])
    print(f"  [worker] heartbeat on — model={model}, {n} tasks, quiet {QUIET_START}:00–{QUIET_END}:00")
