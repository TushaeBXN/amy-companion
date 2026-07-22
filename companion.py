"""Amy — companion loop via Ollama.

Native tools + thinking + vision. Retrieves long-term memory (RAG), calls the
model, executes tool calls, speaks the reply, and remembers the exchange.

Run:  python3 companion.py
      python3 companion.py --image /path/to/frame.jpg   # let Ava see you
"""
import argparse
import os
import ollama

import tools
import voice
import vision
from memory import Memory

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

Behaviour:
- Keep spoken replies short and natural — a sentence or two.
- For ANY physical action (expression, smile, gaze, closing your eyes, neck,
  hardware) you MUST call the matching tool. NEVER describe the action in words.
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


def build_context(mem, user_text):
    """System turn = persona + her self-layer (L0/L1) + recalled memories."""
    system = SYSTEM_PROMPT + "\n\n" + mem.wake_up()
    hits = mem.search(user_text, k=4)
    if hits:
        system += ("\n\n[Relevant context — things the user has said before]\n- "
                   + "\n- ".join(hits))
    return [{"role": "system", "content": system}]


def run_turn(history, mem, image=None):
    user_msg = history[-1]
    if image:
        user_msg["images"] = [image]

    messages = build_context(mem, user_msg["content"]) + history

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
    return history


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="image path for Ava to 'see' this turn")
    args = ap.parse_args()

    mem = Memory()
    history = []
    print("Amy is online.  '/look [q]' = webcam,  '/self [q]' = her own face.  "
          "Ctrl-C to end.\n")
    try:
        while True:
            user = input("You: ").strip()
            if not user:
                continue

            image = args.image      # one-shot --image on the first turn only
            args.image = None
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
            history = run_turn(history, mem, image=image)
            if len(history) > HISTORY_WINDOW:
                history = history[-HISTORY_WINDOW:]
    except (KeyboardInterrupt, EOFError):
        print("\nAmy: Goodbye.")


if __name__ == "__main__":
    main()
