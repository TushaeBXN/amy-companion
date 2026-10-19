"""event_log.py — Shared event stream for Amy's three runtimes.

All three runtimes (companion, Telegram, worker) write here so they share
a single picture of what has happened. No runtime needs to know about the
others' internals — it just emits events and reads the shared log.

Event types
-----------
MessageReceived     — user sent a message to any runtime
TurnStarted         — Amy began generating a response
TurnCompleted       — Amy finished a response turn
ToolCalled          — a tool was dispatched
GoalStarted         — worker picked up a task/goal
GoalCompleted       — worker finished a task/goal successfully
GoalFailed          — worker task/goal failed
CommitmentMade      — Amy promised to do something later (detected from reply)
AgentMessageReceived — an AI agent posted to the inbox
MemoryUpdated       — remember_fact was called

Usage
-----
    import event_log as _el

    _el.emit("MessageReceived", {"from": "Brian", "preview": msg[:80]}, source="telegram")
    _el.emit("GoalStarted", {"task_id": task["id"], "prompt": task["prompt"][:120]}, source="worker")

    # Read what happened across all runtimes in the last hour
    recent = _el.recent(limit=20)
"""

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta

_HERE   = os.path.dirname(os.path.abspath(__file__))
_DB     = os.path.join(_HERE, "event_log.db")
_lock   = threading.Lock()

# How long to keep events (days)
_RETENTION_DAYS = 14


# ── database ──────────────────────────────────────────────────────────────────

def _db():
    conn = sqlite3.connect(_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _init():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id         TEXT PRIMARY KEY,
                ts         TEXT NOT NULL,
                source     TEXT NOT NULL,
                event_type TEXT NOT NULL,
                data       TEXT NOT NULL DEFAULT '{}'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON events(ts DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_type ON events(event_type, ts DESC)")
        conn.commit()


_init()


# ── write ─────────────────────────────────────────────────────────────────────

def emit(event_type: str, data: dict | None = None, source: str = "unknown") -> str:
    """Record an event. Returns the event ID."""
    event_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(data or {}, ensure_ascii=False)

    with _lock:
        with _db() as conn:
            conn.execute(
                "INSERT INTO events (id, ts, source, event_type, data) VALUES (?,?,?,?,?)",
                (event_id, ts, source, event_type, payload),
            )
            conn.commit()

    print(f"[event_log] {source}/{event_type}", flush=True)
    return event_id


# ── read ──────────────────────────────────────────────────────────────────────

def recent(
    limit: int = 30,
    event_types: list[str] | None = None,
    source: str | None = None,
    since_hours: float | None = None,
) -> list[dict]:
    """Return recent events, newest first."""
    clauses, params = [], []

    if event_types:
        placeholders = ",".join("?" * len(event_types))
        clauses.append(f"event_type IN ({placeholders})")
        params.extend(event_types)

    if source:
        clauses.append("source = ?")
        params.append(source)

    if since_hours is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
        clauses.append("ts >= ?")
        params.append(cutoff)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    with _db() as conn:
        rows = conn.execute(
            f"SELECT * FROM events {where} ORDER BY ts DESC LIMIT ?", params
        ).fetchall()

    return [
        {**dict(r), "data": json.loads(r["data"])}
        for r in rows
    ]


def last(event_type: str, source: str | None = None) -> dict | None:
    """Return the most recent event of a given type, or None."""
    results = recent(limit=1, event_types=[event_type], source=source)
    return results[0] if results else None


def pending_commitments() -> list[dict]:
    """Return CommitmentMade events that have no matching GoalCompleted follow-up."""
    commitments = recent(event_types=["CommitmentMade"], limit=50, since_hours=48)
    completed_ids = {
        e["data"].get("commitment_id")
        for e in recent(event_types=["CommitmentFulfilled"], limit=100, since_hours=48)
    }
    return [c for c in commitments if c["id"] not in completed_ids]


# ── maintenance ───────────────────────────────────────────────────────────────

def prune():
    """Remove events older than _RETENTION_DAYS. Call periodically."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_RETENTION_DAYS)).isoformat()
    with _db() as conn:
        n = conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,)).rowcount
        conn.commit()
    if n:
        print(f"[event_log] pruned {n} events older than {_RETENTION_DAYS} days")
    return n


# ── summary helper ────────────────────────────────────────────────────────────

def summary(since_hours: float = 12) -> str:
    """Human-readable summary of recent activity across all runtimes."""
    events = recent(limit=200, since_hours=since_hours)
    if not events:
        return f"No events in the last {since_hours:.0f} hours."

    counts: dict[str, int] = {}
    sources: set[str] = set()
    for e in events:
        counts[e["event_type"]] = counts.get(e["event_type"], 0) + 1
        sources.add(e["source"])

    lines = [f"Last {since_hours:.0f}h activity ({len(events)} events across {', '.join(sorted(sources))})"]
    for etype, n in sorted(counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {etype}: {n}")

    pending = pending_commitments()
    if pending:
        lines.append(f"\nPending commitments ({len(pending)}):")
        for c in pending[:5]:
            lines.append(f"  [{c['ts'][:16]}] {c['data'].get('description','?')[:80]}")

    return "\n".join(lines)
