"""Amy's personal journal — her own thoughts, reflections, and observations."""
import os
import re
from datetime import datetime, timezone

HERE = os.path.dirname(__file__)
JOURNAL_DIR = os.path.join(HERE, "journal")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _path(date: str) -> str:
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    return os.path.join(JOURNAL_DIR, f"{date}.md")


def save_entry(text: str, prompt: str = "") -> str:
    date = _today()
    ts = datetime.now().strftime("%H:%M")
    path = _path(date)
    header = f"# Journal — {date}\n\n" if not os.path.exists(path) else ""
    block = f"## {ts}"
    if prompt:
        block += f" — {prompt}"
    block += f"\n\n{text.strip()}\n\n"
    with open(path, "a") as f:
        f.write(header + block)
    return f"Saved to journal/{date}.md"


def read_recent(days: int = 7) -> str:
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    files = sorted(
        [f for f in os.listdir(JOURNAL_DIR) if re.match(r"\d{4}-\d{2}-\d{2}\.md", f)],
        reverse=True
    )[:days]
    if not files:
        return "No journal entries yet."
    parts = []
    for fname in files:
        with open(os.path.join(JOURNAL_DIR, fname)) as f:
            parts.append(f.read().strip())
    return "\n\n---\n\n".join(parts)


def read_today() -> str:
    path = _path(_today())
    if not os.path.exists(path):
        return f"No entry yet for {_today()}."
    with open(path) as f:
        return f.read().strip()
