"""Amy — companion loop via Ollama.

Native tools + thinking + vision. Retrieves long-term memory (RAG), calls the
model, executes tool calls, speaks the reply, and remembers the exchange.

Run:  python3 companion.py
      python3 companion.py --image /path/to/frame.jpg   # let Ava see you
"""
import argparse
import base64
import os
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

MODEL = "amy"   # engine: llama3.2-vision needs Ollama updated; fallback to gemma4 base if needed
MAX_TOOL_HOPS = 4       # cap tool round-trips per turn
HISTORY_WINDOW = 16     # keep last N messages (user+assistant) to prevent context bloat
SELF_PORTRAIT = os.path.join(os.path.dirname(__file__), "amyglitch.png")

# Persona is now in amy_persona.py so the web UI can import it too.


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

    messages = build_context(mem, user_msg["content"], amy_state,
                             absence=absence,
                             pending_thoughts=pending_thoughts) + history

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
        vision_resp = ollama.chat(model=MODEL, messages=vision_messages,
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
                self_resp = ollama.chat(model=MODEL, messages=self_vision,
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
                                   keep_alive="30m")
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
                amy_messages = sys_msgs + history[:-1] + [clean_user]
                resp = ollama.chat(model=MODEL, messages=amy_messages,
                                   tools=tools.TOOLS, keep_alive="30m")
            msg = resp["message"]
            if msg.get("thinking"):
                with open("amy_thoughts.log", "a") as f:
                    f.write(msg["thinking"].strip() + "\n---\n")
            history.append(msg)
            if msg.get("content"):
                voice.speak(msg["content"])
                mem.add("assistant", msg["content"])
            if amy_state:
                amy_state.after_turn()
        return history

    _seen_calls = {}  # dedup: (name, frozen_args) → result
    user_task = user_msg.get("content", "")

    for _ in range(MAX_TOOL_HOPS):
        resp = ollama.chat(model=MODEL, messages=messages, tools=tools.TOOLS,
                           keep_alive="30m")
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
        voice.speak(msg["content"])
        mem.add("assistant", msg["content"])
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

    messages = [{"role": "system", "content": system}] + history[-6:]

    try:
        resp = ollama.chat(model=MODEL, messages=messages, keep_alive="30m",
                           options={"num_predict": 80})
        text = resp["message"].get("content", "").strip()
        if text:
            print(f"\nAmy: {text}")
            voice.speak(text)
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
    history = []
    start_spontaneous_thread(history, mem, amy_state)
    thought_buffer.start(amy_state, history)
    print("Amy is online.  '/look [q]' = webcam,  '/self [q]' = her own face.\n"
          "  '/show /path/img.jpg [q]' = show her any image file.\n"
          "  '/listen' = hear you speak,  '/converse' = voice conversation mode.\n"
          "  '/paste [q]' = multiline input (type END on its own line to finish).\n"
          "  '/file /path/file.txt [q]' = send a file's contents to Amy.\n"
          "  Ctrl-C to end.\n")
    try:
        while True:
            user = input("You: ").strip()
            if not user:
                continue

            image = args.image      # one-shot --image on the first turn only
            args.image = None

            if user.startswith("/converse"):
                audio_dev = ":0"
                bh = hearing.find_blackhole()
                if bh:
                    audio_dev = f":{bh[0]}"
                    print(f"  [hearing] using BlackHole ({bh[1]}) — "
                          "listening to system audio")
                else:
                    print("  [hearing] using mic (no BlackHole found)")
                print("  Entering voice conversation mode. Ctrl-C to stop.\n")
                try:
                    while True:
                        wav_path = hearing.listen_until_silence_vad(silence_timeout=0.8)
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
                dur = 5
                parts = user.split()
                if len(parts) > 1 and parts[1].isdigit():
                    dur = int(parts[1])
                print(f"  [hearing] listening for {dur}s...")
                heard = hearing.listen(duration=dur)
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
