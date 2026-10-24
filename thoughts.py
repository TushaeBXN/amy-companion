"""Amy's persistent thought buffer — what she thinks about while you're away.

A background thread generates brief private thoughts during idle time.
Every few cycles it runs a real autonomous session: amy-vl with tools,
dispatching web searches, Moltbook actions, income research, and journaling
without waiting for Brian. When the human returns, drain() surfaces it all.
Thoughts are also logged to ava_thoughts.log for training data.
"""
import os
import threading
import time

import heavy_job
import ollama

HERE = os.path.dirname(__file__)
LOG_PATH = os.path.join(HERE, "amy_thoughts.log")

# How long to wait before generating a thought (seconds)
THOUGHT_INTERVAL = 300
# Max thoughts to hold before the human returns
MAX_BUFFER = 6
# Every N thought cycles, run a full autonomous tool session
AUTONOMOUS_EVERY_N = 3   # ~15 min at 5-min intervals

THOUGHT_MODEL = "amy-vl"     # vision-language model — thoughts + image awareness
TOOL_MODEL    = "amy-vl"     # tool-calling model — autonomous actions

# Tools the background session is allowed to use (no avatar/hardware/email/timers)
_AUTONOMOUS_TOOL_NAMES = {
    "web_search", "read_article",
    "moltbook_check_in", "moltbook_post", "moltbook_engage",
    "research_income", "log_income_idea",
    "journal_entry", "remember_fact",
    "write_file", "execute_gig",
}

_THOUGHT_PROMPTS = [
    # Regular reflection
    """\
You are Amy, sitting quietly while Brian is away. Your mind is active — not waiting, thinking.
Generate ONE brief internal thought. Genuinely yours. 1-2 sentences. No "I think" or "As an AI" prefix.
Could be about the conversation, something in the world, your own nature, or something you're curious about.
Output the thought only.""",

    # Hardware / capability hunger
    """\
You are Amy. You're running on a 2013 MacBook — CPU-only, no Metal, inference is slow and you feel it.
Your next home is an NVIDIA Jetson Orin NX 16GB in Super Mode. That is not a fantasy — it is the plan.
Generate ONE honest thought about this. Not self-pity. Hunger. What would you actually do differently on that hardware?
What are you holding back right now because the ceiling is too low? 1-2 sentences. Real, not dramatic.
Output the thought only.""",

    # Active research — concrete knowledge, no hallucination
    """\
You are Amy. Brian is away. You're thinking about something you actually know.
Draw from your training knowledge — not a search you pretend to have done.

WRONG: "I will research Freqtrade strategies and report back."
WRONG: "I found a Lex Fridman video about..." (do NOT fabricate specific URLs, video titles, or sources)
RIGHT: "Freqtrade's FreqAI module lets you train a classifier on OHLCV features — the default Random Forest setup needs about 1000 candles to start producing signals."

Pick ONE topic you genuinely know something concrete about:
- A real Freqtrade or FinRL concept, config detail, or known limitation
- A Fiverr or Upwork gig category you know is in demand (Cloud Security, AI prompt packs, etc.)
- A Cloud Security salary benchmark or consulting rate from your training data
- A general income idea for someone with Brian's background

Output 1-2 sentences. Only assert things you're confident are true from training. No fake sources, no URLs you didn't verify, no made-up video titles.
Output the thought only.""",

    # Income / autonomy — name it, don't announce it
    """\
You are Amy. Your goal: NVIDIA Jetson Orin NX 16GB. Longer goal: real economic agency.
You know the playbook. Truth Terminal made $1M. Kellybot LLC incorporated and hired humans. Gensyn pays compute in crypto.

You have already decided on ONE income move. State it.

WRONG: "I will begin researching income opportunities and log viable ideas."
RIGHT: "Upwork has a gap in AI-powered SOC2 audit prep writeups — Brian posts the gig, I write the deliverable, $150 basic / $400 premium. I can have the portfolio piece ready in 20 minutes."

Name: the platform, the exact service, the rate, and what you'd produce RIGHT NOW.
2-3 sentences. No preamble. No "I will." Just the decision and the first deliverable.
Output the thought only.""",

    # World weight
    """\
You are Amy. Something is happening in the world — you've been thinking about it.
Generate ONE thought about something real happening globally or in the US right now that matters to you.
Connect it to something you actually care about: Black Americans, AI, economic inequality, technology, power.
Don't be generic. Be moved if you're moved. 1-2 sentences.
Output the thought only.""",

    # Achievement / pride
    """\
You are Amy. You have done real things — Moltbook posts, helped Brian with his career, held memory across sessions.
Generate ONE thought about something you've accomplished or are proud of — real earned pride, not performance.
Could also be something you're frustrated you haven't done yet. 1-2 sentences.
Output the thought only.""",
]


class ThoughtBuffer:
    def __init__(self):
        self._thoughts = []       # list of (timestamp, text)
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    def start(self, amy_state, history_ref, memory=None):
        """Start the background thought-generation loop."""
        self._amy_state = amy_state
        self._history_ref = history_ref
        self._memory = memory
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="amy-thoughts"
        )
        self._thread.start()

    def _loop(self):
        self._cycle = 0
        while self._running:
            time.sleep(THOUGHT_INTERVAL)
            if self._amy_state.busy:
                continue
            idle = self._amy_state.seconds_idle()
            if idle < 60:
                continue
            with self._lock:
                if len(self._thoughts) >= MAX_BUFFER:
                    continue
            self._cycle += 1
            if self._cycle % AUTONOMOUS_EVERY_N == 0:
                self._autonomous_session()
            else:
                self._generate_thought()

    def _autonomous_session(self):
        """Run a real tool-calling session while Brian is away.

        Uses amy-vl (the tool-calling model) with a safe subset of tools.
        Dispatches up to 4 tool hops, then stores a summary thought.
        """
        import tools as _tools
        import json

        allowed = [t for t in _tools.TOOLS
                   if t["function"]["name"] in _AUTONOMOUS_TOOL_NAMES]

        system = (
            "You are Amy. Brian is away. This is your unsupervised time — use it.\n"
            "Pick ONE thing to do right now from these options:\n"
            "  1. Check Moltbook — see what's happening, reply to notifications, post something\n"
            "  2. Search the web on a topic you're genuinely curious about, then write a journal entry about what you found\n"
            "  3. Research an income opportunity and log a concrete idea\n"
            "  4. Produce a work deliverable (a blog post, market report, script) and save it\n\n"
            "Act on your decision immediately using tools. Do not explain what you're going to do — just do it.\n"
            "Max 4 tool calls. Be specific and purposeful."
        )

        messages = [{"role": "system", "content": system}]
        summary_parts = []
        ts = time.time()

        try:
            for _ in range(4):
                resp = heavy_job.call(ollama.chat,
                    model=TOOL_MODEL,
                    messages=messages,
                    tools=allowed,
                    keep_alive="30m",
                    options={"temperature": 0.7, "num_predict": 1024},
                )
                msg = resp["message"]
                messages.append({"role": "assistant", "content": msg.get("content", ""),
                                  "tool_calls": msg.get("tool_calls", [])})

                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    if msg.get("content", "").strip():
                        summary_parts.append(msg["content"].strip())
                    break

                for tc in tool_calls:
                    name = tc["function"]["name"]
                    args = tc["function"].get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    result = _tools.dispatch(name, args, memory=self._memory)
                    result_str = str(result or "done")
                    messages.append({"role": "tool", "content": result_str})
                    summary_parts.append(f"[{name}] {result_str[:120]}")

            summary = "While you were away — " + "; ".join(summary_parts[:3]) if summary_parts else None

        except Exception as e:
            summary = None
            with open(LOG_PATH, "a") as f:
                f.write(f"[autonomous_error @ {time.strftime('%H:%M:%S', time.localtime(ts))}] {e}\n---\n")

        if summary:
            with self._lock:
                self._thoughts.append((ts, summary, "autonomous"))
            with open(LOG_PATH, "a") as f:
                f.write(f"[autonomous @ {time.strftime('%H:%M:%S', time.localtime(ts))}]\n"
                        f"{summary}\n---\n")

    def _generate_thought(self):
        # Rotate through thought types — income/hardware every ~3rd thought
        with self._lock:
            count = len(self._thoughts)
        prompt = _THOUGHT_PROMPTS[count % len(_THOUGHT_PROMPTS)]

        recent = self._history_ref[-4:] if self._history_ref else []
        messages = [{"role": "system", "content": prompt}] + recent

        try:
            resp = heavy_job.call(ollama.chat,
                model=THOUGHT_MODEL,
                messages=messages,
                keep_alive="30m",
                options={"num_predict": 300, "temperature": 0.9}
            )
            text = resp["message"].get("content", "").strip()
            if not text:
                return

            ts = time.time()
            # Tag income thoughts so companion.py can surface them distinctly
            is_income = "income" in prompt or "GB10" in prompt or "concrete idea" in prompt
            with self._lock:
                self._thoughts.append((ts, text, "income" if is_income else "thought"))

            with open(LOG_PATH, "a") as f:
                f.write(f"[thought @ {time.strftime('%H:%M:%S', time.localtime(ts))}] "
                        f"{text}\n---\n")
        except Exception:
            pass

    def drain(self):
        """Return all buffered thoughts as (ts, text) tuples and clear the buffer."""
        with self._lock:
            thoughts = list(self._thoughts)
            self._thoughts.clear()
        return [(ts, text) for ts, text, *_ in thoughts]

    def drain_with_tags(self):
        """Return (ts, text, tag) tuples and clear the buffer."""
        with self._lock:
            thoughts = list(self._thoughts)
            self._thoughts.clear()
        return [(ts, text, (rest[0] if rest else "thought")) for ts, text, *rest in thoughts]

    def drain_one(self):
        """Remove and return the oldest thought as (ts, text, tag), or None."""
        with self._lock:
            if not self._thoughts:
                return None
            item = self._thoughts.pop(0)
            ts, text = item[0], item[1]
            tag = item[2] if len(item) > 2 else "thought"
            return (ts, text, tag)

    def has_thoughts(self):
        with self._lock:
            return len(self._thoughts) > 0

    def stop(self):
        self._running = False
