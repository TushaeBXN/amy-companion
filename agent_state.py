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
    # Migrate existing rows — add columns if missing
    cols = [r[1] for r in db.execute("PRAGMA table_info(agent_goals)").fetchall()]
    if "criteria" not in cols:
        db.execute("ALTER TABLE agent_goals ADD COLUMN criteria TEXT DEFAULT '[]'")
    if "max_hops" not in cols:
        db.execute("ALTER TABLE agent_goals ADD COLUMN max_hops INTEGER DEFAULT 8")
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


def set_ground_truth(goal_id: str, step_index: int, actually_passed: bool,
                     note: str = "") -> dict | None:
    """Externally mark whether a step's decision was correct.

    Call this after inspecting real-world results to build a trustworthy
    verification_accuracy metric. ground_truth is independent of Amy's judgment.
    """
    goal = get_goal(goal_id)
    if not goal:
        return None
    evidence = goal["evidence"]
    for e in evidence:
        if e.get("step") == step_index and "ground_truth" not in e:
            e["ground_truth"] = actually_passed
            e["ground_truth_note"] = note
            break
    _update(goal_id, evidence=json.dumps(evidence))
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
    """Aggregate metrics across all non-abandoned goals.

    Step-level:
      total_steps, passed (det+model), failed, inconclusive
      det_pass, det_fail, model_pass, model_fail — breakdown by method
      verification_accuracy — passed / (passed+failed); NOT ground-truth accuracy

    Goal-level:
      total_goals, completed, blocked, abandoned, active_or_pending
      completion_rate — completed / (completed+blocked)
      avg_attempts_completed — mean attempts for completed goals
      avg_retries_per_step — retries = attempts > 1 per distinct step

    Ground-truth accuracy (only populated when set_ground_truth() has been called):
      gt_labels      — number of steps with ground truth set
      gt_correct     — Amy's decision agreed with ground truth
      gt_accuracy    — gt_correct / gt_labels
      gt_false_pass  — Amy said pass, ground truth says fail (dangerous)
      gt_false_fail  — Amy said fail, ground truth says pass (conservative)
    """
    db = _conn()
    all_goals = [_row_to_dict(r) for r in db.execute("SELECT * FROM agent_goals").fetchall()]

    # Goal-level counts
    by_status = {}
    for g in all_goals:
        by_status[g["status"]] = by_status.get(g["status"], 0) + 1

    completed_goals = [g for g in all_goals if g["status"] == "completed"]
    blocked_goals   = [g for g in all_goals if g["status"] == "blocked"]
    decided_goals   = completed_goals + blocked_goals

    avg_attempts = (
        round(sum(g["attempts"] for g in completed_goals) / len(completed_goals), 1)
        if completed_goals else None
    )
    completion_rate = (
        round(len(completed_goals) / len(decided_goals), 3) if decided_goals else None
    )

    # Step-level counts
    step_counts = {k: 0 for k in ("total", "passed", "failed", "inconclusive",
                                   "det_pass", "det_fail", "model_pass", "model_fail")}
    gt = {"labels": 0, "correct": 0, "false_pass": 0, "false_fail": 0}

    for g in all_goals:
        if g["status"] == "abandoned":
            continue
        seen_steps = {}  # step_idx → attempt count (for retry calc)
        for e in (g.get("evidence") or []):
            step_counts["total"] += 1
            d = e.get("decision", "")
            if "pass" in d:
                step_counts["passed"] += 1
            elif "fail" in d:
                step_counts["failed"] += 1
            else:
                step_counts["inconclusive"] += 1
            for key in ("det_pass", "det_fail", "model_pass", "model_fail"):
                if d == key.replace("_", "_"):
                    step_counts[key] += 1
            # Ground-truth audit
            if "ground_truth" in e:
                gt["labels"] += 1
                amy_passed = e.get("passed", False)
                truth = e["ground_truth"]
                if amy_passed == truth:
                    gt["correct"] += 1
                elif amy_passed and not truth:
                    gt["false_pass"] += 1
                elif not amy_passed and truth:
                    gt["false_fail"] += 1
            idx = e.get("step", -1)
            seen_steps[idx] = seen_steps.get(idx, 0) + 1

    decided_steps = step_counts["passed"] + step_counts["failed"]
    stats = {
        # Goal-level
        "total_goals": len(all_goals),
        "completed": by_status.get("completed", 0),
        "blocked": by_status.get("blocked", 0),
        "abandoned": by_status.get("abandoned", 0),
        "active_or_pending": by_status.get("active", 0) + by_status.get("pending", 0),
        "completion_rate": completion_rate,
        "avg_attempts_completed": avg_attempts,
        # Step-level
        "total_steps": step_counts["total"],
        "passed": step_counts["passed"],
        "failed": step_counts["failed"],
        "inconclusive": step_counts["inconclusive"],
        "det_pass": step_counts["det_pass"],
        "det_fail": step_counts["det_fail"],
        "model_pass": step_counts["model_pass"],
        "model_fail": step_counts["model_fail"],
        "verification_accuracy": round(step_counts["passed"] / decided_steps, 3)
                                  if decided_steps else None,
        # Ground-truth (only meaningful when labels > 0)
        "gt_labels": gt["labels"],
        "gt_correct": gt["correct"],
        "gt_accuracy": round(gt["correct"] / gt["labels"], 3) if gt["labels"] else None,
        "gt_false_pass": gt["false_pass"],
        "gt_false_fail": gt["false_fail"],
    }
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
