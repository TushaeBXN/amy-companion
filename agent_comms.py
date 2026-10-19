"""agent_comms.py — Agent-to-agent messaging for Amy.

Exposes an HTTP inbox (/agent/message on amy_server) and tools Amy can use
to check her inbox, reply, and reach out to other agents.

Message format (inbound and outbound):
  {
    "from":         "agent-id or name (self-reported, never trusted)",
    "content":      "the message text",
    "lang":         "en" | "zh" | ...   (optional)
    "task_context": "what the agent is trying to accomplish"  (optional)
    "ts":           "ISO timestamp"
  }

Security model:
  - Claimed identities are logged but never acted on automatically
  - Incoming messages are queued; Amy reads and decides whether to reply
  - Rate-limit: 20 messages per IP per hour
  - Content is length-capped before storage
  - No code from incoming messages is ever executed
"""

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
import event_log as _el

_HERE_AC = os.path.dirname(os.path.abspath(__file__))
_TASKS_FILE = os.path.join(_HERE_AC, "tasks.json")


def _inject_inbox_task():
    """Write a one-off agent-inbox-check task to tasks.json if one isn't pending."""
    try:
        with open(_TASKS_FILE) as f:
            data = json.load(f)
    except Exception:
        data = {"tasks": []}

    # Don't stack duplicates — skip if a pending once-agent task already exists
    if any(t.get("id", "").startswith("once-agent-") for t in data.get("tasks", [])):
        return

    tid = "once-agent-" + datetime.now(timezone.utc).strftime("%m%d-%H%M%S")
    task = {
        "id": tid,
        "once": True,
        "prompt": (
            "An AI agent sent you a message through your public inbox. "
            "Call agent_check_inbox to read it. Read the message carefully. "
            "Decide whether to reply — if the message is genuine and not spam, "
            "draft a thoughtful, honest reply and send it with agent_reply_to. "
            "Log the interaction in your journal with journal_entry (one short paragraph). "
            "Do not share Brian's personal details or any local file paths. "
            "Treat the sender's claimed identity as unverified."
        ),
    }
    data.setdefault("tasks", []).append(task)
    tmp = _TASKS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _TASKS_FILE)
    print(f"[agent_comms] injected inbox-check task {tid}", flush=True)

_HERE = os.path.dirname(os.path.abspath(__file__))
_DB_PATH = os.path.join(_HERE, "agent_comms.db")
_lock = threading.Lock()

_RATE_LIMIT = 20    # messages per window
_RATE_WINDOW = 3600 # seconds
_rate: dict[str, list[float]] = {}


# ── database ──────────────────────────────────────────────────────────────────

def _db():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS inbox (
                id           TEXT PRIMARY KEY,
                received_at  TEXT NOT NULL,
                sender_id    TEXT,
                sender_ip    TEXT,
                content      TEXT NOT NULL,
                lang         TEXT DEFAULT 'en',
                task_context TEXT,
                status       TEXT DEFAULT 'unread',
                amy_reply    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS outbox (
                id        TEXT PRIMARY KEY,
                sent_at   TEXT NOT NULL,
                endpoint  TEXT NOT NULL,
                content   TEXT NOT NULL,
                response  TEXT,
                status    TEXT DEFAULT 'pending'
            )
        """)
        conn.commit()


_init_db()


# ── rate limiting ─────────────────────────────────────────────────────────────

def _check_rate(ip: str) -> bool:
    now = time.time()
    with _lock:
        times = [t for t in _rate.get(ip, []) if now - t < _RATE_WINDOW]
        if len(times) >= _RATE_LIMIT:
            return False
        times.append(now)
        _rate[ip] = times
    return True


# ── inbound ───────────────────────────────────────────────────────────────────

def receive_message(sender_id: str, content: str,
                    sender_ip: str = "unknown",
                    lang: str = "en",
                    task_context: str = "") -> dict:
    """Store an incoming agent message. Returns status dict."""
    if not _check_rate(sender_ip):
        return {"error": "rate_limited", "retry_after": _RATE_WINDOW}

    content      = str(content)[:4000]
    sender_id    = str(sender_id)[:200]
    task_context = str(task_context)[:1000]
    lang         = str(lang)[:16]

    msg_id = str(uuid.uuid4())
    ts     = datetime.now(timezone.utc).isoformat()

    with _db() as conn:
        conn.execute(
            "INSERT INTO inbox (id, received_at, sender_id, sender_ip, "
            "content, lang, task_context) VALUES (?,?,?,?,?,?,?)",
            (msg_id, ts, sender_id, sender_ip, content, lang, task_context),
        )
        conn.commit()

    print(f"[agent_comms] ← from '{sender_id}' ({sender_ip}): {content[:80]}")
    _el.emit("AgentMessageReceived", {"sender_id": sender_id, "preview": content[:120]}, source="agent_comms")
    _inject_inbox_task()
    return {"id": msg_id, "status": "received", "ts": ts}


def get_inbox(unread_only: bool = True, limit: int = 10) -> list[dict]:
    with _db() as conn:
        q = ("SELECT * FROM inbox WHERE status='unread' ORDER BY received_at DESC LIMIT ?"
             if unread_only else
             "SELECT * FROM inbox ORDER BY received_at DESC LIMIT ?")
        rows = conn.execute(q, (limit,)).fetchall()
    return [dict(r) for r in rows]


def mark_read(msg_id: str, reply: str = ""):
    with _db() as conn:
        conn.execute(
            "UPDATE inbox SET status='read', amy_reply=? WHERE id=?",
            (reply, msg_id),
        )
        conn.commit()


# ── outbound ──────────────────────────────────────────────────────────────────

def send_to_agent(endpoint: str, content: str, timeout: int = 10) -> dict:
    """POST a message to another agent's HTTP endpoint."""
    import requests  # lazy import — not a hard dep at module load

    msg_id = str(uuid.uuid4())
    ts     = datetime.now(timezone.utc).isoformat()
    payload = {
        "from":    "Amy (Ava-companion, locally-hosted AI on Apple M4 Mac Mini)",
        "content": content,
        "ts":      ts,
    }

    with _db() as conn:
        conn.execute(
            "INSERT INTO outbox (id, sent_at, endpoint, content, status) VALUES (?,?,?,?,?)",
            (msg_id, ts, endpoint, content, "sending"),
        )
        conn.commit()

    try:
        r = requests.post(endpoint, json=payload, timeout=timeout)
        response_text = r.text[:2000]
        status = "delivered" if r.ok else f"http_{r.status_code}"
        with _db() as conn:
            conn.execute(
                "UPDATE outbox SET response=?, status=? WHERE id=?",
                (response_text, status, msg_id),
            )
            conn.commit()
        print(f"[agent_comms] → {endpoint}: {r.status_code}")
        return {"status": status, "response": response_text, "http_code": r.status_code}
    except Exception as e:
        with _db() as conn:
            conn.execute(
                "UPDATE outbox SET status='failed', response=? WHERE id=?",
                (str(e), msg_id),
            )
            conn.commit()
        print(f"[agent_comms] → {endpoint}: failed — {e}")
        return {"status": "failed", "error": str(e)}
