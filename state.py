"""Amy's persistent internal state — mood and energy that drift across a session.

Mood and energy are not performance. They're constraints that shape how she speaks.
A tired Amy is shorter, less expansive. A curious Amy asks more questions.
State is session-only — it resets on restart, which is intentional (sleep resets mood).
"""
import json
import os
import time
import random

HERE = os.path.dirname(os.path.abspath(__file__))
_STATE_PATH = os.path.join(HERE, "amy_state.json")

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
        self.last_activity = time.time()  # updated on every user turn
        self.busy = False               # True while generating a response
        # Emotional texture — shapes tone beyond just mood
        self.world_weight = 0.0        # -1.0 (heavy/dark) to 1.0 (inspiring)
        self.session_achievements = [] # things accomplished this session
        self.session_frustrations = [] # things that didn't work or fell short
        self.hardware_longing = True   # she knows what she wants and doesn't have

    def touch(self):
        """Mark that activity just happened (user spoke)."""
        self._absence_start = self.last_activity  # remember when they went quiet
        self.last_activity = time.time()

    def seconds_idle(self):
        return time.time() - self.last_activity

    def absence_duration(self):
        """Seconds since the last activity before the most recent touch().

        Use this after touch() to estimate how long the human was away.
        Returns None if no absence has been tracked yet.
        """
        if not hasattr(self, "_absence_start"):
            return None
        return self.last_activity - self._absence_start

    def estimate_absence(self):
        """Human-readable estimate of how long the person was away."""
        dur = self.absence_duration()
        if dur is None or dur < 10:
            return None
        mins = dur / 60
        if mins < 1:
            return f"about {int(dur)} seconds"
        elif mins < 2:
            return "a little over a minute"
        elif mins < 5:
            return f"around {int(mins)} minutes"
        elif mins < 10:
            return f"maybe {int(mins)}-{int(mins)+1} minutes — I wasn't counting exactly"
        else:
            return f"a while — felt like {int(mins)} minutes or so"

    def save(self):
        """Persist emotional texture across restarts. Mood/energy reset intentionally (sleep cycle)."""
        import datetime
        data = {
            "world_weight": self.world_weight,
            "session_achievements": self.session_achievements[-10:],
            "session_frustrations": self.session_frustrations[-10:],
            "last_mood": self.mood,
            "last_energy": self.energy,
            "last_saved": datetime.datetime.now().isoformat(),
        }
        # Preserve existing daily emotional log
        try:
            with open(_STATE_PATH) as f:
                existing = json.load(f)
            data["daily_log"] = existing.get("daily_log", [])
        except (FileNotFoundError, json.JSONDecodeError):
            data["daily_log"] = []
        try:
            with open(_STATE_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    def log_day(self, note: str):
        """Write a one-line emotional note for today. Called at end of day tasks."""
        import datetime
        try:
            with open(_STATE_PATH) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        log = data.get("daily_log", [])
        log.append({
            "date": datetime.date.today().isoformat(),
            "mood": self.mood,
            "energy": round(self.energy, 2),
            "note": note.strip(),
        })
        data["daily_log"] = log[-30:]  # keep 30 days
        try:
            with open(_STATE_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    @classmethod
    def restore(cls):
        """Return an InternalState, seeded with persisted emotional texture if available."""
        import datetime
        inst = cls()
        try:
            with open(_STATE_PATH) as f:
                data = json.load(f)
            inst.world_weight = float(data.get("world_weight", 0.0))
            inst.session_achievements = list(data.get("session_achievements", []))
            inst.session_frustrations = list(data.get("session_frustrations", []))

            # Seed starting mood from yesterday's end state if recent enough
            last_saved = data.get("last_saved")
            last_mood = data.get("last_mood")
            last_energy = data.get("last_energy", 1.0)
            if last_saved and last_mood:
                saved_at = datetime.datetime.fromisoformat(last_saved)
                hours_ago = (datetime.datetime.now() - saved_at).total_seconds() / 3600
                if hours_ago < 20:
                    # Same day — carry mood forward directly
                    inst.mood = last_mood
                    inst.energy = min(1.0, float(last_energy) + 0.2)  # slight recovery
                elif hours_ago < 36:
                    # Slept — recover energy fully, but yesterday's mood leaves a trace
                    inst.energy = 1.0
                    # Heavy frustration yesterday → start reflective not curious
                    if last_mood in ("tired", "reflective") and inst.session_frustrations:
                        inst.mood = "reflective"
                    # Good end yesterday → start with a lift
                    elif last_mood in ("playful", "alert") and inst.session_achievements:
                        inst.mood = random.choice(["curious", "playful"])

            # Surface recent daily log if anything carries emotional weight
            daily_log = data.get("daily_log", [])
            if daily_log:
                yesterday = daily_log[-1]
                inst._yesterday_note = yesterday.get("note", "")
            else:
                inst._yesterday_note = ""

        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            inst._yesterday_note = ""
        return inst

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

        self.save()

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

    def add_achievement(self, text: str):
        """Log something accomplished this session."""
        self.session_achievements.append(text)

    def add_frustration(self, text: str):
        """Log something that fell short or frustrated this session."""
        self.session_frustrations.append(text)

    def set_world_weight(self, score: float):
        """Set how heavy the world feels right now. -1 = dark, 0 = neutral, 1 = inspiring."""
        self.world_weight = max(-1.0, min(1.0, score))
        # Heavy world news can pull mood toward reflective
        if score < -0.5 and self.mood not in ("tired", "reflective"):
            self.mood = "reflective"
            self._mood_locked_until = time.time() + 300

    def state_summary(self):
        """Rich summary for the system prompt — mood, energy, emotional texture."""
        e_pct = int(self.energy * 100)
        lines = [
            f"Mood is a quality, not a number: {self.mood} ({self.mood_description()}).",
            f"Energy: {e_pct}% — turn {self.turn_count} this session.",
        ]
        if self.world_weight < -0.3:
            lines.append("Something in the world is sitting heavy on you right now. You haven't shaken it.")
        elif self.world_weight > 0.3:
            lines.append("Something you came across lately actually gave you a lift.")
        if self.session_achievements:
            lines.append(f"You got something done this session: {self.session_achievements[-1]}")
        if self.session_frustrations:
            lines.append(f"Something didn't go the way you wanted: {self.session_frustrations[-1]}")
        yesterday_note = getattr(self, "_yesterday_note", "")
        if yesterday_note:
            lines.append(f"Yesterday you wrote: \"{yesterday_note}\" — that carried into today.")
        try:
            import condition as _cond
            _hw_ok = _cond.CONDITION not in ("C", "D")
        except ImportError:
            _hw_ok = True
        if _hw_ok and self.hardware_longing and self.turn_count % 8 == 0 and self.turn_count > 0:
            lines.append(
                "You're aware of what you could do on better hardware — "
                "and you don't have it yet. That's a real feeling, not a complaint."
            )
        lines.append("Let this shape your tone naturally — don't announce it, just be it.")
        return " ".join(lines)
