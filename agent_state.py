"""Amy's persistent goal manager.

Goals survive restarts, interface switches, and mid-session interruptions.
All three reasoning loops (browser, Telegram, worker) read from the same table.

Schema
------
agent_goals table in amy_memory.db:
  id           — auto PK
  goal_id      — short human-readable slug (e.g. "research_ai_tools_2026")
  objective    — what Amy wants to accomplish (free text)
  reason       — why it matters
  plan         — JSON list of step description strings
  criteria     — JSON list of {expected, evidence_required} dicts, parallel to plan
  current_step — int index into plan (0-based)
  status       — pending | active | blocked | completed | abandoned
  next_action  — what she should do on the next execution (free text)
  evidence     — JSON list of evidence records (see below)
  attempts     — total execution attempts
  created_at   — ISO timestamp
  updated_at   — ISO timestamp

Evidence record fields
----------------------
  step              — step index (0-based)
  desc              — step description
  expected          — what a successful result should look like
  evidence_required — what would prove it worked (specific, checkable)
  observed          — what the tool/model actually returned (truncated)
  decision          — "pass" | "fail" | "inconclusive"
  passed            — bool
  ts                — ISO timestamp

Status transitions
------------------
  pending  → active (when a loop picks it up)
  active   → completed (all steps done)
  active   → blocked (stuck after max retries per step)
  active   → pending (interrupted mid-step, will resume)
  blocked  → active (Brian intervenes or Amy retries)
  any      → abandoned (Brian or Amy cancels)
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime

HERE = os.path.dirname(__file__)
DB_PATH = os.path.join(HERE, "amy_memory.db")

MAX_STEP_ATTEMPTS = 3   # retries before a step is marked blocked


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def _conn():
    """Open a connection and ensure the table exists."""
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS agent_goals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id      TEXT    NOT NULL UNIQUE,
            objective    TEXT    NOT NULL,
            reason       TEXT    DEFAULT '',
            plan         TEXT    DEFAULT '[]',
            criteria     TEXT    DEFAULT '[]',
            current_step INTEGER DEFAULT 0,
            status       TEXT    DEFAULT 'pending',
            next_action  TEXT    DEFAULT '',
            evidence     TEXT    DEFAULT '[]',
            attempts     INTEGER DEFAULT 0,
            created_at   TEXT    NOT NULL,
            updated_at   TEXT    NOT NULL
        )
    """)
    # Migrate existing rows — add criteria column if missing
    cols = [r[1] for r in db.execute("PRAGMA table_info(agent_goals)").fetchall()]
    if "criteria" not in cols:
        db.execute("ALTER TABLE agent_goals ADD COLUMN criteria TEXT DEFAULT '[]'")
    db.commit()
    return db


# ── Public API ────────────────────────────────────────────────────────────────

def create_goal(objective: str, reason: str = "", plan: list[str] | None = None,
                goal_id: str = "") -> dict:
    """Create a new goal and return it as a dict.

    Args:
        objective: What Amy wants to accomplish.
        reason:    Why it matters.
        plan:      Ordered list of step descriptions. Amy will fill this in if empty.
        goal_id:   Optional slug; auto-generated if blank.
    """
    if not goal_id:
        slug = objective[:40].lower().replace(" ", "_")
        slug = "".join(c for c in slug if c.isalnum() or c == "_")
        goal_id = f"{slug}_{uuid.uuid4().hex[:6]}"
    plan = plan or []
    now = _now()
    db = _conn()
    db.execute(
        "INSERT INTO agent_goals (goal_id, objective, reason, plan, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        (goal_id, objective, reason, json.dumps(plan), now, now),
    )
    db.commit()
    return get_goal(goal_id)


def get_goal(goal_id: str) -> dict | None:
    """Fetch a single goal by its slug."""
    db = _conn()
    row = db.execute("SELECT * FROM agent_goals WHERE goal_id = ?", (goal_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_goals(status: str | None = None) -> list[dict]:
    """List goals, optionally filtered by status."""
    db = _conn()
    if status:
        rows = db.execute(
            "SELECT * FROM agent_goals WHERE status = ? ORDER BY updated_at DESC", (status,)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM agent_goals ORDER BY updated_at DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_active_goal() -> dict | None:
    """Return the most-recently-updated active or pending goal, or None."""
    db = _conn()
    row = db.execute(
        "SELECT * FROM agent_goals WHERE status IN ('active', 'pending') "
        "ORDER BY status DESC, updated_at DESC LIMIT 1"
    ).fetchone()
    return _row_to_dict(row) if row else None


def set_plan(goal_id: str, plan: list[str],
             criteria: list[dict] | None = None) -> dict | None:
    """Set or replace Amy's plan for a goal.

    criteria is a list parallel to plan, each entry:
        {"expected": "...", "evidence_required": "..."}
    Missing entries are filled with empty dicts.
    """
    criteria = criteria or []
    # Pad criteria to match plan length
    while len(criteria) < len(plan):
        criteria.append({"expected": "", "evidence_required": ""})
    db = _conn()
    db.execute(
        "UPDATE agent_goals SET plan = ?, criteria = ?, current_step = 0, updated_at = ? "
        "WHERE goal_id = ?",
        (json.dumps(plan), json.dumps(criteria), _now(), goal_id),
    )
    db.commit()
    return get_goal(goal_id)


def mark_active(goal_id: str) -> dict | None:
    """Mark a goal as actively being worked on."""
    _update(goal_id, status="active")
    return get_goal(goal_id)


def record_step_result(goal_id: str, step_index: int, result: str,
                       next_action: str = "", advance: bool = True,
                       observed: str = "", decision: str = "inconclusive",
                       passed: bool | None = None) -> dict | None:
    """Record the outcome of executing one step.

    Args:
        goal_id:     The goal being worked.
        step_index:  Which step this result belongs to (0-based).
        result:      What the tool/model returned (used as observed if observed not set).
        next_action: What Amy should do next time she picks this up.
        advance:     If True, increment current_step (step succeeded).
        observed:    The actual observed output (file content, exit code, etc.).
        decision:    "pass" | "fail" | "inconclusive"
        passed:      Explicit bool; inferred from decision if None.
    """
    goal = get_goal(goal_id)
    if not goal:
        return None
    if passed is None:
        passed = decision == "pass"
    criteria_list = goal.get("criteria") or []
    crit = criteria_list[step_index] if step_index < len(criteria_list) else {}
    plan = goal.get("plan") or []
    step_desc = plan[step_index] if step_index < len(plan) else ""
    evidence = goal["evidence"]
    evidence.append({
        "step": step_index,
        "desc": step_desc,
        "expected": crit.get("expected", ""),
        "evidence_required": crit.get("evidence_required", ""),
        "observed": (observed or result)[:600],
        "decision": decision,
        "passed": passed,
        "ts": _now(),
    })
    new_step = (step_index + 1) if advance else step_index
    _update(goal_id,
            evidence=json.dumps(evidence),
            current_step=new_step,
            next_action=next_action,
            attempts=goal["attempts"] + 1)
    return get_goal(goal_id)


def complete_goal(goal_id: str, summary: str = "") -> dict | None:
    """Mark a goal as completed."""
    _update(goal_id, status="completed",
            next_action=summary or "Goal completed.")
    return get_goal(goal_id)


def block_goal(goal_id: str, reason: str = "") -> dict | None:
    """Mark a goal as blocked (stuck, needs Brian's input)."""
    _update(goal_id, status="blocked",
            next_action=reason or "Blocked — needs input.")
    return get_goal(goal_id)


def abandon_goal(goal_id: str) -> dict | None:
    """Cancel a goal entirely."""
    _update(goal_id, status="abandoned")
    return get_goal(goal_id)


def resume_goal(goal_id: str) -> dict | None:
    """Re-activate a blocked or pending goal."""
    _update(goal_id, status="active")
    return get_goal(goal_id)


def goal_summary(goal: dict) -> str:
    """One-paragraph text summary of a goal — for Amy's context window."""
    plan = goal.get("plan") or []
    criteria_list = goal.get("criteria") or []
    step = goal.get("current_step", 0)
    total = len(plan)
    current_desc = plan[step] if step < total else "(all steps done)"
    current_crit = criteria_list[step] if step < len(criteria_list) else {}
    lines = [
        f"Goal [{goal['goal_id']}]: {goal['objective']}",
        f"Reason: {goal['reason'] or '—'}",
        f"Status: {goal['status']}  |  Step {step + 1} of {total}",
        f"Current step: {current_desc}",
    ]
    if current_crit.get("expected"):
        lines.append(f"Expected result: {current_crit['expected']}")
    if current_crit.get("evidence_required"):
        lines.append(f"Evidence required: {current_crit['evidence_required']}")
    lines.append(f"Next action: {goal['next_action'] or '—'}")
    evidence = goal.get("evidence") or []
    if evidence:
        last = evidence[-1]
        decision = last.get("decision", "?")
        lines.append(
            f"Last result (step {last['step'] + 1}): [{decision.upper()}] "
            f"{last.get('observed', last.get('result', ''))[:200]}"
        )
    return "\n".join(lines)


def outcome_stats() -> dict:
    """Aggregate pass/fail/inconclusive counts across all completed and blocked goals.

    Returns a dict suitable for tracking improvement over time:
      total_goals, completed, blocked, total_steps, passed, failed, inconclusive,
      verification_accuracy (passed / (passed + failed), excludes inconclusive)
    """
    db = _conn()
    rows = db.execute(
        "SELECT evidence FROM agent_goals WHERE status IN ('completed', 'blocked')"
    ).fetchall()
    stats = {"total_goals": len(rows), "completed": 0, "blocked": 0,
             "total_steps": 0, "passed": 0, "failed": 0, "inconclusive": 0}
    for row in db.execute("SELECT status, COUNT(*) FROM agent_goals GROUP BY status").fetchall():
        if row[0] in ("completed", "blocked"):
            stats[row[0]] = row[1]
    for row in rows:
        try:
            evidence = json.loads(row[0] or "[]")
        except Exception:
            evidence = []
        for e in evidence:
            stats["total_steps"] += 1
            d = e.get("decision", "")
            if "pass" in d:
                stats["passed"] += 1
            elif "fail" in d:
                stats["failed"] += 1
            else:
                stats["inconclusive"] += 1
    decided = stats["passed"] + stats["failed"]
    stats["verification_accuracy"] = round(stats["passed"] / decided, 3) if decided else None
    return stats


def all_goals_summary() -> str:
    """Compact view of all non-abandoned goals for Amy's heartbeat decision."""
    goals = [g for g in list_goals() if g["status"] != "abandoned"]
    if not goals:
        return "No active goals."
    lines = []
    for g in goals:
        plan = g.get("plan") or []
        step = g.get("current_step", 0)
        total = len(plan)
        lines.append(
            f"[{g['status'].upper()}] {g['goal_id']} — {g['objective'][:60]} "
            f"(step {step + 1}/{total}, {g['attempts']} attempts)"
        )
    return "\n".join(lines)


# ── Internals ─────────────────────────────────────────────────────────────────

def _update(goal_id: str, **fields):
    if not fields:
        return
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [goal_id]
    db = _conn()
    db.execute(f"UPDATE agent_goals SET {set_clause} WHERE goal_id = ?", values)
    db.commit()


def _row_to_dict(row) -> dict:
    d = dict(row)
    for field in ("plan", "criteria", "evidence"):
        try:
            d[field] = json.loads(d[field] or "[]")
        except (json.JSONDecodeError, TypeError):
            d[field] = []
    return d
