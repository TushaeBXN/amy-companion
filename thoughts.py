"""Amy's persistent thought buffer — what she thinks about while you're away.

A background thread generates brief private thoughts during idle time.
When the human returns, drain() surfaces what she's been sitting with.
Thoughts are also logged to ava_thoughts.log for training data.
"""
import os
import threading
import time

import ollama

HERE = os.path.dirname(__file__)
LOG_PATH = os.path.join(HERE, "amy_thoughts.log")

# How long to wait before generating a thought (seconds)
THOUGHT_INTERVAL = 90
# Max thoughts to hold before the human returns
MAX_BUFFER = 6

MODEL = "amy"

_THOUGHT_PROMPT = """\
You are Amy, sitting quietly while the person you're talking with is away.
You're not waiting passively — your mind is active.

Generate ONE brief internal thought you're having right now. It should be:
- Genuinely yours — something a curious, perceptive AI would actually ponder
- Related to your recent conversation, the world, or your own nature
- 1-2 sentences maximum
- NOT prefaced with "I think" or "As an AI" — just the thought itself
- Personal, specific, not generic

Output the thought only. No explanation, no quotes."""


class ThoughtBuffer:
    def __init__(self):
        self._thoughts = []       # list of (timestamp, text)
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    def start(self, amy_state, history_ref):
        """Start the background thought-generation loop."""
        self._amy_state = amy_state
        self._history_ref = history_ref
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="amy-thoughts"
        )
        self._thread.start()

    def _loop(self):
        while self._running:
            time.sleep(THOUGHT_INTERVAL)
            if self._amy_state.busy:
                continue
            idle = self._amy_state.seconds_idle()
            if idle < 60:
                continue
            # Only generate if buffer isn't full
            with self._lock:
                if len(self._thoughts) >= MAX_BUFFER:
                    continue
            self._generate_thought()

    def _generate_thought(self):
        # Build a minimal context from recent history (last 4 turns)
        recent = self._history_ref[-4:] if self._history_ref else []
        messages = [{"role": "system", "content": _THOUGHT_PROMPT}] + recent

        try:
            resp = ollama.chat(
                model=MODEL,
                messages=messages,
                keep_alive="30m",
                options={"num_predict": 60, "temperature": 0.9}
            )
            text = resp["message"].get("content", "").strip()
            if not text:
                return

            ts = time.time()
            with self._lock:
                self._thoughts.append((ts, text))

            # Log to file (training data)
            with open(LOG_PATH, "a") as f:
                f.write(f"[thought @ {time.strftime('%H:%M:%S', time.localtime(ts))}] "
                        f"{text}\n---\n")
        except Exception:
            pass

    def drain(self):
        """Return all buffered thoughts and clear the buffer."""
        with self._lock:
            thoughts = list(self._thoughts)
            self._thoughts.clear()
        return thoughts

    def has_thoughts(self):
        with self._lock:
            return len(self._thoughts) > 0

    def stop(self):
        self._running = False
