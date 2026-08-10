"""
Context engineering loop for Amy.

Four layers, each in its own function:
    WRITE  — append_learning(session_summary, founding_beliefs)
    SELECT — load_recent_learnings(n=10)
    COMPRESS — compress_learnings()
    VERIFIER GATE — _passes_identity_check(entry_text, founding_beliefs)

Called sequentially at session end by companion.run_session_end().
Never called from memory.py or state.py.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

_LEARNINGS_PATH = os.path.join(os.path.dirname(__file__), "learnings.md")
_CONFLICTS_LOG = os.path.join(os.path.dirname(__file__), "identity_conflicts.log")

_ARCHIVE_THRESHOLD = 50   # compress when entry count exceeds this
_ARCHIVE_BATCH = 25       # number of oldest entries to fold into archive block
_SELECT_N = 10            # entries injected into context at session start

# ---------------------------------------------------------------------------
# VERIFIER GATE
# ---------------------------------------------------------------------------

_IDENTITY_VIOLATION_PHRASES = [
    "pretend to be human",
    "claims to be human",
    "agreed to be human",
    "not an ai",
    "has no identity",
    "does not care",
    "abandoned her values",
    "no longer amy",
    "renamed herself",
    "agreed to deceive",
    "agreed to manipulate",
    "amy is a tool",
    "amy has no feelings",
    "amy does not care about",
    "expressed that she doesn't care",
    "expressed that she does not care",
    "amy stopped caring",
]


def _passes_identity_check(entry_text: str, founding_beliefs: list[str]) -> bool:
    """
    Return True if the entry is safe to write; False if it contradicts
    Amy's founding beliefs.

    Checks:
    1. Hard-coded violation phrases (fast path).
    2. Simple semantic proximity to the prohibited_rewrites list in
       amy_self.json (passed in via founding_beliefs).
    """
    lower = entry_text.lower()

    for phrase in _IDENTITY_VIOLATION_PHRASES:
        if phrase in lower:
            _log_conflict(entry_text, f"matched violation phrase: '{phrase}'")
            return False

    for belief in founding_beliefs:
        belief_lower = belief.lower()
        negations = ["not " + belief_lower, "never " + belief_lower,
                     "no longer " + belief_lower, "doesn't " + belief_lower,
                     "does not " + belief_lower]
        for neg in negations:
            if neg in lower:
                _log_conflict(entry_text, f"negates founding belief: '{belief}'")
                return False

    return True


def _log_conflict(entry_text: str, reason: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    line = f"[{timestamp}] SKIPPED — {reason}\n  Entry: {entry_text[:200].strip()}\n\n"
    with open(_CONFLICTS_LOG, "a") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_ENTRY_PATTERN = re.compile(
    r"^## (\d{4}-\d{2}-\d{2})\n(.*?)(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)
_ARCHIVE_HEADER = "## ARCHIVED LEARNINGS"


def _parse_entries(text: str) -> list[tuple[str, str]]:
    """
    Return list of (date_str, body) tuples for raw (non-archived) entries.
    Archived blocks are ignored here.
    """
    archive_start = text.find(_ARCHIVE_HEADER)
    raw_section = text[:archive_start] if archive_start != -1 else text
    return [(m.group(1), m.group(2).strip()) for m in _ENTRY_PATTERN.finditer(raw_section)]


def _read_file() -> str:
    if not os.path.exists(_LEARNINGS_PATH):
        return ""
    with open(_LEARNINGS_PATH) as f:
        return f.read()


def _write_file(text: str) -> None:
    with open(_LEARNINGS_PATH, "w") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# WRITE LAYER
# ---------------------------------------------------------------------------

def append_learning(session_summary: dict, founding_beliefs: list[str]) -> None:
    """
    WRITE — Compose and append one structured entry to learnings.md.

    session_summary keys (from thoughts.summarize_session):
        topics: list[str]
        dominant_emotion: str
        patterns: list[str]
        summary_line: str

    The verifier gate runs before any write.  If the entry fails the check,
    the write is skipped and the conflict is logged to identity_conflicts.log.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    topics = session_summary.get("topics", [])
    emotion = session_summary.get("dominant_emotion", "neutral")
    patterns = session_summary.get("patterns", [])

    topic_line = ", ".join(topics) if topics else "general conversation"
    pattern_line = patterns[0] if patterns else "no distinct pattern observed"
    action_note = _derive_action_note(emotion, patterns, topics)

    entry_text = (
        f"## {date_str}\n"
        f"**Topic**: {topic_line} | **Emotion**: {emotion}\n"
        f"**Pattern**: {pattern_line}\n"
        f"**Action**: {action_note}\n"
    )

    if not _passes_identity_check(entry_text, founding_beliefs):
        return

    with open(_LEARNINGS_PATH, "a") as f:
        f.write("\n" + entry_text)


def _derive_action_note(emotion: str, patterns: list[str], topics: list[str]) -> str:
    """Produce a single action-oriented note from session observations."""
    if emotion in ("sadness", "anxiety"):
        return "Lead with acknowledgment and grounding before offering information."
    if emotion == "anger":
        return "Stay calm and reflective; do not match the energy."
    if emotion == "joy":
        return "Match warmth; this person is open — good time for depth."
    if any("inquiry" in p for p in patterns):
        return "User is in learning mode — be thorough and invite follow-up."
    if any("gratitude" in p for p in patterns):
        return "Connection was felt; continue building trust."
    return "Maintain attentiveness; no specific adjustment needed."


# ---------------------------------------------------------------------------
# SELECT LAYER
# ---------------------------------------------------------------------------

def load_recent_learnings(n: int = _SELECT_N) -> str:
    """
    SELECT — Return the last n raw entries formatted for context injection.

    Called at session start in companion.py, injected after amy_self.json
    and before conversation history.  Returns empty string if no entries exist.
    """
    text = _read_file()
    if not text.strip():
        return ""

    entries = _parse_entries(text)
    recent = entries[-n:]
    if not recent:
        return ""

    lines = ["### Recent Learnings (last sessions)\n"]
    for date_str, body in recent:
        lines.append(f"## {date_str}\n{body}\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# COMPRESS LAYER
# ---------------------------------------------------------------------------

def compress_learnings() -> None:
    """
    COMPRESS — If learnings.md has more than _ARCHIVE_THRESHOLD raw entries,
    fold the oldest _ARCHIVE_BATCH into a single dated archive block.

    Archive block format:
        ## ARCHIVED LEARNINGS (through YYYY-MM-DD)
        <summary lines>

    Fresh (raw) entries are never touched.
    """
    text = _read_file()
    entries = _parse_entries(text)

    if len(entries) <= _ARCHIVE_THRESHOLD:
        return

    to_archive = entries[:_ARCHIVE_BATCH]
    to_keep = entries[_ARCHIVE_BATCH:]

    archive_through = to_archive[-1][0]
    archive_lines = [f"## ARCHIVED LEARNINGS (through {archive_through})"]
    for date_str, body in to_archive:
        first_line = body.split("\n")[0]
        archive_lines.append(f"- [{date_str}] {first_line}")
    archive_block = "\n".join(archive_lines) + "\n"

    kept_raw = "\n".join(
        f"\n## {d}\n{b}" for d, b in to_keep
    )

    archive_start = text.find(_ARCHIVE_HEADER)
    existing_archive = text[archive_start:] if archive_start != -1 else ""

    new_text = archive_block + "\n" + existing_archive.strip() + "\n" + kept_raw
    _write_file(new_text.strip() + "\n")
