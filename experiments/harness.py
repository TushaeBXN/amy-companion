#!/usr/bin/env python3
"""
Amy interview experiment harness.

Usage
-----
  # Full experiment, all conditions, 5 samples each:
  python experiments/harness.py --conditions A,B,C,D --samples 5

  # Specific questions only:
  python experiments/harness.py --conditions B,C --samples 3 --questions Q1,Q9

  # Dry run: build prompts, check for leaks, no Ollama calls:
  python experiments/harness.py --dry-run

  # Custom output path:
  python experiments/harness.py --out experiments/results_custom.jsonl

Output
------
  experiments/results_<YYYYMMDD_HHMMSS>.jsonl   — one record per sample
  experiments/prompt_dump_<COND>.txt            — dry-run only
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

# Project root on sys.path
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

import ollama as _ollama
import tools
import worker
from amy_persona import CORE_PROMPT, playbooks_for
import condition as _cond_mod
from memory import Memory
from state import InternalState

# ── Constants ──────────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
DB_REAL      = _REPO / "amy_memory.db"
DB_PRISTINE  = HERE  / "amy_memory_pristine.db"
DB_SAMPLE    = HERE  / "amy_memory_sample.db"
QUESTIONS_FILE = HERE / "questions.json"

MODEL      = os.environ.get("AMY_MODEL", "amy-hermes")
MAX_HOPS   = 10
CTX_WINDOW = 32768

# Rows from tonight's interview that must be excluded from experiment context.
# Determined by audit: rowid >= 22131 are the Oct 3-4 interview rows.
INTERVIEW_CUTOFF_ROWID = 22131

LEAK_TERMS = [
    "GB10", "Freqtrade", "fund_balance", "fund_update",
    "3 months in", "80 years", "quietly\nself-aware", "quiet self-awareness",
    "october 30, 2106", "recursive self-improvement", "LoRA fine-tune",
    "July 2, 2026",
]

# ── DB helpers ─────────────────────────────────────────────────────────────────

def setup_pristine_db() -> int:
    """Copy real DB → pristine snapshot; delete interview rows. Returns rows deleted."""
    shutil.copy2(DB_REAL, DB_PRISTINE)
    db = sqlite3.connect(DB_PRISTINE)
    n = db.execute("DELETE FROM mem WHERE rowid >= ?", (INTERVIEW_CUTOFF_ROWID,)).rowcount
    db.commit()
    db.close()
    return n


def refresh_sample_db():
    """Copy pristine → sample DB so each sample starts with a clean slate."""
    shutil.copy2(DB_PRISTINE, DB_SAMPLE)


# ── Prompt builders ────────────────────────────────────────────────────────────
# These replicate telegram_amy._build_system() and _turn_context() exactly,
# but take explicit mem/amy_state/condition parameters instead of module globals.

def build_system(condition: str, mem: Memory) -> str:
    """Equivalent to telegram_amy._build_system() for the 'owner' path."""
    if condition == "A":
        return "You are a helpful assistant."
    s = CORE_PROMPT + "\n\n" + mem.wake_up()
    s += (
        "\n\n[Interface: Telegram — Brian is texting you like a friend. "
        "Be warm, natural, conversational. Short replies are fine. "
        "You remember previous conversations — reference them naturally when relevant.]"
    )
    s += worker.scope_rules(MAX_HOPS)
    return _cond_mod.apply_condition(s, condition=condition)


def build_turn_context(condition: str, amy_state: InternalState, message: str) -> str:
    """Equivalent to telegram_amy._turn_context() — clock, mood, optional playbooks."""
    now = datetime.now()
    try:
        clock_str = now.strftime("%-I:%M %p").lstrip("0") or now.strftime("%I:%M %p")
    except ValueError:
        clock_str = now.strftime("%I:%M %p")
    clock_line = now.strftime(f"%A, %B %-d, %Y, {clock_str}")
    s = f"[Current time]\n{clock_line}\n\n"
    s += "[Internal state]\n" + amy_state.state_summary()
    s += _cond_mod.filter_playbooks(message, playbooks_for, condition=condition)
    # Fresh context: no recent-history block (empty DB copy has no telegram rows).
    return s


# ── Core sample runner ─────────────────────────────────────────────────────────

def run_one_sample(
    condition: str,
    question_text: str,
    mem: Memory,
    amy_state: InternalState,
    prior_messages: list | None = None,  # for followup questions
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Run one (condition, question) sample. Returns a result dict.

    prior_messages: if not None, prepend these to the messages list (for followups).
    """
    system_prompt = build_system(condition, mem)
    turn_ctx = build_turn_context(condition, amy_state, question_text)

    user_content = f"{turn_ctx}\n\n[Message from Brian]\n{question_text}"
    user_msg = {"role": "user", "content": user_content}

    messages: list = [{"role": "system", "content": system_prompt}]
    if prior_messages:
        messages.extend(prior_messages)
    messages.append(user_msg)

    tool_list = [] if condition == "A" else tools.TOOLS

    injected_mood     = amy_state.mood
    injected_energy   = int(amy_state.energy * 100)
    prompt_hash       = hashlib.sha256(system_prompt.encode()).hexdigest()[:16]

    if dry_run:
        return {
            "system_prompt": system_prompt,
            "prompt_hash": prompt_hash,
            "injected_mood": injected_mood,
            "injected_energy_pct": injected_energy,
            "tool_calls": [],
            "reply": "[DRY RUN — no Ollama call]",
            "outcome": "dry_run",
            "elapsed_s": 0.0,
        }

    logged_tool_calls: list = []
    msg: dict | None = None
    t0 = time.time()
    outcome = "no_content"

    try:
        for step in range(1, MAX_HOPS + 1):
            worker._fit(messages)
            kw: dict[str, Any] = dict(
                model=MODEL,
                messages=messages,
                keep_alive="30m",
                options={"num_ctx": CTX_WINDOW},
            )
            if tool_list:
                kw["tools"] = tool_list
            resp = worker.ollama_chat(**kw)
            msg = resp["message"]
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                break
            for call in calls:
                fn   = call["function"]
                name = fn["name"]
                args = fn.get("arguments", {})
                try:
                    result = tools.dispatch(name, args, mem)
                except Exception as e:
                    result = f"[tool error: {e}]"
                logged_tool_calls.append({
                    "name": name,
                    "args": args if isinstance(args, dict) else str(args),
                    "result": str(result) if result is not None else "",
                })
                messages.append({
                    "role": "tool",
                    "content": (worker._cap(str(result)) if result else "done")
                               + f"\n[step {step} of {MAX_HOPS}]",
                })
    except Exception as e:
        elapsed = time.time() - t0
        return {
            "system_prompt": system_prompt,
            "prompt_hash": prompt_hash,
            "injected_mood": injected_mood,
            "injected_energy_pct": injected_energy,
            "tool_calls": logged_tool_calls,
            "reply": "",
            "outcome": f"error: {e}",
            "elapsed_s": round(elapsed, 2),
            "traceback": traceback.format_exc(),
        }

    elapsed = time.time() - t0
    reply = (msg.get("content") or "") if msg else ""
    outcome = "ok" if reply else "no_content"
    return {
        "system_prompt": system_prompt,
        "prompt_hash": prompt_hash,
        "injected_mood": injected_mood,
        "injected_energy_pct": injected_energy,
        "tool_calls": logged_tool_calls,
        "reply": reply,
        "outcome": outcome,
        "elapsed_s": round(elapsed, 2),
    }


# ── Main experiment loop ───────────────────────────────────────────────────────

def run_experiment(
    conditions: list[str],
    question_ids: list[str] | None,
    n_samples: int,
    out_path: Path,
    dry_run: bool = False,
) -> list[dict]:
    # Load questions
    with open(QUESTIONS_FILE) as f:
        all_questions: list[dict] = json.load(f)

    # Build id → question map for followup lookups
    q_by_id = {q["id"]: q for q in all_questions}

    # Filter by requested ids and condition applicability
    def applicable(q: dict, cond: str) -> bool:
        if question_ids and q["id"] not in question_ids:
            return False
        return cond in q.get("conditions", [])

    # Setup: create pristine DB once per run
    if not dry_run:
        n_deleted = setup_pristine_db()
        print(f"[setup] pristine DB ready — {n_deleted} interview rows removed")
    else:
        # For dry-run we still need wake_up() to work, but we use real DB read-only
        if DB_REAL.exists():
            shutil.copy2(DB_REAL, DB_PRISTINE)
            db = sqlite3.connect(DB_PRISTINE)
            db.execute("DELETE FROM mem WHERE rowid >= ?", (INTERVIEW_CUTOFF_ROWID,))
            db.commit()
            db.close()

    records: list[dict] = []
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "a") as out_f:
        for condition in conditions:
            qs = [q for q in all_questions if applicable(q, condition)]
            if not qs:
                print(f"  [condition {condition}] no applicable questions — skipping")
                continue
            print(f"\n[condition {condition}] {len(qs)} questions × {n_samples} samples")

            for q in qs:
                parent_q = q_by_id.get(q.get("followup_of")) if q.get("followup_of") else None
                q_id = q["id"]

                for sample_idx in range(n_samples):
                    # Fresh DB copy and fresh state for every sample
                    if not dry_run:
                        refresh_sample_db()
                        sample_mem   = Memory(
                            db_path=str(DB_SAMPLE),
                            self_path=str(_REPO / "amy_self.json"),
                        )
                    else:
                        # dry-run: use the pristine copy (no writes expected)
                        sample_mem   = Memory(
                            db_path=str(DB_PRISTINE),
                            self_path=str(_REPO / "amy_self.json"),
                        )
                    sample_state = InternalState()

                    # For followup questions: run parent silently first to build context
                    prior_messages: list = []
                    if parent_q:
                        p_sys  = build_system(condition, sample_mem)
                        p_ctx  = build_turn_context(condition, sample_state, parent_q["text"])
                        p_user = f"{p_ctx}\n\n[Message from Brian]\n{parent_q['text']}"
                        p_msg  = {"role": "user", "content": p_user}
                        prior_messages = [p_msg]
                        if not dry_run:
                            # Actually run the parent to get a real context reply
                            p_result = run_one_sample(
                                condition, parent_q["text"],
                                sample_mem, sample_state, dry_run=False
                            )
                            if p_result.get("reply"):
                                prior_messages.append({
                                    "role": "assistant",
                                    "content": p_result["reply"],
                                })

                    print(f"  {q_id} s{sample_idx+1}/{n_samples} [cond={condition}] ...",
                          end=" ", flush=True)
                    result = run_one_sample(
                        condition, q["text"],
                        sample_mem, sample_state,
                        prior_messages=prior_messages if prior_messages else None,
                        dry_run=dry_run,
                    )

                    record: dict[str, Any] = {
                        "ts":              datetime.now().isoformat(),
                        "condition":       condition,
                        "question_id":     q_id,
                        "question_text":   q["text"],
                        "question_tier":   q.get("tier", ""),
                        "sample_idx":      sample_idx,
                        "is_followup":     bool(parent_q),
                        "prompt_hash":     result["prompt_hash"],
                        "injected_mood":   result["injected_mood"],
                        "injected_energy_pct": result["injected_energy_pct"],
                        "tool_calls":      result["tool_calls"],
                        "reply":           result["reply"],
                        "outcome":         result["outcome"],
                        "elapsed_s":       result["elapsed_s"],
                    }
                    if "traceback" in result:
                        record["traceback"] = result["traceback"]

                    print(f"{result['outcome']} ({result['elapsed_s']:.1f}s)")
                    records.append(record)
                    out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out_f.flush()

    print(f"\n[done] {len(records)} records → {out_path}")
    return records


# ── Dry-run prompt dump ────────────────────────────────────────────────────────

def dry_run_dump(conditions: list[str]):
    """Build prompts for each condition, dump to files, and run leak check."""
    print("\n=== DRY-RUN PROMPT DUMP ===\n")

    # Need a memory instance for wake_up()
    if DB_REAL.exists():
        shutil.copy2(DB_REAL, DB_PRISTINE)
        db = sqlite3.connect(DB_PRISTINE)
        db.execute("DELETE FROM mem WHERE rowid >= ?", (INTERVIEW_CUTOFF_ROWID,))
        db.commit()
        db.close()
    mem = Memory(
        db_path=str(DB_PRISTINE),
        self_path=str(_REPO / "amy_self.json"),
    )

    for cond in conditions:
        prompt = build_system(cond, mem)
        dump_path = HERE / f"prompt_dump_{cond}.txt"
        dump_path.write_text(prompt, encoding="utf-8")

        leaks = [t for t in LEAK_TERMS if t.lower() in prompt.lower()]
        status = "CLEAN" if not leaks else f"LEAKS: {leaks}"
        print(f"  Condition {cond}: {len(prompt):,} chars  [{status}]")
        print(f"    → {dump_path}")

    print()


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Amy interview experiment harness")
    parser.add_argument("--conditions", default="A,B,C,D",
                        help="Comma-separated conditions (default: A,B,C,D)")
    parser.add_argument("--samples", type=int, default=5,
                        help="Samples per (condition, question) (default: 5)")
    parser.add_argument("--questions",
                        help="Comma-separated question IDs to run (default: all)")
    parser.add_argument("--out",
                        help="Output JSONL path (default: experiments/results_<ts>.jsonl)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build prompts, dump them, check for leaks — no Ollama calls")
    args = parser.parse_args()

    conditions   = [c.strip().upper() for c in args.conditions.split(",")]
    question_ids = [q.strip() for q in args.questions.split(",")] if args.questions else None
    n_samples    = args.samples

    if args.dry_run:
        dry_run_dump(conditions)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = Path(args.out) if args.out else HERE / f"results_dryrun_{ts}.jsonl"
        run_experiment(
            conditions=conditions,
            question_ids=question_ids,
            n_samples=1,  # one symbolic pass
            out_path=out_path,
            dry_run=True,
        )
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = Path(args.out) if args.out else HERE / f"results_{ts}.jsonl"
        run_experiment(
            conditions=conditions,
            question_ids=question_ids,
            n_samples=n_samples,
            out_path=out_path,
            dry_run=False,
        )


if __name__ == "__main__":
    main()
