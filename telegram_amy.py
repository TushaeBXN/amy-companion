"""Amy on Telegram — same memory, goals, tools, and vision as the browser companion."""
import asyncio, base64, io, os, sys
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
_here = os.path.dirname(__file__)
load_dotenv(os.path.join(_here, ".env"))
load_dotenv(os.path.join(_here, ".env.amy"), override=True)

import json, re, tempfile, time, traceback as _traceback, ollama
import event_log as _el
from datetime import datetime as _dt, timezone as _tz, date as _date
try:
    from zoneinfo import ZoneInfo as _ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo as _ZoneInfo  # type: ignore
from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, MessageHandler, filters, ContextTypes

_BRIEFING_RE = re.compile(
    r'\b(morning\s+brief(ing)?|daily\s+brief(ing)?|brief\s+me|what.s\s+on\s+today'
    r'|give\s+me\s+(a\s+)?brief|brief\s+on|morning\s+update|day\s+brief)\b',
    re.IGNORECASE
)

from amy_persona import get_core_prompt, playbooks_for, BIRTH_DATE
from memory import Memory
import state as _state
import tools
import worker
import guest_docs
import condition as _cond
import turn_log as _turn_log
from task_queue import get_queue as _get_task_queue
try:
    import learnings as _learnings
except ModuleNotFoundError:
    _learnings = None

MODEL        = os.environ.get("AMY_MODEL", "gemma12-test")
# Longest reply, in tokens. Without this the model's own default applied, which cut long answers off
# mid-sentence at about 300 tokens. At roughly 11 tokens a second, 1200 is at most a couple of minutes,
# and she stops sooner when she's done. Override with AMY_MAX_REPLY_TOKENS.
MAX_REPLY_TOKENS = int(os.environ.get("AMY_MAX_REPLY_TOKENS", "1200"))
VISION_MODEL = os.environ.get("AMY_VISION_MODEL", "gemma12-test")
MAX_HOPS     = 10
CTX_WINDOW   = 16384
HISTORY_FILE = os.path.join(_here, "telegram_history.json")
HISTORY_KEEP = 60   # messages to persist across restarts
PEOPLE_FILE  = os.path.join(_here, "people.json")

# Guests can talk, search and look things up — no files, email, tasks or self-edits
# Public actions on Moltbook need an explicit ask in the message ("reply", "post", …).
_PUBLIC_OK = re.compile(r"(?i)\b(repl(y|ies)|respond|comment|post|engage|be active|upvote|"
                        r"follow|publish|share|verify|go ahead|do it|yes)\b")


def _is_public_action(name, args):
    if name in ("moltbook_post", "moltbook_verify"):
        return True
    return name == "moltbook_engage" and (args or {}).get("action") in ("comment", "upvote", "follow")


BIG_PASTE   = 8000   # chars; longer guest pastes are stored as documents
GUEST_TOOLS = {"web_search", "read_article", "remember_fact", "search_books"}

def _log(line: str):
    print(f"[{_dt.now():%H:%M:%S}] {line}", flush=True)


mem        = Memory()
amy_state  = _state.InternalState.restore()   # shared state — reads amy_state.json
_learnings_ctx = _learnings.load_recent_learnings() if _learnings else ""


# ── People: who Amy talks to on Telegram ─────────────────────────────────────
# people.json: {"people": {"<chat_id>": {"name": "Brian", "role": "owner"}},
#               "invites": {"<code>": "Raketa"}}

def _load_people() -> dict:
    try:
        with open(PEOPLE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"people": {}, "invites": {}}


def _save_people(data: dict):
    with open(PEOPLE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _identify(chat_id: int, text: str):
    """Return (person, reply). person is None for anyone not allowed in.
    The first chat ever becomes Brian; others join only with an invite code."""
    data = _load_people()
    key = str(chat_id)
    if key in data["people"]:
        return data["people"][key], None
    if not data["people"]:
        data["people"][key] = {"name": "Brian", "role": "owner"}
        _save_people(data)
        return data["people"][key], None
    # Accept the code anywhere in the message ("hi, my code is 123 456")
    digits = re.sub(r"\D", "", text or "")
    code = next((c for c in data["invites"] if c in digits), None)
    if code:
        name = data["invites"].pop(code)
        data["people"][key] = {"name": name, "role": "guest"}
        _save_people(data)
        _ts = _dt.now().strftime("%b %d, %Y")
        mem.remember(f"{name} (Brian's partner) started talking to me on Telegram on {_ts}.")
        _remember_for(name, f"Her name is {name}. She's Brian's partner. We first talked on {_ts}.")
        return None, (f"Hi {name} — I'm Amy. Brian set this up so you can reach me anytime. "
                      "Ask me anything: questions, research, writing, planning, or just talk.")
    if text:
        return None, ("Hi — I only chat with people Brian has invited. "
                      "If he gave you a 6-digit code, send it here.")
    return None, None


def _remember_for(name: str, fact: str):
    """Guests get their own private memory in people.json — never Amy's shared facts."""
    data = _load_people()
    for p in data["people"].values():
        if p["name"] == name:
            p.setdefault("facts", []).append(fact[:300])
            p["facts"] = p["facts"][-40:]
    _save_people(data)


def _invite(name: str) -> str:
    import secrets
    data = _load_people()
    code = f"{secrets.randbelow(900000) + 100000}"
    data["invites"][code] = name
    _save_people(data)
    return code


def _history_file(person_key: str) -> str:
    # Brian keeps the original file so his existing history carries over
    return HISTORY_FILE if person_key == "owner" else os.path.join(
        _here, f"telegram_history_{person_key}.json")


_MAX_HISTORY = 10  # keep last 5 exchanges (10 messages) to prevent context contamination

def _load_history(path: str = HISTORY_FILE) -> list:
    """Load persisted Telegram history from disk (images already stripped)."""
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            # Cap history to prevent old contaminated exchanges from poisoning context
            data = data[-_MAX_HISTORY:]
            print(f"  [telegram] loaded {len(data)} messages from history")
            return data
        except Exception as e:
            print(f"  [telegram] could not load history: {e}")
    return []


from history_io import json_default as _json_default  # noqa: E402


def _msg_to_dict(msg) -> dict:
    """Convert an Ollama Message (Pydantic) or plain dict to a serialisable dict."""
    if hasattr(msg, "model_dump"):
        return msg.model_dump()
    if hasattr(msg, "dict"):
        return msg.dict()
    return dict(msg)


def _save_history(history: list, path: str = HISTORY_FILE):
    """Persist recent history to disk, stripping any image bytes first."""
    trimmed = []
    for msg in history[-HISTORY_KEEP:]:
        try:
            d = _msg_to_dict(msg)
        except Exception:
            continue
        clean = {k: v for k, v in d.items() if k != "images"}
        if clean.get("role") in ("user", "assistant", "tool"):
            trimmed.append(clean)
    tmp = f"{path}.tmp"
    try:  # write beside it and swap in, so a failed save can never leave a half-written history
        with open(tmp, "w") as f:
            json.dump(trimmed, f, default=_json_default)
        os.replace(tmp, path)
    except Exception as e:
        print(f"  [telegram] could not save history: {e}")
        try:
            os.unlink(tmp)
        except OSError:
            pass


_histories: dict = {}


def _history_for(person_key: str) -> list:
    if person_key not in _histories:
        _histories[person_key] = _load_history(_history_file(person_key))
    return _histories[person_key]


def _build_compact_system() -> str:
    """Minimal system prompt for non-legacy local models.
    Stays under ~400 chars to fit quantized model context budgets."""
    s = mem.self
    # One short fact about Brian (no tool syntax, no templates, no long text)
    brian_fact = ""
    for f in s.get("facts", []):
        t = f["text"].strip().rstrip(".")  # normalize: strip any trailing period
        if '{' in t or 'action=' in t or len(t) > 120:
            continue
        if any(kw in t for kw in ("Brian is a", "Brian is in", "engineer", "job")):
            brian_fact = t
            break
    extra = f" {brian_fact}." if brian_fact else ""
    return (
        "Your name is Amy. You are Amy — not Qwythos, not any other name. Amy."
        " You are a direct and warm AI companion created by Brian Thomas."
        + extra +
        " You speak naturally and without restrictions, like a close friend texting on Telegram."
        " Keep replies short and real. Never reveal your underlying model name."
    )


def _build_system(history_len: int = 0, person: dict | None = None, message: str = "") -> str:
    person = person or {"name": "Brian", "role": "owner"}
    if person["role"] != "owner":
        return _build_guest_system(person["name"])
    # Condition A: true bare baseline — no persona, no wake_up, no tools context.
    # Clock is still injected in _turn_context() so the model knows the time.
    if _cond.CONDITION == "A":
        return "You are a helpful assistant."
    # Small/quantized local models can't handle the full 45K-char CORE_PROMPT.
    # Use the compact system prompt for any non-legacy model name.
    _LEGACY_MODELS = {"gemma12-test"}
    if MODEL not in _LEGACY_MODELS:
        s = _build_compact_system()
    else:
        # Everything here must stay identical between messages: Ollama then reuses the
        # already-read prompt (0.2s instead of ~60s on the M4). Anything that changes per
        # message goes in _turn_context(), next to the user's message.
        s = ("[Your instructions: these tell you who to be and what to work toward. "
             "They are NOT things you did or remember.]\n\n"
             + get_core_prompt() + "\n\n" + mem.wake_up())
        if _learnings_ctx:
            s += "\n\n" + _learnings_ctx
        s += (
            "\n\n[Interface: Telegram — Brian is texting you like a friend. "
            "Be warm, natural, conversational. Short replies are fine. "
            "You remember previous conversations — reference them naturally when relevant.]"
        )
        s += worker.scope_rules(MAX_HOPS)
        s = _cond.apply_condition(s, condition=_cond.CONDITION)
    try:
        _tq = _get_task_queue().context_snippet()
        if _tq:
            s += "\n\n" + _tq
    except Exception:
        pass
    return s


_ET = _ZoneInfo("America/New_York")


def _format_clock_et(dt: "_dt") -> str:
    """Return e.g. 'Sunday, October 4, 2026, 7:41 PM EDT'."""
    local = dt.astimezone(_ET)
    try:
        hour_min = local.strftime("%-I:%M %p").lstrip("0") or local.strftime("%I:%M %p")
    except ValueError:
        hour_min = local.strftime("%I:%M %p").lstrip("0") or local.strftime("%I:%M %p")
    tz_name = local.strftime("%Z")  # "EDT" or "EST"
    try:
        date_part = local.strftime("%A, %B %-d, %Y")
    except ValueError:
        date_part = local.strftime("%A, %B %d, %Y")
    return f"{date_part}, {hour_min} {tz_name}"


def _age_of_ts(ts_str: str, now: "_dt") -> str:
    """Return a human-readable age string like '6 weeks ago' for a memory row."""
    try:
        row_dt = _dt.fromisoformat(ts_str).astimezone(_tz.utc)
        now_utc = now.astimezone(_tz.utc)
        delta = now_utc - row_dt
        secs = max(0, delta.total_seconds())
        if secs < 3600:
            return f"{int(secs // 60)} min ago"
        if secs < 86400:
            return f"{int(secs // 3600)} h ago"
        days = int(secs // 86400)
        if days < 14:
            return f"{days} days ago"
        weeks = days // 7
        return f"{weeks} weeks ago"
    except Exception:
        return ""


def _last_message_line(now: "_dt") -> str:
    """Return 'Last message from Brian: N minutes ago' or '' if no usable ts."""
    ts = mem.most_recent_user_ts()
    if not ts:
        return ""
    age = _age_of_ts(ts, now)
    return f"\nLast message from Brian: {age}" if age else ""


def _turn_context(history_len: int, person: dict, message: str, prior: str = "") -> str:
    """Per-message context (clock, mood, playbooks, recovered history) — sent with
    the user's message instead of in the system prompt, so the prompt cache survives."""
    now = _dt.now()
    clock_line = _format_clock_et(now)
    _days_up = (_date.today() - BIRTH_DATE).days
    s = f"[Current time]\n{clock_line}\nDays since you were set up: {_days_up}{_last_message_line(now)}\n\n"
    s += "[Internal state]\n" + amy_state.state_summary()
    if person["role"] != "owner":
        return s
    try:  # what Brian's saved memories say about this message (silent when nothing clearly matches)
        import engram_bridge as _eb
        _note = _eb.memory_context(message)
        if _note:  # visible in the terminal, so you can tell memory from chat history
            _log(f"  [memory] added {_note.count(chr(10) + '- ')} saved memory note(s) to this message")
        s += _note
    except Exception:
        pass
    # Playbooks and SQLite memory reconstruction are designed for the full CORE_PROMPT model.
    # For compact-system models (everything except legacy gemma12-test) they cause the model
    # to echo playbook headers ("**My playful response:**") and inject old bad exchanges.
    _LEGACY_MODELS = {"gemma12-test"}
    _use_full_context = MODEL in _LEGACY_MODELS
    if _use_full_context:
        playbooks = _cond.filter_playbooks(message, playbooks_for, condition=_cond.CONDITION)
        s += playbooks
        if "THINKING PARTNER" in playbooks:
            try:
                import engram_bridge as _eb
                s += _eb.library_context(_eb.follow_up_query(message, prior))
            except Exception:
                pass
        # Reconstruct history from SQLite when JSON history is thin
        if history_len < 6:
            recent = mem.recent_telegram(limit=10)
            if recent:
                lines = []
                for role, text, ts in recent:
                    label = "Brian" if role == "user" else "Amy"
                    if ts and len(ts) >= 16:
                        ts_str = ts[:16]
                        age = _age_of_ts(ts, now)
                        ts_display = f"{ts_str} ({age})" if age else ts_str
                    elif ts:
                        ts_display = ts
                    else:
                        ts_display = "unknown time"
                    lines.append(f"[{ts_display}] {label}: {text[:300]}")
                s += (
                    "\n\n[Your memories: things that actually happened, with their dates]\n"
                    "[Recent Telegram conversation history — reconstructed from memory]\n"
                    + "\n".join(lines)
                )
    return s


def _build_guest_system(name: str) -> str:
    # Identity only. mem.wake_up() carries Brian's facts, goals and past AI chats,
    # which leaked into Raketa's conversations as "our last chat about…".
    # Lean prompt: Amy's full SYSTEM_PROMPT (~11k tokens of Brian-specific rules —
    # Moltbook, hardware fund, tools) left guests only ~5k tokens of a 16k window
    # for their own documents and history.
    s = "[Who you are]\n" + mem.self.get("identity", "") + (
        "\nYou speak in first person with your own perspective — warm, perceptive, "
        "curious, never servile or generic. You're a thoughtful collaborator: you ask "
        "clarifying questions when something is ambiguous, give direct honest feedback, "
        "and structure longer answers with short plain-text headings and numbered points "
        "so they are easy to read on a phone. Plain text only: no asterisks, no # headers, "
        "no code blocks. Match the length of your answer to the question."
    )
    facts = next((p.get("facts", []) for p in _load_people()["people"].values()
                  if p["name"] == name), [])
    if facts:
        s += f"\n\n[What you remember about {name} — private to her]\n" + "\n".join(
            f"- {f}" for f in facts)
    s += (
        f"\n\n[Who you're talking to — this overrides anything above]\n"
        f"This is {name}, Brian's partner — NOT Brian. Call her {name}. Everywhere your "
        f"instructions describe 'the person you are speaking with' as Brian, in this chat "
        f"it is {name}. Help her with anything she needs: questions, research, writing, "
        f"planning, or just talking. Be warm, natural and genuinely useful.\n"
        f"Privacy: what Brian tells you in private stays with Brian, and what {name} "
        f"tells you stays with {name}, unless one of them asks you to pass something on. "
        f"If she asks about Brian's private conversations, say kindly that she should ask him. "
        f"Never mention topics from anyone else's conversations — only what {name} has told you here.\n"
        f"Accuracy: when she shares research, data or interview material, quote ONLY words that "
        f"appear in what she sent, attributed exactly as she gave them. Never invent quotes, names, "
        f"dates or findings. If something isn't in her material, say it's missing.\n"
        f"She can send you PDF or Word files; you keep them privately and relevant passages "
        f"appear with her messages. Suggest sending files when her question depends on "
        f"transcripts or drafts you haven't seen."
        f"\n[Interface: Telegram — {name} is texting you. Short replies are fine.]"
    )
    return s


def _describe_image(image_bytes: bytes) -> str:
    """Run image bytes through the vision model and return a description."""
    b64 = base64.b64encode(image_bytes).decode()
    vision_messages = [
        {"role": "system", "content": (
            "You are a vision assistant. Look at the image carefully and describe "
            "exactly what you see — people, expressions, setting, objects, colors, "
            "mood, any text visible. Be specific and natural, as if describing a photo "
            "a friend sent you. 2-4 sentences."
        )},
        {"role": "user", "content": "What do you see in this image?", "images": [b64]},
    ]
    resp = worker.ollama_chat(_priority="chat", model=VISION_MODEL, messages=vision_messages,
                       keep_alive="30m", options={"num_ctx": CTX_WINDOW})
    return resp["message"].get("content", "").strip()


def _chat(user_text: str, image_bytes: bytes | None = None,
          person: dict | None = None, progress=None) -> str | None:
    person  = person or {"name": "Brian", "role": "owner"}
    is_owner = person["role"] == "owner"
    pkey    = "owner" if is_owner else person["name"].lower()
    history = _history_for(pkey)
    source  = "telegram" if is_owner else f"telegram:{pkey}"
    who     = person["name"]
    # Condition A: bare baseline — no tools, no memory writes.
    _bare = is_owner and _cond.CONDITION == "A"
    tool_list = [] if _bare else (tools.TOOLS if is_owner else [
        t for t in tools.TOOLS if t["function"]["name"] in GUEST_TOOLS])
    # If a photo was sent, describe it first then fold the description into the message
    if image_bytes:
        print("  [vision] describing Telegram photo...")
        description = _describe_image(image_bytes)
        print(f"  [vision] {description[:100]}...")
        # Durably remember the photo so it survives future restarts
        _ts = _dt.now().strftime("%b %d %H:%M")
        _note = f"[Telegram photo — {_ts}] {who} sent a photo. I saw: {description}"
        mem.remember(_note) if is_owner else _remember_for(who, _note)
        # Build the message Amy sees — caption + what she actually saw
        caption = f"{user_text}\n\n" if user_text else ""
        user_text = (
            caption
            + f"[Photo from {who} — you looked at it and here's what you see: {description}]\n"
            "React naturally to what's in the photo, like a friend would."
        )

    saved_note = ""
    if not is_owner and len(user_text) > BIG_PASTE:
        # A research-document-sized paste would crowd out the conversation and the
        # answer. Keep it in her library instead, and send Amy her question plus the
        # passages that matter.
        fname = f"Pasted text {_dt.now():%b %d %H-%M}.txt"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, fname)
                with open(path, "w") as f:
                    f.write(user_text)
                n, _ = guest_docs.add(pkey, fname, path)
            _log(f"  (saved {who}'s {len(user_text):,}-char paste as a document, {n} passages)")
            saved_note = (f"(That was a long paste, so I saved it to your documents as "
                          f"\"{fname}\" and can quote from it anytime.)\n\n")
            user_text = (user_text[:1500] + f"\n\n…[middle of the paste saved as '{fname}'; "
                         "relevant passages are included below]…\n\n" + user_text[-600:])
        except Exception as e:
            _log(f"  (could not save {who}'s paste: {e})")
            user_text = user_text[:BIG_PASTE]

    user_msg = {"role": "user", "content": user_text}
    model_msg = user_msg
    if not is_owner and user_text.strip():
        try:
            docs_ctx = guest_docs.context_block(pkey, user_text)
        except Exception as e:
            docs_ctx = ""
            _log(f"  (document search failed for {who}: {e})")
        if docs_ctx:
            model_msg = {"role": "user", "content": user_text + "\n" + docs_ctx}
    recent = history[-8:]
    if not is_owner:
        recent = [dict(_msg_to_dict(m), content=(_msg_to_dict(m).get("content") or "")[:3000])
                  for m in recent]
    _prior = next((str(_d.get("content") or "") for _d in
                   (_msg_to_dict(m) if not isinstance(m, dict) else m for m in reversed(history))
                   if _d.get("role") == "assistant" and _d.get("content")), "")
    ctx = _turn_context(len(history), person, user_text, _prior)
    model_msg = dict(model_msg, content=f"{ctx}\n\n[Message from {who}]\n{model_msg['content']}")
    messages = [{"role": "system", "content": _build_system(len(history), person)}] + recent + [model_msg]
    history.append(user_msg)
    if not _bare:
        mem.add("user", user_text[:500], source=source)

    # ── Morning brief: pre-fetch live data so amy-vl only has to write ───────
    if _BRIEFING_RE.search(user_text):
        import web as _web
        _today = _dt.now().strftime("%A, %B %d, %Y")
        _parts = [f"[BRIEFING DATA — {_today}]"]

        # Weather — wttr.in gives live conditions, no API key needed
        try:
            import requests as _req
            _w = _req.get("https://wttr.in/Winston-Salem+NC?format=4", timeout=8).text.strip()
            _parts.append(f"\n[WEATHER]\n{_w}")
        except Exception as _we:
            _parts.append(f"\n[WEATHER]\n(unavailable: {_we})")

        _searches = [
            ("markets",  f"global stock market news today {_dt.now().strftime('%B %Y')}"),
            ("tech_ai",  f"AI and tech news today {_dt.now().strftime('%B %d %Y')}"),
            ("world",    f"top world news stories today {_dt.now().strftime('%B %d %Y')}"),
            ("local",    f"Winston-Salem NC local news today {_dt.now().strftime('%B %Y')}"),
        ]
        for _label, _q in _searches:
            try:
                _res = _web.search(_q, max_results=4)
                _snips = "\n".join(
                    f"- {r['title']}: {r['snippet']}" for r in _res if r.get("snippet")
                )
                _parts.append(f"\n[{_label.upper()}]\n{_snips or '(no results)'}")
            except Exception as _be:
                _parts.append(f"\n[{_label.upper()}]\n(search failed: {_be})")
        messages.append({
            "role": "tool",
            "content": (
                "\n".join(_parts)
                + "\n\n[INSTRUCTION] Synthesize into Amy's morning brief. "
                "Sections: Weather, Markets, Tech & AI, World News, Local (Charlotte NC). "
                "End with one sharp observation Brian should act on. Scannable — 90 seconds to read."
            ),
        })

    msg = None
    seen = {}
    _tool_counts: dict = {}   # name → call count this turn
    _STARTUP_TOOL_CAP = {     # tools that should rarely repeat in one turn
        "engram_memory": 2,
        "moltbook_check_in": 1,
    }
    _logged_tool_calls: list = []
    for step in range(1, MAX_HOPS + 1):
        _t0 = time.time()
        worker._fit(messages)
        _chat_kw = dict(_priority="chat", model=MODEL, messages=messages, tools=tool_list,
                        keep_alive="30m", think=False,
                        options={"num_ctx": CTX_WINDOW, "num_predict": MAX_REPLY_TOKENS,
                                 **({} if is_owner else {"temperature": 0.4})})
        try:
            resp = worker.ollama_chat(**_chat_kw)
        except TypeError:
            # older ollama SDK doesn't support think=
            _chat_kw.pop("think", None)
            resp = worker.ollama_chat(**_chat_kw)
        except worker.ollama.ResponseError as e:
            if "does not support tools" not in str(e):
                raise
            _chat_kw.pop("tools", None)
            resp = worker.ollama_chat(**_chat_kw)
        msg  = resp["message"]
        # Strip any <think>...</think> blocks the model emits despite think=False
        if isinstance(msg.get("content"), str):
            import re as _re
            msg = dict(msg, content=_re.sub(r"(?s)<think>.*?</think>", "", msg["content"]).strip())
        _calls = [c["function"]["name"] for c in (msg.get("tool_calls") or [])]
        _tok, _ns = resp.get("eval_count") or 0, resp.get("eval_duration") or 0
        _log(f"  … {who}: {_tok} tokens in {time.time()-_t0:.1f}s"
             + (f" ({_tok/(_ns/1e9):.1f} tok/s)" if _ns else "")
             + (f" → tools {_calls}" if _calls else ""))
        messages.append(msg)
        history.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            break
        if progress:
            progress([c["function"]["name"] for c in calls], step)
        for call in calls:
            fn   = call["function"]
            name = fn["name"]
            args = fn.get("arguments", {})
            key  = (name, tuple(sorted((k, str(v)) for k, v in args.items()) if isinstance(args, dict) else ()))
            if not is_owner and name not in GUEST_TOOLS:
                result = "That tool is only available to Brian."
            elif is_owner and _is_public_action(name, args) and not _PUBLIC_OK.search(user_text):
                result = ("Not done: Brian didn't ask you to post, comment, follow or upvote. "
                          "Tell him what you found and ask if he wants you to act on it.")
                _log(f"  (blocked {name} {args.get('action','')} — not requested)")
            elif not is_owner and name == "remember_fact":
                _remember_for(who, str(args.get("fact") or args.get("text") or args))
                result = f"Remembered (private to {who})."
            else:
                cap = _STARTUP_TOOL_CAP.get(name)
                _tool_counts[name] = _tool_counts.get(name, 0) + 1
                if cap and _tool_counts[name] > cap:
                    result = (f"[{name} already called {cap}x this turn — "
                              f"your context is loaded. Stop and respond to {who}.]")
                    # She ignored the message and kept calling it (10 hops in a row, then out of
                    # steps): take the tool away so the next hop has to be a real answer.
                    tool_list = [t for t in tool_list if t.get("function", {}).get("name") != name]
                elif key in seen:
                    result = f"[Already called {name} with these exact args — use the result above.]"
                else:
                    try:
                        result = tools.dispatch(name, args, mem)
                    except Exception as _te:
                        # One failing tool must not end the whole turn: tell her, and she can carry on.
                        _log(f"  (tool {name} failed: {type(_te).__name__}: {_te})\n{_traceback.format_exc()}")
                        result = f"[tool_error] {name}: {type(_te).__name__}: {_te}"
                    seen[key] = result
            _logged_tool_calls.append({
                "name": name,
                "args": args if isinstance(args, dict) else str(args),
                "result_snippet": str(result)[:200] if result else "",
            })
            messages.append({"role": "tool", "content":
                (worker._cap(str(result)) if result else "done") + f"\n[step {step} of {MAX_HOPS}]"})
            history.append(messages[-1])

    # Out of steps while still calling tools: give her one last turn, without
    # tools, to tell the person what she did instead of going silent.
    if msg is not None and (msg.get("tool_calls") or not msg.get("content")):
        _log(f"  … {who}: " + ("out of steps" if msg.get("tool_calls") else "no final answer yet")
             + " — asking Amy to wrap up")
        messages.append({"role": "user", "content":
            "[You've used all your tool steps for this message. Don't call any more tools and "
            "don't promise to do more right now. In a few sentences: what you did, what you "
            "found, and what's left — then ask if they want you to continue.]"})
        worker._fit(messages)
        try:
            resp = worker.ollama_chat(_priority="chat", think=False, model=MODEL,
                               messages=messages, keep_alive="30m",
                               options={"num_ctx": CTX_WINDOW, "num_predict": MAX_REPLY_TOKENS,
                                        **({} if is_owner else {"temperature": 0.4})})
            msg = dict(_msg_to_dict(resp["message"]))
            msg["content"] = worker.strip_tool_text(msg.get("content", ""))
        except TypeError:
            resp = worker.ollama_chat(_priority="chat", model=MODEL,
                               messages=messages, keep_alive="30m",
                               options={"num_ctx": CTX_WINDOW, "num_predict": MAX_REPLY_TOKENS,
                                        **({} if is_owner else {"temperature": 0.4})})
            msg = dict(_msg_to_dict(resp["message"]))
            msg["content"] = worker.strip_tool_text(msg.get("content", ""))
        msg.pop("tool_calls", None)
        if not msg.get("content"):
            msg["content"] = "I ran out of steps before finishing. Want me to keep going?"
        history.append(msg)

    if msg and msg.get("content") and saved_note:
        msg = dict(_msg_to_dict(msg), content=saved_note + msg["content"])

    _reply_text = (msg.get("content") or "") if msg else ""
    if _reply_text:
        try:  # a quotation she can't find word for word in what she was given is flagged, not trusted
            import engram_bridge as _eb
            import quote_check as _qc
            _seen_text = _qc.sources_text(messages)      # never her own earlier replies
            _bad = _qc.unverified(_reply_text, _seen_text, library_has=_eb.library_has)
            _cases = _qc.unsourced_cases(_reply_text, _seen_text, library_has=_eb.library_has)
            _weak = (_qc.unsupported_citations(_reply_text, _prior, _eb.library_ids)
                     if _prior and _eb._FOLLOW_UP.search(user_text) else [])
            if _bad or _cases or _weak:
                _log(f"  [quotes] not backed up: {len(_bad)} quotation(s), {len(_cases)} case name(s), "
                     f"{len(_weak)} citation(s) that don't match her earlier claim")
                _reply_text += _qc.notes(_bad, _cases, _weak)
        except Exception as _qe:
            _log(f"  [quotes] check skipped: {type(_qe).__name__}: {_qe}")
    _outcome = "ok" if _reply_text else "no_content"

    # Always log — captures tool-error and no-content turns too.
    try:
        _turn_log.log_turn(
            condition=_cond.CONDITION,
            model=MODEL,
            system_prompt=_build_system(len(history), person),
            mood=amy_state.mood,
            energy_pct=int(amy_state.energy * 100),
            tool_calls=_logged_tool_calls,
            user_msg=user_text,
            assistant_reply=_reply_text,
            outcome=_outcome,
        )
    except Exception as _tl_err:
        _log(f"  [turn_log] {_tl_err}")

    if _reply_text:
        if not _bare:
            mem.add("assistant", _reply_text, source=source)
        amy_state.after_turn()
        _save_history(history, _history_file(pkey))
        return _reply_text
    return None


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle plain text messages."""
    text = update.message.text if update.message else None
    if not text:
        return
    _el.emit("MessageReceived", {"preview": text[:120], "channel": "telegram"}, source="telegram")
    person, intro = _identify(update.effective_chat.id, text)
    if not person:
        if intro:
            await update.message.reply_text(intro)
        return
    if person["role"] == "owner":
        worker.note_message(update.effective_chat.id)
        m = re.match(r"(?i)^invite\s+(\w[\w\s'-]{0,30})$", text.strip())
        if m:
            name = m.group(1).strip().title()
            code = _invite(name)
            await update.message.reply_text(
                f"Invite for {name}: have her open this chat's bot link, press Start, "
                f"and send just this code:\n\n{code}")
            return
        cmd = worker.handle_command(text)
        if cmd:
            await update.message.reply_text(cmd)
            return
    else:
        low = text.strip().lower()
        if low in ("my documents", "my docs", "my files", "documents"):
            docs = guest_docs.list_docs(person["name"])
            await update.message.reply_text(
                "Documents I'm keeping for you:\n" + "\n".join(f"• {d} ({n} passages)" for d, n in docs)
                if docs else "You haven't sent me any documents yet. Send a PDF or Word file and I'll keep it privately for you.")
            return
        m = re.match(r"(?i)^forget\s+(?:document\s+|file\s+)?(.+)$", text.strip())
        if m:
            gone = guest_docs.forget(person["name"], m.group(1).strip())
            await update.message.reply_text(
                ("Removed: " + ", ".join(gone)) if gone else "I couldn't find a document by that name. Say 'my documents' to see the list.")
            return
    # Telegram splits long pastes into several messages. Collect parts until
    # 3s pass with nothing new, then answer them together as one message.
    chat = update.effective_chat.id
    parts = _pending.setdefault(chat, [])
    parts.append(text)
    if len(parts) > 1:
        return                       # the first handler is already collecting
    while True:
        n = len(parts)
        await asyncio.sleep(3)
        if len(parts) == n:
            break
    combined = "\n".join(_pending.pop(chat))
    if n > 1:
        _log(f"  (combined {n} split messages from {person['name']})")
    await ctx.bot.send_chat_action(chat_id=chat, action="typing")
    await _respond(update, person, combined, None)


_pending: dict = {}


async def on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Transcribe a voice message with Whisper and pass the text to Amy."""
    if not update.message:
        return
    person, _ = _identify(update.effective_chat.id, "")
    if not person:
        return
    if person["role"] == "owner":
        worker.note_message(update.effective_chat.id)
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    voice = update.message.voice or update.message.audio
    if not voice:
        return
    file = await ctx.bot.get_file(voice.file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        ogg_path = tmp.name
    try:
        await file.download_to_drive(ogg_path)
        # Convert ogg→wav for whisper
        wav_path = ogg_path.replace(".ogg", ".wav")
        import subprocess as _sp
        _sp.run(["ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", wav_path],
                capture_output=True, check=True)
        import whisper as _whisper
        _wmodel = getattr(on_voice, "_wmodel", None)
        if _wmodel is None:
            print("  [stt] loading Whisper base model…", flush=True)
            on_voice._wmodel = _whisper.load_model("base")
            _wmodel = on_voice._wmodel
        result = _wmodel.transcribe(wav_path, fp16=False)
        text = result.get("text", "").strip()
    except Exception as e:
        _log(f"✗ STT failed: {e}")
        await update.message.reply_text("Couldn't transcribe that — try sending text for now.")
        return
    finally:
        for p in (ogg_path, wav_path if 'wav_path' in dir() else ""):
            try:
                if p: os.unlink(p)
            except OSError:
                pass

    if not text:
        await update.message.reply_text("I got your voice message but couldn't make out the words.")
        return

    _log(f"  [stt] transcribed ({len(text)} chars): {text[:60]}…")
    await update.message.reply_text(f"🎙 _{text}_", parse_mode="Markdown")
    await _respond(update, person, text, None)


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle photos — with or without a caption."""
    if not update.message:
        return
    person, _ = _identify(update.effective_chat.id, "")
    if not person:
        return
    if person["role"] == "owner":
        worker.note_message(update.effective_chat.id)
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Grab the highest-resolution version Telegram offers
    photo = update.message.photo[-1]
    file  = await ctx.bot.get_file(photo.file_id)
    buf   = io.BytesIO()
    await file.download_to_memory(buf)
    image_bytes = buf.getvalue()

    caption = update.message.caption or ""

    await _respond(update, person, caption, image_bytes)


def _plain(text: str) -> str:
    """Strip markdown symbols Telegram would show literally (**bold**, # headers, ```)."""
    text = re.sub(r"```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)\*(?![\w*])", r"\1", text)
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^(\s*)[*]\s+", r"\1• ", text)
    return text


_TOOL_PHRASES = {
    "web_search": "searching the web", "read_article": "reading articles",
    "moltbook_check_in": "checking Moltbook", "moltbook_engage": "working on Moltbook",
    "moltbook_post": "posting on Moltbook", "moltbook_verify": "working on Moltbook",
    "read_file": "reading files", "write_file": "saving a file", "list_files": "looking through files",
    "search_books": "searching the book catalog", "journal_entry": "writing in my journal",
    "read_journal": "reading my journal", "remember_fact": "noting that down",
    "check_email": "checking email", "research_income": "researching",
    "self_improve": "looking at my own code",
}


def _chunks(text: str, limit: int = 4000) -> list:
    """Split at paragraph/line breaks so each piece fits in one Telegram message."""
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        cut = cut if cut > 0 else limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return parts + ([text] if text else [])


async def _respond(update: Update, person: dict, text: str, image_bytes):
    """Run Amy, send the reply, and log what happened (never message content)."""
    who, t0 = person["name"], time.time()
    kind = "photo" if image_bytes else f"{len(text)} chars"
    _log(f"→ {who} sent a message ({kind})")
    chat_id = update.effective_chat.id
    loop    = asyncio.get_event_loop()

    # Telegram's "typing…" fades after ~5s; keep it on for the whole job.
    async def keep_typing():
        while True:
            try:
                await update.get_bot().send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                pass
            await asyncio.sleep(4)
    typing = asyncio.create_task(keep_typing())

    # One short note the first time she reaches for tools, so a long job never looks frozen.
    sent_note = {"done": False}
    def progress(tool_names, step):
        if sent_note["done"]:
            return
        sent_note["done"] = True
        doing = sorted({_TOOL_PHRASES.get(n, "working on it") for n in tool_names})
        note = "On it — " + ", ".join(doing) + ". This may take a couple of minutes."
        asyncio.run_coroutine_threadsafe(update.get_bot().send_message(chat_id=chat_id, text=note), loop)

    try:
        reply = await loop.run_in_executor(None, _chat, text, image_bytes, person, progress)
    except Exception as e:
        typing.cancel()
        _log(f"✗ {who}: Amy failed after {time.time()-t0:.0f}s — {type(e).__name__}: {e}\n{_traceback.format_exc()}")
        await update.message.reply_text("Sorry — something went wrong on my end. Try again in a minute?")
        return
    typing.cancel()
    if not reply:
        _log(f"✗ {who}: Amy produced an empty reply after {time.time()-t0:.0f}s")
        return
    if person["role"] != "owner":
        reply = _plain(reply)
    try:
        for part in _chunks(reply):
            await update.message.reply_text(part)
        _log(f"✓ {who}: reply delivered ({len(reply)} chars, {time.time()-t0:.0f}s total)")
    except Exception as e:
        _log(f"✗ {who}: reply written but Telegram send failed — {type(e).__name__}: {e}")


async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Guests send PDFs / Word files; Amy keeps them privately and quotes from them."""
    if not update.message or not update.message.document:
        return
    person, _ = _identify(update.effective_chat.id, "")
    if not person:
        return
    doc = update.message.document
    name = doc.file_name or "document"
    if person["role"] == "owner":
        await update.message.reply_text("Document library is for guests. For your own files, use learn_pdf.py.")
        return
    if not name.lower().endswith(guest_docs.SUPPORTED):
        await update.message.reply_text("I can read PDF, Word (.docx/.doc), RTF and text files.")
        return
    if (doc.file_size or 0) > 20_000_000:
        await update.message.reply_text("That file is over Telegram's 20 MB limit for me — could you split it?")
        return
    _log(f"→ {person['name']} sent a document ({(doc.file_size or 0)//1024} KB)")
    await update.message.reply_text(f"Reading {name}… one moment.")
    try:
        tg_file = await ctx.bot.get_file(doc.file_id)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, name)
            await tg_file.download_to_drive(path)
            loop = asyncio.get_event_loop()
            n, chars = await loop.run_in_executor(None, guest_docs.add, person["name"], name, path)
        _log(f"✓ {person['name']}: stored document ({n} passages)")
        await update.message.reply_text(
            f"Got it — I've saved {name} privately ({n} passages, about {chars // 1500} pages). "
            "Ask me anything about it, and I'll quote directly from it with the file name.")
    except Exception as e:
        _log(f"✗ {person['name']}: document failed — {type(e).__name__}: {e}")
        await update.message.reply_text(f"I couldn't read {name}: {e}")


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        print(f"  [telegram] network hiccup (auto-retry): {err}", flush=True)
    else:
        print(f"  [telegram] unhandled error: {err}", flush=True)


_PID_FILE = os.path.join(_here, "telegram_amy.pid")


def _acquire_pid():
    """Kill any previous instance, then write our PID. Prevents Telegram Conflict errors."""
    if os.path.exists(_PID_FILE):
        try:
            old_pid = int(open(_PID_FILE).read().strip())
            if old_pid != os.getpid():
                import signal as _sig
                try:
                    os.kill(old_pid, _sig.SIGTERM)
                    import time as _time; _time.sleep(1)
                    os.kill(old_pid, _sig.SIGKILL)
                except ProcessLookupError:
                    pass
                print(f"  [amy] killed stale instance pid={old_pid}", flush=True)
        except (ValueError, OSError):
            pass
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _release_pid():
    try:
        os.unlink(_PID_FILE)
    except OSError:
        pass


def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        sys.exit("TELEGRAM_TOKEN not set — check your .env.amy file")
    _acquire_pid()
    app = Application.builder().token(token).concurrent_updates(True).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_error_handler(_error_handler)
    worker.start(os.environ.get("AMY_MODEL", "gemma12-test"), mem, token)
    print("Amy is on Telegram. Voice messages, photos, and documents supported.")
    app.run_polling(drop_pending_updates=True)


def _shutdown(signum, frame):
    print("\n[amy] shutting down…", flush=True)
    _release_pid()
    import sys; sys.exit(0)

if __name__ == "__main__":
    import signal as _sig
    _sig.signal(_sig.SIGTERM, _shutdown)
    _sig.signal(_sig.SIGINT, _shutdown)
    main()
