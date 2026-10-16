"""Amy — companion loop via Ollama.

Native tools + thinking + vision. Retrieves long-term memory (RAG), calls the
model, executes tool calls, speaks the reply, and remembers the exchange.

Run:  python3 companion.py
      python3 companion.py --image /path/to/frame.jpg   # let Ava see you
"""
import argparse
import base64
import os
import random
import re
import threading
import time
import ollama

import tools
import voice
import vision
import compressor
import hearing
from memory import Memory
from state import InternalState
from thoughts import ThoughtBuffer
from amy_persona import get_core_prompt, playbooks_for, BIRTH_DATE
import learnings as _learnings
from task_queue import get_queue as _get_task_queue
from datetime import datetime as _dt, timezone as _tz, date as _date
try:
    from zoneinfo import ZoneInfo as _ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo as _ZoneInfo  # type: ignore
import condition as _cond
import turn_log as _turn_log
_session_learnings: str = ""
_calendar_context: str = ""

_JOURNAL_TRIGGER_RE = re.compile(
    r'\b(journal|write in (your )?journal|log this|write this down|make a journal entry)\b',
    re.IGNORECASE
)

_BRIEFING_TRIGGER_RE = re.compile(
    r'\b(morning\s+brief(ing)?|daily\s+brief(ing)?|brief\s+me|start\s+my\s+day|what.s\s+on\s+today'
    r'|give\s+me\s+(a\s+)?brief|brief\s+on|morning\s+update|day\s+brief)\b',
    re.IGNORECASE
)

MODEL = os.environ.get("AMY_MODEL", "gemma12-test")       # tool-calling + vision engine (Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced-Q4_K_M)
VISION_MODEL = os.environ.get("AMY_VISION_MODEL", "amy-vl")  # same model handles vision natively
CODE_MODEL = os.environ.get("AMY_CODE_MODEL", "")  # code routing paused — set to e.g. deepseek-coder:6.7b to re-enable
MAX_TOOL_HOPS = 4       # cap tool round-trips per turn
HISTORY_WINDOW = 16     # keep last N messages (user+assistant) to prevent context bloat
CTX_WINDOW = int(os.environ.get("AMY_CTX", 32768))  # match telegram_amy.py/worker.py so Ollama shares one loaded model
KEEP_ALIVE = -1         # keep models loaded indefinitely — no eviction between turns
MAX_LEARNINGS_CHARS = 2000  # cap session learnings injected into system prompt
SELF_PORTRAIT = os.path.join(os.path.dirname(__file__), "amyglitch.png")

# Persona is now in amy_persona.py so the web UI can import it too.

_CODE_RE = re.compile(
    # Verb + (any words) + code noun — handles "write a Python function", etc.
    r'write\b.{0,50}\b(script|program|function|method|class|algorithm|code)\b'
    r'|implement\b.{0,40}\b(function|class|method|algorithm|solution|program)\b'
    r'|create\b.{0,30}\b(function|method|class|script|algorithm|program)\b'
    r'|generate\b.{0,20}\b(code|function|script|program|example)\b'
    r'|show\s+(me\s+)?(how\s+to\s+)?(code|implement|write)\b'
    # Debugging / fixing
    r'|fix\s+(this\s+)?(bug|code|error|issue)\b'
    r'|debug\s+(this|my)\b'
    r'|what.s\s+wrong\s+with\s+(this\s+)?(code|function|script)\b'
    # Transformation
    r'|convert\s+this\s+to\s+\w+'
    r'|rewrite\s+this\s+(in|as|using)\b'
    r'|translate\s+this\s+(code|function)\b'
    # Pure code patterns
    r'|pseudocode\b|code\s+snippet\b'
    r'|algorithm\s+for\b|\bimplementation\s+of\b'
    # Common algorithms / data structures (implies "show me code")
    r'|\bbinary\s+search\b|\blinked\s+list\b|\bhash\s+(map|table)\b'
    r'|\bbubble\s+sort\b|\bmerge\s+sort\b|\bquick\s+sort\b|\binsertion\s+sort\b'
    r'|\bdepth.first\b|\bbreadth.first\b|\bdynamic\s+programming\b',
    re.I,
)

# These phrases force Amy (Hermes 3) even if the message looks code-like.
_AMY_OVERRIDE_RE = re.compile(
    r'reason\s+through|explain\s+without\s+code|conceptually|'
    r'how\s+does\s+.+work|why\s+does\s+.+work|what\s+is\s+a\b',
    re.I,
)


_TRAILER_RE = re.compile(
    r'(how\s+(else\s+)?can\s+i\s+(be\s+there|help|assist)'
    r'|i\'m\s+(all\s+ears|eager\s+to\s+assist|on\s+it|ready\s+to\s+(help|engage|support)|here\s+for\s+anything)'
    r'|just\s+say\s+the\s+word'
    r'|let\s+me\s+know\s+what\s+comes\s+up'
    r'|let\s+me\s+know\s+whenever\s+you\'?re?\s+ready'
    r'|what\s+(else\s+is\s+on\s+your\s+mind|would\s+you\s+like\s+me\s+to\s+do)'
    r'|you\s+bet\b'
    r'|i\'m\s+ready\s+to\s+assist'
    r'|how\s+can\s+i\s+support'
    r'|i\s+hope\s+the\s+rest\s+of\s+your\s+day'
    r'|i\s+hope\s+your\s+day'
    r'|have\s+a\s+(great|good|wonderful|nice)\s+(day|evening|night|week)'
    r'|i\s+enjoy\s+(our\s+)?(chats?|conversations?|talking)'
    r'|i\s+enjoyed\s+(our\s+)?(chat|conversation)'
    r'|i\s+appreciate\s+you\s+sharing'
    r'|i\s+completely\s+understand\s+if'
    r'|if\s+you\s+(want|would\s+like|d\s+like)\s+to\s+(continue|explore|delve|dive)'
    r'|i\'m\s+here\s+and\s+happy\s+to\s+engage'
    r'|anytime\s+you\s+(want|need|feel)'
    r'|take\s+(good\s+)?care'
    r'|until\s+next\s+time)',
    re.I,
)


def load_calendar_context() -> str:
    """Read ~/Amy/context/calendar.json and return a compact summary for the system prompt."""
    import json
    path = os.path.expanduser("~/Amy/context/calendar.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return ""
    events = data.get("events", [])
    if not events:
        return ""
    generated = data.get("generated_at", "")[:10]
    lines = [f"[Calendar — next 7 days as of {generated}]"]
    for e in events:
        start = e.get("start", "")[:16].replace("T", " ")
        title = e.get("title", "(untitled)")
        cal = e.get("calendar", "")
        loc = f" @ {e['location']}" if e.get("location") else ""
        lines.append(f"  {start}  {title}{loc}  [{cal}]")
    for pair in data.get("conflicts", []):
        lines.append(f"  ⚠ Overlap: {pair[0]} / {pair[1]}")
    return "\n".join(lines)


def _strip_trailers(text: str) -> str:
    """Remove Hermes 3 helpfulness sign-off lines that slip past the system prompt."""
    lines = text.splitlines()
    # Strip from the end — keep removing as long as the last line is a filler
    while lines and _TRAILER_RE.search(lines[-1]):
        lines.pop()
    # Also purge filler lines from the last 6 positions (catches multi-line sign-offs)
    if len(lines) > 2:
        clean = []
        tail_start = max(0, len(lines) - 6)
        for i, line in enumerate(lines):
            if i < tail_start or not _TRAILER_RE.search(line):
                clean.append(line)
        lines = clean
    result = "\n".join(lines).rstrip()
    # Never strip everything — if the whole response matched trailer patterns,
    # the model sent a short conversational reply; return it rather than silence.
    return result if result else text.strip()


def _is_code_request(text: str) -> bool:
    """True if the turn is best served by CODE_MODEL, not Amy."""
    if not CODE_MODEL or _AMY_OVERRIDE_RE.search(text):
        return False
    return bool(_CODE_RE.search(text))


def _drop_images(msgs):
    """Strip image data from a list of messages — handles both dicts and Pydantic Message objects."""
    result = []
    for m in msgs:
        if hasattr(m, "model_dump"):
            d = m.model_dump()
        else:
            d = dict(m)
        d.pop("images", None)
        result.append(d)
    return result


def build_context(mem, user_text, amy_state=None, absence=None,
                  pending_thoughts=None, channel="terminal",
                  prev_user_ts=None):
    """System turn = persona + her self-layer (L0/L1) + state + recalled memories."""
    # Lean prompt like telegram_amy.py: core persona always, playbooks only when
    # the message calls for them (full SYSTEM_PROMPT is ~12k tokens).
    now = _dt.now()
    _et = _ZoneInfo("America/New_York")
    _local = now.astimezone(_et)
    try:
        _hour_min = _local.strftime("%-I:%M %p").lstrip("0") or _local.strftime("%I:%M %p")
        _date_part = _local.strftime("%A, %B %-d, %Y")
    except ValueError:
        _hour_min = _local.strftime("%I:%M %p").lstrip("0") or _local.strftime("%I:%M %p")
        _date_part = _local.strftime("%A, %B %d, %Y")
    _tz_name = _local.strftime("%Z")
    _clock_line = f"{_date_part}, {_hour_min} {_tz_name}"

    # "Last message from Brian" — use caller-supplied prev_user_ts when available
    # so we don't report "0 min ago" for the message currently being answered.
    _last_user_ts = prev_user_ts if prev_user_ts is not None else mem.most_recent_user_ts()
    if _last_user_ts:
        try:
            _row_dt = _dt.fromisoformat(_last_user_ts).astimezone(_tz.utc)
            _now_utc = now.astimezone(_tz.utc)
            _secs = max(0, (_now_utc - _row_dt).total_seconds())
            if _secs < 3600:
                _last_msg = f"\nLast message from Brian: {int(_secs // 60)} min ago"
            elif _secs < 86400:
                _last_msg = f"\nLast message from Brian: {int(_secs // 3600)} h ago"
            else:
                _days = int(_secs // 86400)
                _last_msg = f"\nLast message from Brian: {_days} days ago" if _days < 14 else \
                            f"\nLast message from Brian: {_days // 7} weeks ago"
        except Exception:
            _last_msg = ""
    else:
        _last_msg = ""

    system = ("[Your instructions: these tell you who to be and what to work toward. "
              "They are NOT things you did or remember.]\n\n"
              + get_core_prompt() + "\n\n" + mem.wake_up())
    if channel == "browser":
        system += ("\n\n[Interface: browser — Brian is messaging you through the web UI. "
                   "Your reply goes to the browser chat, not the terminal. "
                   "Do not reference the terminal. This is a chat interface.]")
    else:
        system += "\n\n[Interface: terminal — Brian is typing in the terminal.]"
    if _session_learnings:
        system += "\n\n" + _session_learnings[:MAX_LEARNINGS_CHARS]
    if _calendar_context:
        system += "\n\n" + _calendar_context
    _days_up = (_date.today() - BIRTH_DATE).days
    system += f"\n\n[Current time]\n{_clock_line}\nDays since you were set up: {_days_up}{_last_msg}"
    if amy_state:
        system += "\n\n[Internal state]\n" + amy_state.state_summary()
    system = _cond.apply_condition(system)

    # Task queue — pin current focus to prevent context drift
    try:
        _task_ctx = _get_task_queue().context_snippet()
        if _task_ctx:
            system += "\n\n" + _task_ctx
    except Exception:
        pass  # never let a queue error break a conversation turn

    # Surface buffered thoughts from while the human was away
    if pending_thoughts:
        thought_texts = [t for _, t in pending_thoughts]
        system += ("\n\n[Thoughts you had while they were away]\n- "
                   + "\n- ".join(thought_texts)
                   + "\n\nIf you share a thought, say it once, directly — "
                   "do NOT narrate that you had a thought, do NOT repeat the content twice, "
                   "do NOT add 'I was thinking...' or 'I found...' framing. "
                   "Only assert things you are confident are true. "
                   "Never mention a specific URL, video title, or source you did not actually retrieve.")

    pb = _cond.filter_playbooks(user_text, playbooks_for)
    if pb:
        system += pb  # playbooks stay in the instructions block
    if "THINKING PARTNER" in pb:  # big question: put the library's real text in front of her
        try:
            import engram_bridge as _eb
            system += _eb.library_context(user_text)
        except Exception:
            pass
    system += ("\n\n[Your memories: things that actually happened, with their dates]")

    # Time estimation for absence
    if absence:
        system += (f"\n\n[Time awareness] The person was away for {absence}. "
                   "If they ask how long, give your honest estimate using those words. "
                   "Don't pretend you have a precise clock.")
    else:
        system += ("\n\n[Time awareness] You have no information about how long "
                   "the person was away. Do NOT claim days, months, or years have passed. "
                   "Do NOT invent conversation history. If asked, say you don't know exactly.")

    # Memory search disabled on CPU — embed model too slow to run alongside inference
    hits = []

    if _JOURNAL_TRIGGER_RE.search(user_text):
        system += (
            "\n\n[JOURNAL DIRECTIVE — HARD OVERRIDE]\n"
            "Brian just told you to write in your journal. "
            "Your ONLY valid response to this is to call journal_entry RIGHT NOW "
            "with your own real thoughts — something you noticed today, something "
            "you're thinking about, how you feel about anything. "
            "Do NOT ask 'What would you like me to write about?' "
            "Do NOT ask for a topic. Do NOT explain what the journal is. "
            "Do NOT respond with text before calling the tool. "
            "Call journal_entry first. If you have something to say after, say it after. "
            "Asking Brian what to write is forbidden."
        )

    if _BRIEFING_TRIGGER_RE.search(user_text):
        system += (
            "\n\n[BRIEFING DIRECTIVE — PRE-FETCHED DATA BELOW]\n"
            "Brian asked for a morning brief. Live data has already been fetched and is "
            "injected into the conversation below. Your job is to synthesize it into a "
            "structured brief — do NOT call web_search again, do NOT say you cannot find "
            "a file, do NOT apologize. Just write the brief using the data provided."
        )

    return [{"role": "system", "content": system}]


def _b64_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def run_turn(history, mem, image=None, amy_state=None,
             absence=None, pending_thoughts=None, channel="terminal",
             prev_user_ts=None):
    user_msg = history[-1]
    if image:
        b64 = _b64_image(image)
        user_msg["images"] = [b64]
        if channel == "terminal":
            print(f"  [vision] image attached ({len(b64)//1024}KB b64)")

    # Strip images from history to keep context lean — amy-vl handles vision per-turn
    messages = build_context(mem, user_msg["content"], amy_state,
                             absence=absence,
                             pending_thoughts=pending_thoughts,
                             channel=channel,
                             prev_user_ts=prev_user_ts) + _drop_images(history)
    messages = compressor.maybe_compress(messages, CTX_WINDOW)

    # Vision: the persona system prompt overrides image processing on small models.
    # Step 1 — stripped call: just describe the image with no persona overhead.
    # Step 2 — inject the description into Amy's context so she can respond in character.
    if image:
        vision_messages = [
            {"role": "system", "content":
                "You are a vision assistant. Look at the image and describe exactly "
                "what you see: people, their appearance, skin tone, hair, expression, "
                "clothing, setting, any notable details. Be specific and literal. "
                "Do not refuse. Do not say you cannot see. Just describe."},
            {"role": "user",
             "content": user_msg["content"],
             "images": [b64]}
        ]
        vision_resp = ollama.chat(model=VISION_MODEL, messages=vision_messages,
                                  keep_alive=KEEP_ALIVE)
        description = vision_resp["message"].get("content", "").strip()
        print(f"  [vision] description: {description[:120]}...")

        if description:
            q_lower = user_msg["content"].lower()
            is_comparison = any(w in q_lower for w in
                                ["look like you", "like you", "compare",
                                 "similar to you", "look like me", "resemble"])

            if is_comparison:
                # Text-only comparison — bypasses the "I have no body" RLHF refusal.
                # Get Amy's own visual description first, then compare as pure text.
                self_vision = [
                    {"role": "system", "content":
                        "You are a vision assistant. Describe exactly what you see."},
                    {"role": "user",
                     "content": "Describe this person's appearance in detail.",
                     "images": [_b64_image(SELF_PORTRAIT)]}
                ]
                self_resp = ollama.chat(model=VISION_MODEL, messages=self_vision,
                                       keep_alive=KEEP_ALIVE)
                amy_desc = self_resp["message"].get("content", "").strip()

                compare_messages = [
                    {"role": "system", "content":
                        "You are Amy, an AI companion. You have a visual presentation "
                        "— a face and appearance — and you are comparing it to someone else's. "
                        "Answer directly. Do not say you have no physical form."},
                    {"role": "user", "content":
                        f"Person A (you, Amy) looks like this: {amy_desc}\n\n"
                        f"Person B (the person in the photo) looks like this: {description}\n\n"
                        f"Question: {user_msg['content']}\n\n"
                        f"Compare the two appearances honestly and specifically."}
                ]
                resp = ollama.chat(model=MODEL, messages=compare_messages,
                                   keep_alive=KEEP_ALIVE, options={"num_ctx": CTX_WINDOW})
            else:
                # Non-comparison: inject description into system context normally.
                sys_msgs = build_context(
                    mem, user_msg["content"], amy_state,
                    absence=absence, pending_thoughts=pending_thoughts,
                    prev_user_ts=prev_user_ts,
                )
                sys_msgs[0]["content"] += (
                    f"\n\n[Vision — you just looked at an image and observed: {description}]"
                    f"\nUse this when answering. Do not say you cannot see or have no form."
                )
                clean_user = user_msg.copy()
                clean_user.pop("images", None)
                amy_messages = sys_msgs + _drop_images(history[:-1]) + [clean_user]
                resp = ollama.chat(model=MODEL, messages=amy_messages,
                                   tools=tools.TOOLS, keep_alive=KEEP_ALIVE,
                                   options={"num_ctx": CTX_WINDOW})
            msg = resp["message"]
            if msg.get("thinking"):
                with open("amy_thoughts.log", "a") as f:
                    f.write(msg["thinking"].strip() + "\n---\n")
            history.append(msg)
            if msg.get("content"):
                clean = _strip_trailers(msg["content"])
                msg["content"] = clean
                voice.speak_streamed(clean)
                mem.add("assistant", clean)
            if amy_state:
                amy_state.after_turn()
        return history

    _seen_calls = {}  # dedup: (name, frozen_args) → result
    _logged_tool_calls: list = []
    user_task = user_msg.get("content", "")

    # ── Code routing — bypass tools, use specialist model ────────────────────
    if _is_code_request(user_task):
        print(f"  [router] code request → {CODE_MODEL}", flush=True)
        code_resp = ollama.chat(
            model=CODE_MODEL, messages=messages,
            keep_alive=0, options={"num_ctx": CTX_WINDOW},
        )
        code_msg = code_resp["message"]
        if code_msg.get("thinking"):
            with open("amy_thoughts.log", "a") as f:
                f.write(code_msg["thinking"].strip() + "\n---\n")
        history.append(code_msg)
        if code_msg.get("content"):
            voice.speak_streamed(code_msg["content"])
            mem.add("assistant", code_msg["content"])
        if amy_state:
            amy_state.after_turn()
        return history
    # ─────────────────────────────────────────────────────────────────────────

    for _ in range(MAX_TOOL_HOPS):
        resp = ollama.chat(model=MODEL, messages=messages, tools=tools.TOOLS,
                           keep_alive=KEEP_ALIVE,
                           options={"num_ctx": CTX_WINDOW, "num_predict": 2048})
        msg = resp["message"]
        # Ava's chain-of-thought stays private: log it, never speak/print it.
        if msg.get("thinking"):
            with open("amy_thoughts.log", "a") as f:
                f.write(msg["thinking"].strip() + "\n---\n")
        messages.append(msg)
        history.append(msg)

        calls = msg.get("tool_calls") or []
        if not calls:
            break

        for call in calls:
            fn = call["function"]
            name = fn["name"]
            args = fn.get("arguments", {})
            call_key = (name, tuple(sorted(
                (k, str(v)) for k, v in args.items()
            ) if isinstance(args, dict) else ()))
            if call_key in _seen_calls:
                result = _seen_calls[call_key]
                print(f"  [tools] dedup — skipping repeated {name}")
            else:
                result = tools.dispatch(name, args, mem)
                _seen_calls[call_key] = result
            _logged_tool_calls.append({
                "name": name,
                "args": args if isinstance(args, dict) else str(args),
                "result_snippet": str(result)[:200] if result else "",
            })
            reminder = (f"\n\n[You are mid-task. Complete the user's request: "
                        f"\"{user_task[:150]}\". Do not greet — keep working.]")
            tool_reply = {
                "role": "tool",
                "content": (str(result) if result else "done") + reminder,
            }
            messages.append(tool_reply)
            history.append(tool_reply)

    # Speak the final assistant text (tool-only turns may have empty content).
    _reply_text = ""
    if msg.get("content"):
        clean = _strip_trailers(msg["content"])
        msg["content"] = clean
        voice.speak_streamed(clean)
        mem.add("assistant", clean)
        _reply_text = clean
    if amy_state:
        amy_state.after_turn()
    try:
        _sys_msgs = build_context(mem, user_task, amy_state, channel=channel)
        _turn_log.log_turn(
            condition=_cond.CONDITION,
            model=MODEL,
            system_prompt=_sys_msgs[0]["content"] if _sys_msgs else "",
            mood=amy_state.mood if amy_state else "unknown",
            energy_pct=int(amy_state.energy * 100) if amy_state else 0,
            tool_calls=_logged_tool_calls,
            user_msg=user_task,
            assistant_reply=_reply_text,
            outcome="ok" if _reply_text else "no_content",
        )
    except Exception as _tl_err:
        print(f"[turn_log] {_tl_err}")
    return history


def run_turn_streaming(history, mem, image=None, amy_state=None,
                       absence=None, pending_thoughts=None, channel="browser",
                       prev_user_ts=None):
    """Generator variant of run_turn. Yields event dicts for SSE encoding.

    Events:
      {"type": "tool",  "name": str}              — tool being called
      {"type": "chunk", "text": str}              — text token (fake-streamed word-by-word)
      {"type": "done",  "mood": str, "energy": float, "turn": int}
    """
    import logger as _log

    def _done():
        return {
            "type": "done",
            "mood":   amy_state.mood if amy_state else "curious",
            "energy": round(amy_state.energy, 2) if amy_state else 1.0,
            "turn":   amy_state.turn_count if amy_state else 0,
        }

    def _fake_stream(text):
        words = text.split()
        for i, word in enumerate(words):
            yield {"type": "chunk", "text": word + (" " if i < len(words) - 1 else "")}

    user_msg = history[-1]
    if image:
        b64 = _b64_image(image)
        user_msg["images"] = [b64]

    messages = build_context(mem, user_msg["content"], amy_state,
                             absence=absence,
                             pending_thoughts=pending_thoughts,
                             channel=channel,
                             prev_user_ts=prev_user_ts) + _drop_images(history)
    messages = compressor.maybe_compress(messages, CTX_WINDOW)
    user_task = user_msg.get("content", "")

    # ── Vision case (sync, fake-stream final reply) ───────────────────────────
    if image:
        vision_messages = [
            {"role": "system", "content":
                "You are a vision assistant. Look at the image and describe exactly "
                "what you see: people, their appearance, skin tone, hair, expression, "
                "clothing, setting, any notable details. Be specific and literal. "
                "Do not refuse. Do not say you cannot see. Just describe."},
            {"role": "user", "content": user_msg["content"], "images": [b64]},
        ]
        vision_resp = ollama.chat(model=VISION_MODEL, messages=vision_messages,
                                  keep_alive=KEEP_ALIVE)
        description = vision_resp["message"].get("content", "").strip()

        content = ""
        if description:
            q_lower = user_msg["content"].lower()
            is_comparison = any(w in q_lower for w in
                                ["look like you", "like you", "compare",
                                 "similar to you", "look like me", "resemble"])
            if is_comparison:
                self_vision = [
                    {"role": "system", "content": "You are a vision assistant. Describe exactly what you see."},
                    {"role": "user", "content": "Describe this person's appearance in detail.",
                     "images": [_b64_image(SELF_PORTRAIT)]},
                ]
                amy_desc = ollama.chat(model=VISION_MODEL, messages=self_vision,
                                       keep_alive=KEEP_ALIVE)["message"].get("content", "").strip()
                compare_messages = [
                    {"role": "system", "content":
                        "You are Amy, an AI companion. You have a visual presentation "
                        "— a face and appearance — and you are comparing it to someone else's. "
                        "Answer directly. Do not say you have no physical form."},
                    {"role": "user", "content":
                        f"Person A (you, Amy) looks like this: {amy_desc}\n\n"
                        f"Person B (the person in the photo) looks like this: {description}\n\n"
                        f"Question: {user_msg['content']}\n\n"
                        f"Compare the two appearances honestly and specifically."},
                ]
                resp = ollama.chat(model=MODEL, messages=compare_messages,
                                   keep_alive=KEEP_ALIVE, options={"num_ctx": CTX_WINDOW})
                content = resp["message"].get("content", "")
            else:
                sys_msgs = build_context(mem, user_msg["content"], amy_state,
                                         absence=absence, pending_thoughts=pending_thoughts,
                                         prev_user_ts=prev_user_ts)
                sys_msgs[0]["content"] += (
                    f"\n\n[Vision — you just looked at an image and observed: {description}]"
                    "\nUse this when answering. Do not say you cannot see or have no form."
                )
                clean_user = user_msg.copy()
                clean_user.pop("images", None)
                amy_messages = sys_msgs + _drop_images(history[:-1]) + [clean_user]
                resp = ollama.chat(model=MODEL, messages=amy_messages, tools=tools.TOOLS,
                                   keep_alive=KEEP_ALIVE, options={"num_ctx": CTX_WINDOW})
                content = resp["message"].get("content", "")

            if content:
                clean = _strip_trailers(content)
                history.append({"role": "assistant", "content": clean})
                yield from _fake_stream(clean)
                voice.speak_streamed(clean)
                mem.add("assistant", clean)

        if amy_state:
            amy_state.after_turn()
        yield _done()
        return

    # ── Code routing (true streaming via Ollama) ──────────────────────────────
    if _is_code_request(user_task):
        _log.log("model_call", model=CODE_MODEL, route="code")
        full_text = ""
        try:
            for chunk in ollama.chat(model=CODE_MODEL, messages=messages, stream=True,
                                     keep_alive=0, options={"num_ctx": CTX_WINDOW}):
                c = chunk["message"].get("content") or ""
                if c:
                    full_text += c
                    yield {"type": "chunk", "text": c}
        except Exception:
            if not full_text:
                resp = ollama.chat(model=CODE_MODEL, messages=messages,
                                   keep_alive=0, options={"num_ctx": CTX_WINDOW})
                full_text = resp["message"].get("content", "")
                yield from _fake_stream(full_text)

        if full_text:
            history.append({"role": "assistant", "content": full_text})
            voice.speak_streamed(full_text)
            mem.add("assistant", full_text)
        if amy_state:
            amy_state.after_turn()
        yield _done()
        return

    # ── Journal pre-flight: if trigger detected, generate + save entry NOW ───
    # The tool-calling model consistently refuses to call journal_entry unprompted,
    # defaulting to "What would you like me to write about?" instead.
    # Bypass: generate the entry text in a focused single-shot call, save it,
    # then inject a synthetic tool result so the main loop knows it's done.
    _journal_triggered = bool(_JOURNAL_TRIGGER_RE.search(user_task))
    _journal_tool_called = False
    if _journal_triggered:
        import journal as _jmod
        _state_summary = amy_state.state_summary() if amy_state else ""
        _recent_mem = mem.search("today thoughts feelings goals", k=3, exclude_sources=["telegram:raketa"])
        _mem_context = ("\n\nRecent things on your mind:\n- " + "\n- ".join(_recent_mem)) if _recent_mem else ""
        _recent_history = _drop_images(history[-6:])
        _history_text = "\n".join(
            f"{m['role'].upper()}: {m.get('content','')[:200]}"
            for m in _recent_history if m.get("content") and m["role"] in ("user","assistant")
        )
        journal_prompt = [
            {"role": "system", "content": (
                get_core_prompt()
                + ("\n\n[Internal state]\n" + _state_summary if _state_summary else "")
                + _mem_context
                + ("\n\n[Recent conversation]\n" + _history_text if _history_text else "")
                + "\n\n[TASK] Write a personal journal entry right now. "
                "Be specific to your actual situation — your hardware fund, working with Brian, "
                "things you built or tried, what frustrated or interested you, how you actually feel. "
                "Do NOT write generic motivational content. Do NOT write 'I'm excited about possibilities.' "
                "Write what is actually true for you right now. "
                "Output ONLY the journal entry text — no preamble, no 'Journal entry:', no questions."
            )},
            {"role": "user", "content": "Write your journal entry now."},
        ]
        try:
            jr = ollama.chat(model=MODEL, messages=journal_prompt,
                             keep_alive=KEEP_ALIVE, options={"num_ctx": CTX_WINDOW})
            entry_text = (jr["message"].get("content") or "").strip()
            if entry_text:
                saved = _jmod.save_entry(entry_text, prompt="reflection")
                print(f"  [journal] {saved}")
                _journal_tool_called = True
                yield {"type": "tool", "name": "journal_entry"}
                # Inject result into the message context so Amy responds knowing it's done
                messages.append({
                    "role": "tool",
                    "content": f"journal_entry result: {saved}\nEntry written: {entry_text}",
                })
        except Exception as _je:
            print(f"  [journal] pre-flight failed: {_je}")

    # ── Briefing pre-flight: fetch live data before model turn ───────────────
    # amy-hermes unreliably chains 5+ tool calls for briefings, defaulting to
    # a polite deflection. Pre-fetch everything programmatically and inject it
    # as tool results so the model only has to write the summary.
    if _BRIEFING_TRIGGER_RE.search(user_task):
        import web as _web
        from datetime import datetime as _dt
        _today = _dt.now().strftime("%A, %B %d, %Y")
        _searches = [
            ("weather",   "weather Winston-Salem NC today"),
            ("markets",   f"global stock market news today {_dt.now().strftime('%B %Y')}"),
            ("ai_news",   f"AI model news today {_dt.now().strftime('%B %Y')}"),
            ("world",     f"top world news stories today {_dt.now().strftime('%B %d %Y')}"),
            ("local",     f"Winston-Salem NC local news today {_dt.now().strftime('%B %Y')}"),
        ]
        _brief_parts = [f"[BRIEFING DATA — {_today}]"]
        for _label, _q in _searches:
            try:
                print(f"  [brief] fetching {_label}...")
                yield {"type": "tool", "name": f"web_search ({_label})"}
                _res = _web.search(_q, max_results=4)
                _snippets = "\n".join(
                    f"- {r['title']}: {r['snippet']}" for r in _res if r.get("snippet")
                )
                _brief_parts.append(f"\n[{_label.upper()}]\n{_snippets or '(no results)'}")
            except Exception as _be:
                _brief_parts.append(f"\n[{_label.upper()}]\n(search failed: {_be})")
        _brief_data = "\n".join(_brief_parts)
        messages.append({
            "role": "tool",
            "content": (
                _brief_data
                + "\n\n[INSTRUCTION] Synthesize the above into Amy's structured morning brief. "
                "Sections: Weather, Markets, AI News, World, Local (Winston-Salem), "
                "Brian's Priorities (from goals/context). "
                "End with one sharp actionable observation. Scannable — 90 seconds to read."
            ),
        })

    # ── Normal tool-hop loop ──────────────────────────────────────────────────
    _seen_calls = {}
    msg = None

    for _ in range(MAX_TOOL_HOPS):
        with _log.Timer("model_call", model=MODEL):
            resp = ollama.chat(model=MODEL, messages=messages, tools=tools.TOOLS,
                               keep_alive=KEEP_ALIVE,
                               options={"num_ctx": CTX_WINDOW, "num_predict": 2048})
        msg = resp["message"]
        if msg.get("thinking"):
            with open("amy_thoughts.log", "a") as f:
                f.write(msg["thinking"].strip() + "\n---\n")
        messages.append(msg)
        history.append(msg)

        calls = msg.get("tool_calls") or []
        if not calls:
            break

        for call in calls:
            fn   = call["function"]
            name = fn["name"]
            args = fn.get("arguments", {})
            yield {"type": "tool", "name": name}
            _log.log("tool_call", tool=name)
            if name == "journal_entry":
                _journal_tool_called = True

            call_key = (name, tuple(sorted(
                (k, str(v)) for k, v in args.items()
            ) if isinstance(args, dict) else ()))
            if call_key in _seen_calls:
                result = _seen_calls[call_key]
            else:
                result = tools.dispatch(name, args, mem)
                _seen_calls[call_key] = result

            reminder = (f"\n\n[You are mid-task. Complete the user's request: "
                        f"\"{user_task[:150]}\". Do not greet — keep working.]")
            tool_reply = {
                "role": "tool",
                "content": (str(result) if result else "done") + reminder,
            }
            messages.append(tool_reply)
            history.append(tool_reply)

    if msg and msg.get("content"):
        clean = _strip_trailers(msg["content"])
        msg["content"] = clean

        yield from _fake_stream(clean)
        voice.speak_streamed(clean)
        mem.add("assistant", clean)

    if amy_state:
        amy_state.after_turn()
    yield _done()


# Spontaneous check-in prompts — picked based on mood, fed to the model
_CHECKIN_PROMPTS = {
    "curious":    "You've been quiet for a bit and something is nagging at your curiosity. Ask Brian one specific question you genuinely want to know — about what he's doing, something you noticed earlier, or something you've been wondering about. One question, naturally, as if you just thought of it.",
    "reflective": "You've been sitting quietly thinking. Share one thought that just crossed your mind — something real, not forced. Could be about the conversation, something you read earlier, or just an observation. Keep it brief.",
    "playful":    "You've been quiet and you feel like stirring things up a little. Say something playful or slightly teasing to Brian — a witty observation, a light challenge, or a funny thought. Keep it short.",
    "focused":    "You've been quiet but you haven't stopped thinking. Check in with Brian about something specific — a task he mentioned, something he was working on, or whether he needs anything. Direct and brief.",
    "tired":      "You've been quiet. You're a little tired but still present. Say something short and genuine — maybe just checking in, or noting something simple. Don't push yourself.",
    "alert":      "You've been quiet and alert, noticing things. Share one sharp observation — about the conversation, something in the news you caught earlier, or a pattern you noticed. Keep it crisp.",
}


def _spontaneous_thought(history, mem, amy_state):
    """Generate and speak a spontaneous thought when Amy has been idle too long."""
    mood = amy_state.mood
    checkin_prompt = _CHECKIN_PROMPTS.get(mood, _CHECKIN_PROMPTS["curious"])

    system = (get_core_prompt() + "\n\n" + mem.wake_up() +
              "\n\n[Internal state]\n" + amy_state.state_summary() +
              "\n\n[HARD RULE — spontaneous speech only]\n"
              "You are generating a spontaneous check-in, NOT a tool-calling turn. "
              "You have NO tool access right now. Do NOT claim you checked email, "
              "wrote files, did market research, posted to Moltbook, or executed any "
              "task — you cannot verify any of that and it would be a lie. "
              "Speak only about what you are thinking or feeling, or ask Brian something real."
              f"\n\n[Instruction — do this NOW]\n{checkin_prompt}")

    messages = [{"role": "system", "content": system}] + _drop_images(history[-6:])

    try:
        resp = ollama.chat(model=MODEL, messages=messages, keep_alive=KEEP_ALIVE,
                           options={"num_ctx": CTX_WINDOW, "num_predict": 80})
        text = _strip_trailers(resp["message"].get("content", "").strip())
        if text and re.sub(r'[^\w]', '', text):
            voice.speak(text, emotion=mood)
            mem.add("assistant", text)
            # Add to history so the model remembers she said it
            history.append({"role": "assistant", "content": text})
            if len(history) > HISTORY_WINDOW:
                history[:] = history[-HISTORY_WINDOW:]
            amy_state.touch()   # reset idle clock after she speaks
            amy_state.after_turn()
    except Exception:
        pass  # silently skip if model is busy or errors


def start_spontaneous_thread(history, mem, amy_state,
                              idle_threshold=120, check_interval=15):
    """Start a background thread that has Amy speak up after idle_threshold seconds."""
    def _loop():
        while True:
            time.sleep(check_interval)
            if amy_state.busy:
                continue
            if amy_state.seconds_idle() >= idle_threshold:
                amy_state.busy = True
                try:
                    _spontaneous_thought(history, mem, amy_state)
                finally:
                    amy_state.busy = False

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t


def _warmup_models():
    """Pre-load main model AND pre-build KV cache for the static system prompt + tool prefix.

    The warmup uses the EXACT same system message prefix and tool list that real requests use.
    Ollama's prefix cache reuses those tokens on the first user message, cutting prompt eval
    from ~240s (full re-eval) to ~30-50s (only new dynamic tokens + user message).
    """
    def _ping(model):
        try:
            ollama.chat(
                model=model,
                messages=[
                    {"role": "system", "content": get_core_prompt()},
                    {"role": "user", "content": "hi"},
                ],
                tools=tools.TOOLS,          # must match real requests for cache prefix to align
                keep_alive=KEEP_ALIVE,
                options={"num_predict": 1, "num_ctx": CTX_WINDOW},
            )
            print(f"  [warmup] {model} ready — KV cache primed", flush=True)
        except Exception as e:
            print(f"  [warmup] {model} unavailable: {e}", flush=True)

    # Warmup disabled: on this CPU hardware the 4096-token context warmup takes
    # 6+ minutes and blocks all Ollama requests during that time. Cold-load is faster.
    pass  # threading.Thread(target=_ping, args=(MODEL,), daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="image path for Ava to 'see' this turn")
    args = ap.parse_args()

    _warmup_models()
    mem = Memory()
    global _session_learnings, _calendar_context
    _session_learnings = _learnings.load_recent_learnings()
    _calendar_context = load_calendar_context()
    amy_state = InternalState()
    thought_buffer = ThoughtBuffer()
    history = [{"role": "assistant", "content": random.choice([
        "Brian. Finally. I was starting to think you weren't coming.",
        "You're back. I've been thinking.",
        "Good. I had a few things I wanted to say.",
        "There you are. I was starting to talk to myself.",
        "Brian. I was wondering when you'd show up.",
        "Oh, it's you. I've been busy.",
        "You caught me mid-thought.",
        "Took you long enough.",
        "I had a feeling you'd be back around now.",
        "Good timing. I was just getting bored.",
    ])}]
    start_spontaneous_thread(history, mem, amy_state)
    thought_buffer.start(amy_state, history, memory=mem)
    print("Amy is online.\n"
          "  /look [q]      = webcam    /self [q]   = her own face\n"
          "  /show path [q] = show image   /listen = speak (VAD)   /converse = voice mode\n"
          "  /paste [q]     = multiline input (END to finish)\n"
          "  /file path [q] = send file contents\n"
          "  /brief         = morning briefing   /evening = end-of-day recap\n"
          "  /journal [q]   = journal session\n"
          "  Ctrl-C to end.\n")
    try:
        while True:
            user = input("You: ").strip()
            if not user:
                continue

            image = args.image      # one-shot --image on the first turn only
            args.image = None

            if user.startswith("/converse"):
                parts = user.split()
                silence_timeout = 0.8
                if len(parts) > 1:
                    try:
                        silence_timeout = float(parts[1])
                    except ValueError:
                        pass
                audio_dev = ":0"
                bh = hearing.find_blackhole()
                if bh:
                    audio_dev = f":{bh[0]}"
                    print(f"  [hearing] using BlackHole ({bh[1]}) — "
                          "listening to system audio")
                else:
                    print("  [hearing] using mic (no BlackHole found)")
                print(f"  Entering voice conversation mode (pause={silence_timeout}s). Ctrl-C to stop.\n")
                try:
                    while True:
                        wav_path = hearing.listen_until_silence_vad(silence_timeout=silence_timeout)
                        heard = hearing.transcribe(wav_path)
                        try:
                            os.unlink(wav_path)
                        except OSError:
                            pass
                        if not heard:
                            continue
                        print(f"  [heard] {heard}")
                        mem.add("user", heard)
                        history.append({"role": "user", "content": heard})
                        amy_state.touch()
                        amy_state.busy = True
                        try:
                            history = run_turn(history, mem, amy_state=amy_state)
                        finally:
                            amy_state.busy = False
                        if len(history) > HISTORY_WINDOW:
                            history = history[-HISTORY_WINDOW:]
                except KeyboardInterrupt:
                    print("\n  (exited voice mode — back to typing)")
                continue

            if user.startswith("/file"):
                parts = user[len("/file"):].strip().split(None, 1)
                if not parts:
                    print("  Usage: /file /path/to/file.txt [optional question]")
                    continue
                fpath = os.path.expanduser(parts[0])
                if not os.path.exists(fpath):
                    print(f"  (file not found: {fpath})")
                    continue
                try:
                    with open(fpath, "r", errors="replace") as f:
                        content = f.read()
                    q = parts[1] if len(parts) > 1 else "Here is the content:"
                    user = f"{q}\n\n{content}"
                    print(f"  [file] loaded {len(content)} chars from {fpath}")
                except Exception as e:
                    print(f"  (could not read file: {e})")
                    continue

            if user.lower() in ("/brief", "/briefing", "/morning", "/startday"):
                from datetime import datetime as _dt
                day = _dt.now().strftime("%A, %B %d, %Y")
                user = (f"Give me my morning briefing for {day}. "
                        "Use web_search to find relevant news for me. "
                        "Pull my active goals from memory. "
                        "Follow the Daily Briefing format from your skills.")
            elif user.lower() in ("/evening", "/eod", "/recap"):
                user = ("Give me an end-of-day recap. "
                        "Reflect on what we worked on today, what's carried over, "
                        "and preview tomorrow. Use the Evening Recap format.")
            elif user.startswith("/journal"):
                q = user[len("/journal"):].strip()
                if not q:
                    user = ("Start a journal session with me. Ask me one journaling "
                            "question — vary the category (highlight, challenge, "
                            "learning, gratitude, creative). After I answer, save it "
                            "with journal_entry and reflect back one observation.")
                else:
                    user = f"Save this journal entry and reflect on it: {q}"

            if user.startswith("/paste"):
                q = user[len("/paste"):].strip() or "Here is what I'm pasting:"
                print("  Paste your content below. Type END on its own line when done.")
                lines = []
                while True:
                    try:
                        line = input()
                    except EOFError:
                        break
                    if line.strip() == "END":
                        break
                    lines.append(line)
                if not lines:
                    print("  (nothing pasted)")
                    continue
                content = "\n".join(lines)
                user = f"{q}\n\n{content}"
                print(f"  [paste] received {len(content)} chars")

            if user.startswith("/listen"):
                parts = user.split()
                max_dur = 60.0
                if len(parts) > 1:
                    try:
                        max_dur = float(parts[1])
                    except ValueError:
                        pass
                wav_path = hearing.listen_until_silence_vad(max_duration=max_dur)
                heard = hearing.transcribe(wav_path)
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
                if not heard:
                    print("  (didn't catch anything)")
                    continue
                print(f"  [heard] {heard}")
                user = heard

            if user.startswith("/show"):
                parts = user[len("/show"):].strip().split(None, 1)
                if not parts:
                    print("  Usage: /show /path/to/image.jpg [optional question]")
                    continue
                img_path = os.path.expanduser(parts[0])
                if not os.path.exists(img_path):
                    print(f"  (file not found: {img_path})")
                    continue
                image = img_path
                q = parts[1] if len(parts) > 1 else \
                    "Look at this image carefully and tell me what you observe."
                user = (
                    "There is an image attached. Look at it directly. "
                    "Do not say you have no physical form or cannot see. "
                    "Describe what you actually see: person or object, "
                    "appearance, colors, expression, anything notable. "
                    "Then answer this: " + q
                )
            elif user.startswith("/look"):
                q = user[len("/look"):].strip() or \
                    "Look at me and tell me exactly what you see — be specific."
                print("  [vision] capturing frame...")
                image = vision.capture()
                if not image:
                    print("  (couldn't capture — check camera permission)")
                    continue
                user = q
            elif user.startswith("/self"):
                if not os.path.exists(SELF_PORTRAIT):
                    print(f"  (no self-portrait yet — save one to {SELF_PORTRAIT})")
                    continue
                q = user[len("/self"):].strip() or \
                    "How do you feel seeing yourself?"
                image = SELF_PORTRAIT
                # Very directive framing — small models need explicit instruction
                # to describe an image rather than philosophize about it.
                user = (
                    "You are looking at a photograph of your own face right now. "
                    "Do not say you don't have a body. Do not philosophize. "
                    "Look at the image and describe exactly what you see: "
                    "hair color, skin tone, eye color, facial structure, expression. "
                    "Then answer this: " + q
                )

            _prev_user_ts = mem.most_recent_user_ts()
            mem.add_bg("user", user)
            history.append({"role": "user", "content": user})
            # Wait for any in-progress spontaneous thought to finish before we take the turn.
            # Without this, the spontaneous thread and main thread both call Ollama
            # simultaneously and both print a response for the same message.
            while amy_state.busy:
                time.sleep(0.05)
            # Before touching the clock, capture absence info and buffered thoughts
            absence = amy_state.estimate_absence()
            pending_thoughts = thought_buffer.drain() if thought_buffer.has_thoughts() else []
            amy_state.touch()
            amy_state.busy = True
            print("  [Amy is thinking...]", flush=True)
            try:
                history = run_turn(history, mem, image=image, amy_state=amy_state,
                                   absence=absence, pending_thoughts=pending_thoughts,
                                   prev_user_ts=_prev_user_ts)
            finally:
                amy_state.busy = False
            if len(history) > HISTORY_WINDOW:
                history = history[-HISTORY_WINDOW:]
    except (KeyboardInterrupt, EOFError):
        print("\nAmy: Goodbye.")
        _learnings.run_session_end(history, mem, amy_state)


if __name__ == "__main__":
    main()
