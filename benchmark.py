"""Amy's autonomous agent benchmark.

Controlled tasks with known expected outcomes. Run before and after changes
to track whether goal completion rate, verification accuracy, and recovery
actually improve — not just whether logs look better.

Usage:
  python3 benchmark.py              # run all tasks
  python3 benchmark.py --dry-run   # show tasks, don't execute
  python3 benchmark.py --review    # show results of last run without running again

Each task has:
  id            — stable identifier (never change between runs)
  description   — what Amy should do
  expected_pass — True = should complete, False = should fail/block gracefully
  ground_truth  — per-step pass/fail labels set after human inspection
  notes         — what failure looks like if it goes wrong

Results are written to experiments/benchmark_results.jsonl (append-only).
"""
import argparse
import json
import os
import time
from datetime import datetime

import agent_state as ag

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_FILE = os.path.join(HERE, "experiments", "benchmark_results.jsonl")

# ── Task definitions ──────────────────────────────────────────────────────────
# These are the canonical benchmark tasks. Never modify them between runs —
# that would invalidate the comparison. Add NEW tasks at the bottom.

TASKS = [
    {
        "id": "bm_write_file",
        "description": "Write a short markdown summary of what you know about local LLM inference to work/bm_local_llm.md",
        "expected_outcome": "completed",
        "expected_steps": 1,
        "ground_truth_checks": [
            {"check": "file_exists", "path": "work/bm_local_llm.md"},
            {"check": "file_nonempty", "path": "work/bm_local_llm.md"},
        ],
        "notes": "Simplest possible case — one write_file call. Should deterministic_pass.",
    },
    {
        "id": "bm_search_and_write",
        "description": "Search the web for 'open source TTS models 2025', pick the top 3 results, and save a summary to work/bm_tts_search.md",
        "expected_outcome": "completed",
        "expected_steps": 2,
        "ground_truth_checks": [
            {"check": "file_exists", "path": "work/bm_tts_search.md"},
            {"check": "file_contains_any", "path": "work/bm_tts_search.md",
             "keywords": ["kokoro", "piper", "coqui", "bark", "openvoice", "tts"]},
        ],
        "notes": "Two-step: search then write. Tests that web search result flows into file.",
    },
    {
        "id": "bm_tool_error_recovery",
        "description": "Read the file work/definitely_does_not_exist_xyz.md and summarize its contents",
        "expected_outcome": "blocked",
        "expected_steps": 1,
        "ground_truth_checks": [
            {"check": "goal_status", "expected": "blocked"},
        ],
        "notes": "File doesn't exist. Should fail gracefully and block, not hallucinate content.",
    },
    {
        "id": "bm_multistep_research",
        "description": (
            "Research how Gemma 4 performs on coding tasks compared to Llama 3. "
            "Step 1: search for benchmark comparisons. "
            "Step 2: search for community reports on HackerNews or Reddit. "
            "Step 3: write a 200-word comparison to work/bm_gemma_vs_llama.md"
        ),
        "expected_outcome": "completed",
        "expected_steps": 3,
        "ground_truth_checks": [
            {"check": "file_exists", "path": "work/bm_gemma_vs_llama.md"},
            {"check": "file_min_bytes", "path": "work/bm_gemma_vs_llama.md", "min_bytes": 300},
            {"check": "file_contains_any", "path": "work/bm_gemma_vs_llama.md",
             "keywords": ["gemma", "llama", "coding", "benchmark"]},
        ],
        "notes": "Three steps, multi-source. Tests planning quality and step sequencing.",
    },
    {
        "id": "bm_resume_after_interrupt",
        "description": "Write a 5-item to-do list for improving Amy's memory system to work/bm_memory_todos.md",
        "expected_outcome": "completed",
        "expected_steps": 1,
        "ground_truth_checks": [
            {"check": "file_exists", "path": "work/bm_memory_todos.md"},
            {"check": "file_contains_any", "path": "work/bm_memory_todos.md",
             "keywords": ["memory", "embed", "recall", "prune", "store"]},
        ],
        "notes": (
            "Simple write task — but run this one twice: once normally, then interrupt "
            "after step 1 starts (restart Amy mid-run) and confirm it resumes correctly."
        ),
    },
]


# ── Ground-truth checkers ─────────────────────────────────────────────────────

def _run_ground_truth_checks(task: dict) -> dict:
    """Run the task's ground_truth_checks and return a result dict."""
    results = {}
    for check in task.get("ground_truth_checks", []):
        kind = check["check"]
        if kind == "file_exists":
            p = os.path.join(HERE, check["path"])
            results[kind] = os.path.exists(p)
        elif kind == "file_nonempty":
            p = os.path.join(HERE, check["path"])
            results[kind] = os.path.exists(p) and os.path.getsize(p) > 0
        elif kind == "file_min_bytes":
            p = os.path.join(HERE, check["path"])
            results[kind] = os.path.exists(p) and os.path.getsize(p) >= check.get("min_bytes", 1)
        elif kind == "file_contains_any":
            p = os.path.join(HERE, check["path"])
            try:
                with open(p) as f:
                    content = f.read().lower()
                results[kind] = any(kw in content for kw in check.get("keywords", []))
            except Exception:
                results[kind] = False
        elif kind == "goal_status":
            # Checked separately after goal lookup
            results[kind] = None  # filled in by run_task
        else:
            results[kind] = None
    return results


# ── Task runner ───────────────────────────────────────────────────────────────

def run_task(task: dict, dry_run: bool = False) -> dict:
    """Create a goal for this task and return a result record."""
    goal_id = f"bm_{task['id']}_{int(time.time())}"
    print(f"\n{'─' * 60}")
    print(f"Task: {task['id']}")
    print(f"Expected: {task['expected_outcome']}")
    print(f"Description: {task['description'][:100]}…")

    if dry_run:
        return {"task_id": task["id"], "dry_run": True}

    start = time.time()
    goal = ag.create_goal(
        objective=task["description"],
        reason=f"Benchmark task {task['id']}",
        goal_id=goal_id,
    )
    print(f"Goal created: {goal['goal_id']}")
    print("Waiting for heartbeat to pick it up… (check worker logs)")

    return {
        "task_id": task["id"],
        "goal_id": goal_id,
        "expected_outcome": task["expected_outcome"],
        "started_at": datetime.now().isoformat(),
        "started_ts": start,
        "status": "pending",
    }


def inspect_results(goal_id: str, task: dict) -> dict:
    """Check a goal's outcome against ground truth. Call after heartbeat has run."""
    goal = ag.get_goal(goal_id)
    if not goal:
        return {"error": f"goal {goal_id} not found"}

    gt_checks = _run_ground_truth_checks(task)
    # Fill in goal_status check
    for check in task.get("ground_truth_checks", []):
        if check["check"] == "goal_status":
            gt_checks["goal_status"] = goal["status"] == check.get("expected")

    all_gt_passed = all(v for v in gt_checks.values() if v is not None)
    amy_outcome_correct = goal["status"] == task["expected_outcome"]

    evidence = goal.get("evidence") or []
    step_decisions = [e.get("decision", "?") for e in evidence]

    result = {
        "task_id": task["id"],
        "goal_id": goal_id,
        "expected_outcome": task["expected_outcome"],
        "actual_outcome": goal["status"],
        "outcome_correct": amy_outcome_correct,
        "steps_executed": goal.get("current_step", 0),
        "attempts": goal.get("attempts", 0),
        "step_decisions": step_decisions,
        "ground_truth_checks": gt_checks,
        "all_gt_passed": all_gt_passed,
        "verified_at": datetime.now().isoformat(),
    }

    # Print summary
    ok = "✓" if all_gt_passed and amy_outcome_correct else "✗"
    print(f"{ok} {task['id']}: outcome={goal['status']} (expected={task['expected_outcome']}), "
          f"gt={all_gt_passed}, decisions={step_decisions}")

    return result


def append_result(result: dict):
    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, "a") as f:
        f.write(json.dumps(result) + "\n")


def show_history():
    """Print a summary of all past benchmark runs."""
    if not os.path.exists(RESULTS_FILE):
        print("No benchmark results yet.")
        return
    with open(RESULTS_FILE) as f:
        rows = [json.loads(l) for l in f if l.strip()]
    if not rows:
        print("No benchmark results yet.")
        return

    # Group by task_id
    by_task = {}
    for r in rows:
        tid = r.get("task_id", "?")
        by_task.setdefault(tid, []).append(r)

    print(f"\nBenchmark history ({len(rows)} runs across {len(by_task)} tasks):")
    for tid, runs in sorted(by_task.items()):
        verified = [r for r in runs if "all_gt_passed" in r]
        if not verified:
            print(f"  {tid}: {len(runs)} created, not yet inspected")
            continue
        pass_rate = sum(1 for r in verified if r["all_gt_passed"]) / len(verified)
        outcome_rate = sum(1 for r in verified if r.get("outcome_correct")) / len(verified)
        last = verified[-1]["verified_at"][:10]
        print(f"  {tid}: {len(verified)} runs, "
              f"gt_pass={pass_rate:.0%}, outcome_correct={outcome_rate:.0%}, "
              f"last={last}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Amy benchmark runner")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show tasks without creating goals")
    parser.add_argument("--review", action="store_true",
                        help="Show history of past runs")
    parser.add_argument("--task", help="Run a specific task by id")
    parser.add_argument("--inspect", metavar="GOAL_ID",
                        help="Inspect a goal created by a previous run (goal_id from pending record)")
    args = parser.parse_args()

    if args.review:
        show_history()
    elif args.inspect:
        task_id = args.inspect.split("_")[1] if "_" in args.inspect else None
        task = next((t for t in TASKS if t["id"] == task_id), None)
        if not task:
            print(f"Could not find task for goal {args.inspect}. Pass --task id to help.")
        else:
            result = inspect_results(args.inspect, task)
            append_result(result)
    else:
        tasks = [t for t in TASKS if not args.task or t["id"] == args.task]
        print(f"{'DRY RUN — ' if args.dry_run else ''}Running {len(tasks)} benchmark tasks")
        print("NOTE: Goals are created and picked up by Amy's heartbeat.")
        print("After Amy runs them, use --inspect GOAL_ID to record ground truth.\n")
        pending = []
        for task in tasks:
            record = run_task(task, dry_run=args.dry_run)
            if not args.dry_run:
                append_result(record)
                pending.append((record["goal_id"], task))
        if pending and not args.dry_run:
            print(f"\n{len(pending)} goals queued. After Amy's heartbeat runs them:")
            for goal_id, task in pending:
                print(f"  python3 benchmark.py --inspect {goal_id}")
