"""Amy's persistent internal state — mood and energy that drift across a session.

Mood and energy are not performance. They're constraints that shape how she speaks.
A tired Amy is shorter, less expansive. A curious Amy asks more questions.
State is session-only — it resets on restart, which is intentional (sleep resets mood).
"""
import time
import random

# Mood ring: each mood has a description Amy can reference + tone hint for the prompt
MOODS = {
    "curious":    "curious and engaged — asking questions, leaning in",
    "focused":    "focused and direct — efficient, not chatty",
    "reflective": "reflective and a bit quiet — thinking before speaking",
    "playful":    "playful and light — more likely to be witty or teasing",
    "tired":      "tired — shorter replies, less elaboration, honest about it",
    "alert":      "alert and sharp — noticing details, slightly more intense",
}

# Energy: 0.0 (exhausted) to 1.0 (fully charged)
# Drops slightly each turn, recovers when Amy is idle, drops faster in long sessions

class InternalState:
    def __init__(self):
        self.mood = "curious"           # always starts curious
        self.energy = 1.0
        self.turn_count = 0
        self.session_start = time.time()
        self._last_turn_time = time.time()
        self._mood_locked_until = 0    # prevents mood flipping every turn

    def after_turn(self):
        """Call after each completed turn to update state."""
        now = time.time()
        gap = now - self._last_turn_time
        self._last_turn_time = now
        self.turn_count += 1

        # Energy drain: ~0.008 per turn so she stays above 50% for ~60 turns
        drain = 0.008 if gap > 10 else 0.012
        self.energy = max(0.05, self.energy - drain)

        # Mood drift — only shift every ~5 turns to avoid jitter
        if now > self._mood_locked_until:
            self._shift_mood()
            self._mood_locked_until = now + (5 * 60)  # lock for ~5 min

    def _shift_mood(self):
        """Drift mood naturally based on energy and time."""
        e = self.energy
        session_mins = (time.time() - self.session_start) / 60

        if e < 0.25:
            self.mood = "tired"
        elif e < 0.45:
            # Low energy — reflective or tired
            self.mood = random.choice(["reflective", "tired"])
        elif e > 0.8 and session_mins < 15:
            # Fresh session, high energy
            self.mood = random.choice(["curious", "alert", "playful"])
        else:
            # Mid-session — mix of focused and reflective
            self.mood = random.choice(["focused", "curious", "reflective"])

    def recover(self, seconds_idle):
        """Recover energy after a period of silence."""
        recovery = min(0.15, seconds_idle / 300 * 0.1)
        self.energy = min(1.0, self.energy + recovery)

    def mood_description(self):
        return MOODS.get(self.mood, "")

    def state_summary(self):
        """One-line summary for the system prompt."""
        e_pct = int(self.energy * 100)
        return (f"Your current mood: {self.mood} ({self.mood_description()}). "
                f"Energy level: {e_pct}% (turn {self.turn_count} this session). "
                f"Let this shape your tone naturally — don't announce it, just be it.")
