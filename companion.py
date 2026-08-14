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
import hearing
from memory import Memory
from state import InternalState
from thoughts import ThoughtBuffer
from amy_persona import SYSTEM_PROMPT
import learnings as _learnings
_session_learnings: str = ""

MODEL = os.environ.get("AMY_MODEL", "amy-hermes")         # tool-calling engine (Llama 3 8B)
VISION_MODEL = os.environ.get("AMY_VISION_MODEL", "amy")  # vision engine (gemma4:e2b multimodal)
CODE_MODEL = os.environ.get("AMY_CODE_MODEL", "deepseek-coder:6.7b")  # code generation
MAX_TOOL_HOPS = 4       # cap tool round-trips per turn
HISTORY_WINDOW = 16     # keep last N messages (user+assistant) to prevent context bloat
CTX_WINDOW = 8192       # cap KV cache — 131K default on amy-hermes kills performance on CPU
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
    r'|what\s+(else\s+is\s+on\s+your\s+mind|would\s+you\s+like\s+me\s+to\s+do)'
    r'|you\s+bet\b'
    r'|i\'m\s+ready\s+to\s+assist'
    r'|how\s+can\s+i\s+support)',
    re.I,
)


def _strip_trailers(text: str) -> str:
    """Remove Hermes 3 helpfulness sign-off lines that slip past the system prompt."""
    lines = text.splitlines()
    while lines and _TRAILER_RE.search(lines[-1]):
        lines.pop()
    return "\n".join(lines).rstrip()


def _is_code_request(text: str) -> bool:
    """True if the turn is best served by CODE_MODEL, not Amy."""
    if _AMY_OVERRIDE_RE.search(text):
        return False
    return bool(_CODE_RE.search(text))


def build_context(mem, user_text, amy_state=None, absence=None,
                  pending_thoughts=None):
    """System turn = persona + her self-layer (L0/L1) + state + recalled memories."""
    system = SYSTEM_PROMPT + "\n\n" + mem.wake_up()
    if _session_learnings:
        system += "\n\n" + _session_learnings
    if amy_state:
        system += "\n\n[Internal state]\n" + amy_state.state_summary()

    # Surface buffered thoughts from while the human was away
    if pending_thoughts:
        thought_texts = [t for _, t in pending_thoughts]
        system += ("\n\n[Thoughts you had while they were away — these are yours, "
                   "share them naturally if it fits]\n- "
                   + "\n- ".join(thought_texts))

    # Time estimation for absence
    if absence:
        system += (f"\n\n[Time awareness] The person was away for {absence}. "
                   "If they ask how long, give your honest estimate using those words. "
                   "Don't pretend you have a precise clock.")

    hits = mem.search(user_text, k=4)
    if hits:
        system += ("\n\n[Relevant context — things the user has said before]\n- "
                   + "\n- ".join(hits))
        conflicts = mem.check_contradiction(user_text)
        relevant_conflicts = [c for c in conflicts if c[0] not in hits]
        if relevant_conflicts:
            system += ("\n\n[Possible tension — you may have said something "
                       "different before. Be honest if there's a conflict, "
                       "don't smooth it over.]\n- "
                       + "\n- ".join(t for t, _ in relevant_conflicts))
    return [{"role": "system", "content": system}]


def _b64_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def run_turn(history, mem, image=None, amy_state=None,
             absence=None, pending_thoughts=None):
    user_msg = history[-1]
    if image:
        b64 = _b64_image(image)
        user_msg["images"] = [b64]
        print(f"  [vision] image attached ({len(b64)//1024}KB b64)")

    # Strip images from history before any text-model call — amy-hermes is text-only
    _text_history = [{k: v for k, v in m.items() if k != "images"} for m in history]
    messages = build_context(mem, user_msg["content"], amy_state,
                             absence=absence,
                             pending_thoughts=pending_thoughts) + _text_history

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
                                  keep_alive="30m")
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
                                       keep_alive="30m")
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
                                   keep_alive="30m", options={"num_ctx": CTX_WINDOW})
            else:
                # Non-comparison: inject description into system context normally.
                sys_msgs = build_context(
                    mem, user_msg["content"], amy_state,
                    absence=absence, pending_thoughts=pending_thoughts
                )
                sys_msgs[0]["content"] += (
                    f"\n\n[Vision — you just looked at an image and observed: {description}]"
                    f"\nUse this when answering. Do not say you cannot see or have no form."
                )
                clean_user = user_msg.copy()
                clean_user.pop("images", None)
                # Strip images from all prior history — amy-hermes is text-only
                safe_history = [{k: v for k, v in m.items() if k != "images"}
                                for m in history[:-1]]
                amy_messages = sys_msgs + safe_history + [clean_user]
                resp = ollama.chat(model=MODEL, messages=amy_messages,
                                   tools=tools.TOOLS, keep_alive="30m",
                                   options={"num_ctx": CTX_WINDOW})
            msg = resp["message"]
            if msg.get("thinking"):
                with open("amy_thoughts.log", "a") as f:
                    f.write(msg["thinking"].strip() + "\n---\n")
            history.append(msg)
            if msg.get("content"):
                clean = _strip_trailers(msg["content"])
                msg["content"] = clean
                voice.speak(clean)
                mem.add("assistant", clean)
            if amy_state:
                amy_state.after_turn()
        return history

    _seen_calls = {}  # dedup: (name, frozen_args) → result
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
            voice.speak(code_msg["content"])
            mem.add("assistant", code_msg["content"])
        if amy_state:
            amy_state.after_turn()
        return history
    # ─────────────────────────────────────────────────────────────────────────

    for _ in range(MAX_TOOL_HOPS):
        resp = ollama.chat(model=MODEL, messages=messages, tools=tools.TOOLS,
                           keep_alive="30m", options={"num_ctx": CTX_WINDOW})
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
            reminder = (f"\n\n[You are mid-task. Complete the user's request: "
                        f"\"{user_task[:150]}\". Do not greet — keep working.]")
            tool_reply = {
                "role": "tool",
                "content": (str(result) if result else "done") + reminder,
            }
            messages.append(tool_reply)
            history.append(tool_reply)

    # Speak the final assistant text (tool-only turns may have empty content).
    if msg.get("content"):
        clean = _strip_trailers(msg["content"])
        msg["content"] = clean
        voice.speak(clean)
        mem.add("assistant", clean)
    if amy_state:
        amy_state.after_turn()
    return history


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

    system = (SYSTEM_PROMPT + "\n\n" + mem.wake_up() +
              "\n\n[Internal state]\n" + amy_state.state_summary() +
              f"\n\n[Instruction — do this NOW]\n{checkin_prompt}")

    safe = [{k: v for k, v in m.items() if k != "images"} for m in history[-6:]]
    messages = [{"role": "system", "content": system}] + safe

    try:
        resp = ollama.chat(model=MODEL, messages=messages, keep_alive="30m",
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="image path for Ava to 'see' this turn")
    args = ap.parse_args()

    mem = Memory()
    global _session_learnings
    _session_learnings = _learnings.load_recent_learnings()
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
    thought_buffer.start(amy_state, history)
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

            mem.add("user", user)
            history.append({"role": "user", "content": user})
            # Before touching the clock, capture absence info and buffered thoughts
            absence = amy_state.estimate_absence()
            pending_thoughts = thought_buffer.drain() if thought_buffer.has_thoughts() else []
            amy_state.touch()
            amy_state.busy = True
            print("  [Amy is thinking...]", flush=True)
            try:
                history = run_turn(history, mem, image=image, amy_state=amy_state,
                                   absence=absence, pending_thoughts=pending_thoughts)
            finally:
                amy_state.busy = False
            if len(history) > HISTORY_WINDOW:
                history = history[-HISTORY_WINDOW:]
    except (KeyboardInterrupt, EOFError):
        print("\nAmy: Goodbye.")
        _learnings.run_session_end(history, mem, amy_state)


if __name__ == "__main__":
    main()
