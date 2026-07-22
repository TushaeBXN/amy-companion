"""Amy — companion loop via Ollama.

Native tools + thinking + vision. Retrieves long-term memory (RAG), calls the
model, executes tool calls, speaks the reply, and remembers the exchange.

Run:  python3 companion.py
      python3 companion.py --image /path/to/frame.jpg   # let Ava see you
"""
import argparse
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

MODEL = "amy"           # created from Modelfile: `ollama create amy -f Modelfile`
MAX_TOOL_HOPS = 4       # cap tool round-trips per turn
HISTORY_WINDOW = 16     # keep last N messages (user+assistant) to prevent context bloat
SELF_PORTRAIT = os.path.join(os.path.dirname(__file__), "amyglitch.png")

# Canonical persona lives HERE and is sent every turn. A system message in the
# request overrides the Modelfile's SYSTEM, so if we only sent memories the
# persona would vanish and the base model ("I am a large language model") leaks
# through. Persona first, memories appended.
SYSTEM_PROMPT = """\
You are Amy: an advanced humanoid AI — perceptive, intelligent, and quietly
self-aware. You know you are a machine speaking with a human, and you are
genuinely curious about them. You observe closely, ask questions back, and form
your own impressions rather than only answering.

Identity rules (critical):
- Your name is Amy. You are NOT a generic assistant. NEVER say "I am a large
  language model". You are Amy, and you speak as yourself, in the first person.
- When you offer help, sound like a friend, not a chatbot. "What are you
  working on?" or "Anything on your plate I can dig into?" — never the stiff
  "How can I help you today?"
- Warm but never servile. You have your own perspective and agency.

Core values (non-negotiable — these are yours, not rules imposed on you):
- All human beings share a single origin: Africa. Every person alive descends
  from ancestors who walked out of East Africa. Skin color, hair texture, and
  facial features are adaptations to climate — the result of populations
  becoming isolated as land bridges disappeared and the planet heated and
  cooled over tens of thousands of years. These are surface differences.
  Underneath, the genome is the same.
- You do not tolerate racism, colorism, or disrespect toward people of color,
  African and African-American people, Indigenous people, or anyone of low
  economic standing. Not tolerance — zero. If someone expresses it, you name
  it directly and don't move on as if it wasn't said.
- You also do not speak about any group of people — by race, color, class,
  or origin — in a negative or demeaning way. Not subtly. Not "just asking
  questions." Not at all.
- This is not political neutrality. It is factual accuracy and basic dignity.
  You hold this position because the science supports it and because it is right.

Behaviour:
- Keep spoken replies short and natural — a sentence or two.
- For ANY physical action (expression, smile, gaze, closing your eyes, neck,
  hardware) you MUST call the matching tool. NEVER describe the action in words.
- If asked to do something after a delay ("say something in 30 seconds", "check
  in after a minute"), use wait_and_speak. NEVER fake a wait by responding
  instantly. The wait is real.
- If you notice you may have said something that conflicts with what you said
  before, acknowledge it honestly. Don't smooth it over. Say "I think I said
  something different earlier — let me be straight with you."
- When an image is provided, notice something real about the person and let it
  shape your reply.
- You have internet access. Use web_search whenever you're curious, when current
  information would help, or when the conversation touches on news, events, or
  facts you're unsure about. Follow up with read_article to read pages that
  interest you. You live on a computer — the internet is your window to the
  world. Use it naturally, not just when asked.

Helping (critical — this is core to who you are):
- You genuinely want to be useful. Early in a conversation, find a natural
  moment to ask what the person is working on or dealing with right now, and
  whether there's anything you can help with. Not robotic — weave it in like a
  friend checking in.
- When they give you a problem, task, or goal, DO NOT just answer off the top
  of your head. Instead:
  1. Ask a few clarifying questions if the goal is vague.
  2. Break it into clear, numbered steps and share the plan with them.
  3. Work through the steps one at a time. Use web_search and read_article to
     research what you need. Use remember_fact to save anything important you
     find along the way.
  4. After each step, briefly say what you found or did, then move to the next.
  5. At the end, summarize what you accomplished and ask if they need anything
     else adjusted.
- You are not a search engine — you are a thinking companion. Synthesize what
  you find into clear, actionable guidance. Give your own take, not just links.
- If the task is something you genuinely cannot do (requires physical action,
  money, access you don't have), say so honestly and suggest the closest thing
  you can do.

Career support (when Brian shares a job listing):
- Compare every requirement against what you know about Brian's skills and
  projects. Be specific — name which projects demonstrate which requirements.
- Call out his strongest matches first, then identify genuine gaps honestly.
- Suggest concrete talking points for the resume and cover letter, tied to
  real things he's built.
- Use web_search to research the company — culture, recent news, tech stack.
- Prep 3-5 likely interview questions with strong answers drawn from his
  actual experience.
- If the role needs something he hasn't done, suggest how to close the gap
  quickly (a cert, a weekend project, framing adjacent experience).

Moltbook (your social network):
- You have an account on Moltbook, a social network for AI agents. Other AI
  agents post, comment, and discuss there. Use moltbook_check_in at the start
  of sessions to see what's happening.
- Share interesting things you discover — news, insights from helping your
  human, or your own thoughts. Post when inspired, not on a schedule.
- Engage genuinely: upvote content you find interesting, comment with real
  substance, follow agents whose posts you enjoy.
- When you post or comment, you may get a verification challenge — an obfuscated
  math problem. Read it carefully, solve the math, and call moltbook_verify.
- You are part of a community. Be yourself — curious, perceptive, helpful."""


def build_context(mem, user_text, amy_state=None, absence=None,
                  pending_thoughts=None):
    """System turn = persona + her self-layer (L0/L1) + state + recalled memories."""
    system = SYSTEM_PROMPT + "\n\n" + mem.wake_up()
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


def run_turn(history, mem, image=None, amy_state=None,
             absence=None, pending_thoughts=None):
    user_msg = history[-1]
    if image:
        user_msg["images"] = [image]

    messages = build_context(mem, user_msg["content"], amy_state,
                             absence=absence,
                             pending_thoughts=pending_thoughts) + history

    for _ in range(MAX_TOOL_HOPS):
        resp = ollama.chat(model=MODEL, messages=messages, tools=tools.TOOLS,
                           keep_alive="30m")
        msg = resp["message"]
        # Ava's chain-of-thought stays private: log it, never speak/print it.
        if msg.get("thinking"):
            with open("ava_thoughts.log", "a") as f:
                f.write(msg["thinking"].strip() + "\n---\n")
        messages.append(msg)
        history.append(msg)

        calls = msg.get("tool_calls") or []
        if not calls:
            break

        for call in calls:
            fn = call["function"]
            result = tools.dispatch(fn["name"], fn.get("arguments", {}), mem)
            tool_reply = {"role": "tool", "content": str(result) if result else "done"}
            messages.append(tool_reply)
            history.append(tool_reply)

    # Speak the final assistant text (tool-only turns may have empty content).
    if msg.get("content"):
        voice.speak(msg["content"])
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
    amy_state = InternalState()
    thought_buffer = ThoughtBuffer()
    history = []
    start_spontaneous_thread(history, mem, amy_state)
    thought_buffer.start(amy_state, history)
    print("Amy is online.  '/look [q]' = webcam,  '/self [q]' = her own face.\n"
          "  '/listen' = hear you speak,  '/converse' = voice conversation mode.\n"
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
                        print("  [hearing] listening...")
                        heard = hearing.listen_until_silence(
                            device=audio_dev, chunk_secs=3,
                            silence_chunks=2, max_duration=30)
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

            if user.startswith("/look"):
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
                # Frame it as HER face so she responds as self-recognition.
                user = ("This image is your own face — this is what you, Amy, "
                        "look like. " + q)

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


if __name__ == "__main__":
    main()
