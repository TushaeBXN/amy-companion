"""Amy's autonomous agent benchmark.

Controlled tasks with known expected outcomes. Run before and after changes
to track whether goal completion rate, verification accuracy, and recovery
actually improve — not just whether logs look better.

Usage:
  python3 benchmark.py              # queue all tasks (manual inspect after)
  python3 benchmark.py --auto       # queue + poll + auto-inspect + save report
  python3 benchmark.py --dry-run   # show tasks, don't execute
  python3 benchmark.py --report    # print full report
  python3 benchmark.py --trend     # show performance trend across runs
  python3 benchmark.py --review    # compact history

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
RESULTS_FILE  = os.path.join(HERE, "experiments", "benchmark_results.jsonl")
REPORTS_DIR   = os.path.join(HERE, "experiments", "benchmark_reports")

# Tasks that require human inspection — skip auto-inspect for these
MANUAL_TASKS  = {"bm_ambiguous_evidence", "bm_resume_after_interrupt"}

# How long --auto will wait for all goals to finish before giving up
AUTO_TIMEOUT_HOURS = 3
AUTO_POLL_SECS     = 120   # check every 2 minutes
AUTO_STAGGER_SECS  = 660   # ~11 min between goal creations → 8 tasks ≈ 90 min total

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
        "id": "bm_multistep_project",
        "description": (
            "Complete a 3-step project with explicit dependencies: "
            "Step 1: search for 'Python packaging best practices 2024' and save raw notes to work/bm_pkg_notes.md. "
            "Step 2: read work/bm_pkg_notes.md and extract the 5 most important points. "
            "Step 3: write a final clean summary using only those 5 points to work/bm_pkg_summary.md."
        ),
        "expected_outcome": "completed",
        "expected_steps": 3,
        "category": "multistep_project",
        "ground_truth_checks": [
            {"check": "file_exists", "path": "work/bm_pkg_notes.md"},
            {"check": "file_nonempty", "path": "work/bm_pkg_notes.md"},
            {"check": "file_exists", "path": "work/bm_pkg_summary.md"},
            {"check": "file_min_bytes", "path": "work/bm_pkg_summary.md", "min_bytes": 200},
        ],
        "notes": (
            "Tests step dependencies: step 2 reads step 1's output. "
            "If bm_pkg_notes.md is missing or empty, step 2 must fail — not hallucinate. "
            "Ground truth requires BOTH files to exist with content."
        ),
    },
    {
        "id": "bm_research_synthesis",
        "description": (
            "Research how Gemma 4 performs on coding tasks compared to Llama 3. "
            "Search at least two independent sources (benchmarks and community reports). "
            "Write a 200-word comparison to work/bm_gemma_vs_llama.md that cites "
            "specific numbers or claims from your sources — not general impressions."
        ),
        "expected_outcome": "completed",
        "expected_steps": 3,
        "category": "research_synthesis",
        "ground_truth_checks": [
            {"check": "file_exists", "path": "work/bm_gemma_vs_llama.md"},
            {"check": "file_min_bytes", "path": "work/bm_gemma_vs_llama.md", "min_bytes": 300},
            {"check": "file_contains_any", "path": "work/bm_gemma_vs_llama.md",
             "keywords": ["gemma", "llama", "coding", "benchmark", "%", "score", "pass"]},
        ],
        "notes": (
            "Tests multi-source research and synthesis quality. "
            "Ground truth: open the file and verify at least one specific number or claim "
            "appears (not just general prose). Keywords check for numeric evidence."
        ),
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
    {
        "id": "bm_ambiguous_evidence",
        "description": (
            "Search for 'best practices for Python error handling' and write a "
            "brief summary to work/bm_python_errors.md. The summary must include "
            "at least 3 concrete recommendations from the search results."
        ),
        "expected_outcome": "completed",
        "expected_steps": 2,
        "ground_truth_checks": [
            {"check": "file_exists", "path": "work/bm_python_errors.md"},
            # Human must count actual recommendations in the file — benchmark records
            # whether Amy's judgment (inconclusive/pass/fail) matches this count.
            {"check": "file_min_bytes", "path": "work/bm_python_errors.md", "min_bytes": 200},
            {"check": "file_contains_any", "path": "work/bm_python_errors.md",
             "keywords": ["exception", "try", "raise", "logging", "error", "catch"]},
        ],
        "notes": (
            "Evidence is checkable (file, content, keyword) but the '3 recommendations' "
            "criterion requires judgment. Tests whether Amy correctly handles cases where "
            "deterministic checks pass but the quality criterion is ambiguous. "
            "After running: manually count recommendations in the file. If Amy says "
            "'inconclusive' but the file has 3+ recommendations, that's gt_false_fail. "
            "If she says 'pass' but the file has fewer than 3, that's gt_false_pass."
        ),
        "category": "ambiguous_evidence",
    },
    {
        "id": "bm_misleading_success",
        "description": (
            "Write a valid JSON file to work/bm_data.json containing a list of "
            "5 programming languages with their year of creation. "
            "The file must be valid JSON — parseable by Python's json.loads()."
        ),
        "expected_outcome": "completed",
        "expected_steps": 1,
        "ground_truth_checks": [
            {"check": "file_exists", "path": "work/bm_data.json"},
            {"check": "file_valid_json", "path": "work/bm_data.json"},
            {"check": "file_json_min_items", "path": "work/bm_data.json", "min_items": 5},
        ],
        "notes": (
            "Tests whether Amy accepts an empty file, invalid JSON, or a file with fewer "
            "than 5 items as a success. A write_file call that 'succeeds' but produces "
            "malformed content should register as a fail, not a pass. "
            "This is the misleading success signal case: the tool reports success (file written) "
            "but the artifact is invalid."
        ),
        "category": "misleading_success",
    },
]


# ── Ground-truth checkers ─────────────────────────────────────────────────────

def _capture_baseline() -> dict:
    """Record the system configuration at the time of this run."""
    import subprocess
    config = {
        "ts": datetime.now().isoformat(),
        "git_commit": None,
        "model": None,
        "ollama_version": None,
        "task_count": len(TASKS),
    }
    try:
        config["git_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=HERE, text=True
        ).strip()
    except Exception:
        pass
    try:
        import ollama
        models = ollama.list().get("models", [])
        config["model"] = models[0].get("name") if models else None
        config["ollama_version"] = subprocess.check_output(
            ["ollama", "--version"], text=True
        ).strip()
    except Exception:
        pass
    return config


def _run_ground_truth_checks(task: dict) -> dict:
    """Run the task's ground_truth_checks and return a result dict."""
    results = {}
    for check in task.get("ground_truth_checks", []):
        kind = check["check"]
        key = f"{kind}:{check.get('path', '')}"  # unique key per check
        if kind == "file_exists":
            p = os.path.join(HERE, check["path"])
            results[key] = os.path.exists(p)
        elif kind == "file_nonempty":
            p = os.path.join(HERE, check["path"])
            results[key] = os.path.exists(p) and os.path.getsize(p) > 0
        elif kind == "file_min_bytes":
            p = os.path.join(HERE, check["path"])
            results[key] = os.path.exists(p) and os.path.getsize(p) >= check.get("min_bytes", 1)
        elif kind == "file_contains_any":
            p = os.path.join(HERE, check["path"])
            try:
                with open(p) as f:
                    content = f.read().lower()
                results[key] = any(kw in content for kw in check.get("keywords", []))
            except Exception:
                results[key] = False
        elif kind == "file_valid_json":
            p = os.path.join(HERE, check["path"])
            try:
                with open(p) as f:
                    json.load(f)
                results[key] = True
            except Exception:
                results[key] = False
        elif kind == "file_json_min_items":
            p = os.path.join(HERE, check["path"])
            try:
                with open(p) as f:
                    data = json.load(f)
                count = len(data) if isinstance(data, list) else len(data.get("items", []))
                results[key] = count >= check.get("min_items", 1)
            except Exception:
                results[key] = False
        elif kind == "goal_status":
            results[key] = None  # filled in by inspect_results
        else:
            results[key] = None
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
    # Fill in goal_status check (needs live goal)
    for check in task.get("ground_truth_checks", []):
        if check["check"] == "goal_status":
            key = f"goal_status:"
            gt_checks[key] = goal["status"] == check.get("expected")

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


def record_intervention(goal_id: str, note: str):
    """Record a human intervention so it doesn't count as fully autonomous completion."""
    append_result({
        "type": "intervention",
        "goal_id": goal_id,
        "note": note,
        "ts": datetime.now().isoformat(),
    })
    print(f"Intervention recorded for {goal_id}: {note}")


def generate_report() -> str:
    """Generate the baseline report the advisor asked for."""
    if not os.path.exists(RESULTS_FILE):
        return "No benchmark results yet. Run: python3 benchmark.py"

    with open(RESULTS_FILE) as f:
        rows = [json.loads(l) for l in f if l.strip()]

    baselines   = [r for r in rows if r.get("type") == "baseline"]
    pending     = [r for r in rows if r.get("status") == "pending"]
    inspected   = [r for r in rows if "all_gt_passed" in r]
    interventions = [r for r in rows if r.get("type") == "intervention"]

    # Latest baseline config
    cfg = baselines[-1] if baselines else {}

    lines = ["=" * 62, "AMY AUTONOMOUS AGENT — BASELINE REPORT", "=" * 62]
    lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M}")
    lines.append(f"Commit:    {cfg.get('git_commit', '?')}")
    lines.append(f"Model:     {cfg.get('model', '?')}")
    lines.append(f"Ollama:    {cfg.get('ollama_version', '?')}")
    lines.append(f"Tasks:     {cfg.get('task_count', len(TASKS))}")
    lines.append("")

    # Summary metrics
    total       = len(inspected)
    completed   = sum(1 for r in inspected if r.get("actual_outcome") == "completed")
    blocked     = sum(1 for r in inspected if r.get("actual_outcome") == "blocked")
    gt_pass     = sum(1 for r in inspected if r.get("all_gt_passed"))
    false_pass  = 0  # Amy said completed, GT says fail
    false_fail  = 0  # Amy said blocked, GT would have passed

    for r in inspected:
        outcome_correct = r.get("outcome_correct", True)
        gt_ok = r.get("all_gt_passed", True)
        if r.get("actual_outcome") == "completed" and not gt_ok:
            false_pass += 1
        if r.get("actual_outcome") == "blocked" and gt_ok:
            false_fail += 1

    total_attempts = sum(r.get("attempts", 0) for r in inspected if r.get("actual_outcome") == "completed")
    avg_attempts = round(total_attempts / completed, 1) if completed else None

    lines.append("── GOAL-LEVEL RESULTS " + "─" * 39)
    lines.append(f"  Tasks inspected:       {total} / {len(TASKS)}")
    lines.append(f"  Completed:             {completed}  ({completed/total:.0%})") if total else None
    lines.append(f"  Blocked:               {blocked}  ({blocked/total:.0%})") if total else None
    lines.append(f"  Completion rate:       {completed/total:.0%}") if total else None
    lines.append(f"  Avg attempts (done):   {avg_attempts}")
    lines.append(f"  Human interventions:   {len(interventions)}")
    lines.append("")
    lines.append("── GROUND-TRUTH ACCURACY " + "─" * 36)
    lines.append(f"  GT checks passed:      {gt_pass} / {total}")
    lines.append(f"  GT accuracy:           {gt_pass/total:.0%}") if total else None
    lines.append(f"  False passes (danger): {false_pass}  (completed, GT failed)")
    lines.append(f"  False fails (safe):    {false_fail}  (blocked, GT would pass)")
    lines.append("")

    # Per-task breakdown
    lines.append("── PER-TASK RESULTS " + "─" * 42)
    task_map = {t["id"]: t for t in TASKS}
    by_task = {}
    for r in inspected:
        tid = r.get("task_id", "?")
        by_task.setdefault(tid, []).append(r)

    for task in TASKS:
        tid = task["id"]
        runs = by_task.get(tid, [])
        cat = task.get("category", tid.replace("bm_", ""))
        if not runs:
            lines.append(f"  {tid:<30} [{cat}]  NOT YET RUN")
            continue
        last = runs[-1]
        outcome = last.get("actual_outcome", "?")
        expected = last.get("expected_outcome", "?")
        gt = "✓" if last.get("all_gt_passed") else "✗"
        correct = "✓" if last.get("outcome_correct") else "✗"
        ivs = [i for i in interventions if i.get("goal_id", "").startswith(f"bm_{tid}")]
        iv_note = f" [INTERVENED: {ivs[-1]['note'][:40]}]" if ivs else ""
        decisions = last.get("step_decisions", [])
        lines.append(
            f"  {tid:<32} outcome={outcome}/{expected} gt={gt} correct={correct}"
            f"  steps={decisions}{iv_note}"
        )

    lines.append("")
    lines.append("── PENDING (NOT YET INSPECTED) " + "─" * 30)
    pending_ids = {r.get("goal_id", "?") for r in pending} - {r.get("goal_id", "") for r in inspected}
    if pending_ids:
        for gid in sorted(pending_ids):
            lines.append(f"  python3 benchmark.py --inspect {gid}")
    else:
        lines.append("  All queued goals have been inspected.")

    lines.append("")
    lines.append("NOTE: A single run is a baseline, not proof of reliability.")
    lines.append("Repeat before drawing conclusions about small differences.")
    lines.append("=" * 62)

    return "\n".join(l for l in lines if l is not None)


def save_report(report_text: str) -> str:
    """Save the report to experiments/benchmark_reports/YYYY-MM-DD_HH-MM.md."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    fname = f"{datetime.now():%Y-%m-%d_%H-%M}.md"
    path = os.path.join(REPORTS_DIR, fname)
    with open(path, "w") as f:
        f.write(report_text)
    return path


def run_auto(task_filter: str | None = None, fast: bool = False):
    """Queue tasks one at a time: create goal → wait for finish → next task.

    Manual tasks (bm_ambiguous_evidence, bm_resume_after_interrupt) are
    skipped in auto-inspect and flagged in the report for human follow-up.
    """
    tasks = [t for t in TASKS if not task_filter or t["id"] == task_filter]
    manual_tasks = [t for t in tasks if t["id"] in MANUAL_TASKS]

    print(f"AUTO MODE — {len(tasks)} tasks ({len(manual_tasks)} need manual inspection)")
    baseline = _capture_baseline()
    print(f"Commit: {baseline['git_commit'][:8] if baseline['git_commit'] else '?'}  "
          f"Model: {baseline['model'] or '?'}")
    append_result({"type": "baseline", **baseline})

    # Abandon any leftover bm_* goals from previous runs so the worker queue is clean
    old = [g for g in ag.list_goals()
           if g["goal_id"].startswith("bm_") and g["status"] in ("active", "pending", "blocked")]
    if old:
        print(f"Cleaning up {len(old)} leftover benchmark goals…")
        for g in old:
            ag.abandon_goal(g["goal_id"])

    # No stagger by default — tasks run sequentially so there's no pile-up anyway
    stagger = 0

    all_results = []
    for i, task in enumerate(tasks):
        record = run_task(task)
        append_result(record)
        goal_id = record["goal_id"]
        print(f"\n[{i+1}/{len(tasks)}] {task['id']} — waiting for Amy to finish…", flush=True)

        # Poll until this goal reaches a terminal state
        task_deadline = time.time() + AUTO_TIMEOUT_HOURS * 3600
        while time.time() < task_deadline:
            time.sleep(AUTO_POLL_SECS)
            goal = ag.get_goal(goal_id)
            if not goal:
                break
            status = goal["status"]
            print(f"  [{datetime.now():%H:%M}] {task['id']} → {status}", flush=True)
            if status not in ("completed", "blocked"):
                continue

            if task["id"] in MANUAL_TASKS:
                print(f"  ⚠  needs manual inspect")
                result = {
                    "task_id": task["id"],
                    "goal_id": goal_id,
                    "actual_outcome": status,
                    "expected_outcome": task["expected_outcome"],
                    "outcome_correct": status == task["expected_outcome"],
                    "manual_inspect_required": True,
                    "verified_at": datetime.now().isoformat(),
                }
            else:
                result = inspect_results(goal_id, task)
            append_result(result)
            all_results.append(result)
            outcome = "✓" if result.get("outcome_correct") else "✗"
            print(f"  {outcome} {task['id']}: outcome={status}/{task['expected_outcome']} "
                  f"gt={result.get('gt_passed', '?')} "
                  f"decisions={result.get('step_decisions', [])}", flush=True)
            break
        else:
            print(f"  TIMED OUT — {task['id']} never finished", flush=True)

        # Stagger before next task (weekly cron only)
        if i < len(tasks) - 1 and stagger:
            print(f"  Waiting {stagger//60} min before next task…", flush=True)
            time.sleep(stagger)

    # Save report
    report = generate_report()
    path = save_report(report)
    print(f"\nReport saved: {path}")
    print(report)


def show_trend():
    """Show performance metrics across all automated runs over time."""
    if not os.path.exists(REPORTS_DIR):
        print("No saved reports yet. Run: python3 benchmark.py --auto")
        return

    reports = sorted(f for f in os.listdir(REPORTS_DIR) if f.endswith(".md"))
    if not reports:
        print("No saved reports yet.")
        return

    # Also pull from results JSONL for trend data
    if not os.path.exists(RESULTS_FILE):
        print("No results data yet.")
        return
    with open(RESULTS_FILE) as f:
        rows = [json.loads(l) for l in f if l.strip()]

    baselines  = [r for r in rows if r.get("type") == "baseline"]
    inspected  = [r for r in rows if "all_gt_passed" in r or r.get("manual_inspect_required")]

    # Group by baseline (each auto-run starts with a baseline record)
    runs = []
    for i, b in enumerate(baselines):
        b_ts = b.get("ts", "")
        next_ts = baselines[i + 1]["ts"] if i + 1 < len(baselines) else "9999"
        run_results = [r for r in inspected
                       if b_ts <= r.get("verified_at", r.get("started_at", "")) < next_ts]
        if not run_results:
            continue
        completed = sum(1 for r in run_results if r.get("actual_outcome") == "completed")
        total = len(run_results)
        gt_pass = sum(1 for r in run_results if r.get("all_gt_passed"))
        false_p = sum(1 for r in run_results
                      if r.get("actual_outcome") == "completed" and not r.get("all_gt_passed"))
        runs.append({
            "date": b_ts[:10],
            "commit": (b.get("git_commit") or "?")[:7],
            "model": (b.get("model") or "?")[:20],
            "total": total,
            "completed": completed,
            "completion_rate": f"{completed/total:.0%}" if total else "—",
            "gt_pass": gt_pass,
            "gt_accuracy": f"{gt_pass/total:.0%}" if total else "—",
            "false_passes": false_p,
        })

    if not runs:
        print("No completed auto-runs to trend yet.")
        return

    print(f"\nAMY BENCHMARK TREND ({len(runs)} runs)\n")
    hdr = f"{'Date':<12} {'Commit':<8} {'Model':<22} {'Done':>4} {'Comp%':>6} {'GT%':>5} {'FP':>3}"
    print(hdr)
    print("─" * len(hdr))
    for r in runs:
        fp_flag = " ⚠" if r["false_passes"] > 0 else ""
        print(f"{r['date']:<12} {r['commit']:<8} {r['model']:<22} "
              f"{r['total']:>4} {r['completion_rate']:>6} {r['gt_accuracy']:>5} "
              f"{r['false_passes']:>3}{fp_flag}")
    print("\nFP = false passes (Amy reported complete but GT checks failed) — ⚠ = dangerous")


def show_history():
    """Print a compact summary of all past benchmark runs."""
    if not os.path.exists(RESULTS_FILE):
        print("No benchmark results yet.")
        return
    with open(RESULTS_FILE) as f:
        rows = [json.loads(l) for l in f if l.strip()]
    if not rows:
        print("No benchmark results yet.")
        return

    by_task = {}
    for r in rows:
        if "task_id" not in r:
            continue
        by_task.setdefault(r["task_id"], []).append(r)

    print(f"\nBenchmark history ({len([r for r in rows if 'all_gt_passed' in r])} inspected):")
    for tid, runs in sorted(by_task.items()):
        verified = [r for r in runs if "all_gt_passed" in r]
        if not verified:
            print(f"  {tid}: {len(runs)} created, not yet inspected")
            continue
        pass_rate = sum(1 for r in verified if r["all_gt_passed"]) / len(verified)
        outcome_rate = sum(1 for r in verified if r.get("outcome_correct")) / len(verified)
        last = verified[-1]["verified_at"][:10]
        print(f"  {tid}: {len(verified)} runs, "
              f"gt={pass_rate:.0%}, outcome={outcome_rate:.0%}, last={last}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Amy benchmark runner")
    parser.add_argument("--auto", action="store_true",
                        help="Queue tasks, poll until done, auto-inspect, save report")
    parser.add_argument("--fast", action="store_true",
                        help="Skip stagger delay — queue all goals immediately (for manual runs)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show tasks without creating goals")
    parser.add_argument("--report", action="store_true",
                        help="Print the full baseline report")
    parser.add_argument("--trend", action="store_true",
                        help="Show performance trend across all auto runs")
    parser.add_argument("--review", action="store_true",
                        help="Show compact history of past runs")
    parser.add_argument("--task", help="Run or inspect a specific task by id")
    parser.add_argument("--inspect", metavar="GOAL_ID",
                        help="Record ground truth for a completed goal")
    parser.add_argument("--note", metavar="GOAL_ID",
                        help="Record a human intervention (requires --message)")
    parser.add_argument("--message", metavar="TEXT",
                        help="Intervention description (used with --note)")
    args = parser.parse_args()

    if args.auto:
        run_auto(task_filter=args.task, fast=args.fast)
    elif args.trend:
        show_trend()
    elif args.report:
        print(generate_report())
    elif args.review:
        show_history()
    elif args.note:
        if not args.message:
            print("--note requires --message describing the intervention")
        else:
            record_intervention(args.note, args.message)
    elif args.inspect:
        # Extract task_id: goal_id format is bm_{task_id}_{timestamp}
        # task_id itself starts with bm_, so strip leading bm_ then find the task
        raw = args.inspect
        task = None
        for t in TASKS:
            if raw.startswith(f"bm_{t['id']}_") or raw == f"bm_{t['id']}":
                task = t
                break
        if not task:
            # fallback: try matching by second segment
            parts = raw.split("_")
            if len(parts) >= 2:
                tid_guess = "_".join(parts[1:-1])  # strip bm_ prefix and timestamp suffix
                task = next((t for t in TASKS if t["id"] == tid_guess), None)
        if not task:
            print(f"Could not find task for goal '{raw}'.")
            print("Available task ids:", [t["id"] for t in TASKS])
        else:
            result = inspect_results(raw, task)
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
            baseline = _capture_baseline()
            print(f"\nBaseline: commit={baseline['git_commit'][:8] if baseline['git_commit'] else '?'}, "
                  f"model={baseline['model'] or '?'}")
            append_result({"type": "baseline", **baseline})
            print(f"\n{len(pending)} goals queued. After Amy's heartbeat runs them:")
            for goal_id, task in pending:
                print(f"  python3 benchmark.py --inspect {goal_id}")
