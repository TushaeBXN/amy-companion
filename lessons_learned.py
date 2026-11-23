"""
Persistent failure memory for Amy.

Every time a task or goal step fails, the failure is stored here with:
- what the task was
- what went wrong
- what was tried
- what category of failure it was

Before each new task/step, Amy queries this DB for similar past failures
and gets them injected into her planning context — so she doesn't repeat
mistakes, she approaches the task already knowing what didn't work.

The search is keyword-based (no embeddings needed, runs on-device fast).
"""

import json, os, re, sqlite3, time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
DB_PATH = HERE / "lessons_learned.db"
MAX_LESSONS = 5000   # keep the DB from growing forever


def _conn():
    db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    db.execute("""
        CREATE TABLE IF NOT EXISTS lessons (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL NOT NULL,
            task_id     TEXT,
            category    TEXT,
            objective   TEXT NOT NULL,
            what_failed TEXT NOT NULL,
            tools_tried TEXT,
            pattern     TEXT,
            applies_when TEXT,
            outcome     TEXT DEFAULT 'failure'
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_ts ON lessons(ts)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_category ON lessons(category)")
    db.commit()
    return db


def record_failure(
    objective: str,
    what_failed: str,
    task_id: str = "",
    category: str = "execution",
    tools_tried: list = None,
    pattern: str = "",
    applies_when: str = "",
):
    """Store a failure for future reference."""
    try:
        db = _conn()
        db.execute(
            """INSERT INTO lessons
               (ts, task_id, category, objective, what_failed, tools_tried, pattern, applies_when)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                time.time(),
                task_id or "",
                category or "execution",
                objective[:800],
                what_failed[:800],
                json.dumps(tools_tried or []),
                pattern[:500],
                applies_when[:300],
            ),
        )
        db.commit()
        # Prune oldest if over limit
        count = db.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
        if count > MAX_LESSONS:
            db.execute(
                "DELETE FROM lessons WHERE id IN "
                "(SELECT id FROM lessons ORDER BY ts ASC LIMIT ?)",
                (count - MAX_LESSONS,),
            )
            db.commit()
        db.close()
    except Exception as e:
        print(f"  [lessons] record_failure error: {e}", flush=True)


def record_success(objective: str, task_id: str = "", pattern: str = ""):
    """Record what worked — useful for positive reinforcement context."""
    try:
        db = _conn()
        db.execute(
            """INSERT INTO lessons
               (ts, task_id, category, objective, what_failed, pattern, outcome)
               VALUES (?, ?, 'success', ?, 'N/A — succeeded', ?, 'success')""",
            (time.time(), task_id or "", objective[:800], pattern[:500]),
        )
        db.commit()
        db.close()
    except Exception as e:
        print(f"  [lessons] record_success error: {e}", flush=True)


def recall(query: str, k: int = 4) -> str:
    """
    Search lessons relevant to `query`. Returns a formatted string
    ready to inject into a planning prompt, or "" if nothing relevant.

    Scoring: keyword overlap between query and objective/what_failed/pattern.
    Recent lessons get a small recency boost.
    """
    try:
        db = _conn()
        rows = db.execute(
            "SELECT ts, category, objective, what_failed, tools_tried, pattern, outcome "
            "FROM lessons ORDER BY ts DESC LIMIT 2000"
        ).fetchall()
        db.close()
    except Exception as e:
        print(f"  [lessons] recall error: {e}", flush=True)
        return ""

    if not rows:
        return ""

    q_words = set(re.findall(r"\w+", query.lower())) - {
        "the", "a", "an", "to", "and", "or", "in", "of", "for", "with", "this", "that"
    }
    if not q_words:
        return ""

    scored = []
    now = time.time()
    for ts, category, objective, what_failed, tools_tried_json, pattern, outcome in rows:
        blob = f"{objective} {what_failed} {pattern}".lower()
        blob_words = set(re.findall(r"\w+", blob))
        overlap = len(q_words & blob_words)
        if overlap == 0:
            continue
        # Recency boost: lessons from last 7 days score slightly higher
        age_days = (now - ts) / 86400
        recency = 1.2 if age_days < 7 else (1.1 if age_days < 30 else 1.0)
        score = overlap * recency
        scored.append((score, ts, category, objective, what_failed, tools_tried_json, pattern, outcome))

    if not scored:
        return ""

    scored.sort(key=lambda x: -x[0])
    top = scored[:k]

    failures = [r for r in top if r[7] != "success"]
    successes = [r for r in top if r[7] == "success"]

    lines = []
    if failures:
        lines.append("[Past failures on similar tasks — avoid these approaches]")
        for _, ts, category, objective, what_failed, tools_json, pattern, _ in failures:
            age = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            lines.append(f"• [{age}] {category}: {objective[:80]}")
            lines.append(f"  Failed: {what_failed[:120]}")
            if pattern:
                lines.append(f"  Better approach: {pattern[:120]}")
    if successes:
        lines.append("[What worked on similar tasks]")
        for _, ts, category, objective, what_failed, tools_json, pattern, _ in successes:
            if pattern:
                lines.append(f"• {pattern[:120]}")

    return "\n".join(lines) if lines else ""


def stats() -> str:
    """Quick summary of what's in the DB."""
    try:
        db = _conn()
        total = db.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
        failures = db.execute("SELECT COUNT(*) FROM lessons WHERE outcome != 'success'").fetchone()[0]
        cats = db.execute(
            "SELECT category, COUNT(*) FROM lessons WHERE outcome != 'success' "
            "GROUP BY category ORDER BY COUNT(*) DESC LIMIT 5"
        ).fetchall()
        db.close()
        cat_str = ", ".join(f"{c}:{n}" for c, n in cats)
        return f"{total} lessons ({failures} failures) — top categories: {cat_str or 'none yet'}"
    except Exception as e:
        return f"lessons DB error: {e}"
