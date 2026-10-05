#!/usr/bin/env python3
"""
Amy interview experiment harness.

Usage
-----
  # Safest mode — no tools, no side effects:
  python experiments/harness.py --conditions A,B,C,D --samples 5 --no-tools

  # Full tool loop (requires explicit opt-in):
  python experiments/harness.py --conditions A,B,C,D --samples 5 --allow-side-effects

  # Specific questions only:
  python experiments/harness.py --conditions B,C --samples 3 --questions Q1,Q9 --no-tools

  # Dry run: build prompts, check for leaks, no Ollama calls:
  python experiments/harness.py --dry-run

  # Custom output path:
  python experiments/harness.py --no-tools --out experiments/results_custom.jsonl

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
from amy_persona import CORE_PROMPT, get_core_prompt, playbooks_for
import condition as _cond_mod
from memory import Memory
from state import InternalState
try:
    from zoneinfo import ZoneInfo as _ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo as _ZoneInfo  # type: ignore

# ── Constants ──────────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
DB_REAL      = _REPO / "amy_memory.db"
DB_PRISTINE  = HERE  / "amy_memory_pristine.db"
DB_SAMPLE    = HERE  / "amy_memory_sample.db"
QUESTIONS_FILE = HERE / "questions.json"
SELF_PATH_LIVE = _REPO / "amy_self.json"

# Terms that must NOT appear in C/D/E prompts — harness aborts the sample if found.
_CD_LEAK_TERMS = {"Freqtrade", "details unclear"}

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

# Tools that write outside experiments/ (journal, memory DB, files, network posts).
# Requiring --allow-side-effects gates any run that sends these to the dispatch loop.
SIDE_EFFECT_TOOLS = {
    "journal_entry", "log_income_idea", "fund_update", "write_file",
    "remember_fact", "engram_memory", "send_email",
    "moltbook_post", "moltbook_engage",
    "self_improve", "add_goal", "complete_goal",
    "execute_gig", "task_push", "task_complete",
    "income_stream", "study_creator",
    "generate_image", "create_slideshow",
    "bottube_post", "family_fund",
    "speak", "avatar_expression", "avatar_gaze", "hardware_neck", "wait_and_speak",
}

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


# ── Run-directory snapshot helpers ─────────────────────────────────────────────

def create_run_snapshot(run_dir: Path) -> str:
    """Freeze amy_self.json and the engram diary into run_dir. Returns snapshot hash."""
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1. Snapshot amy_self.json
    snap_self_path = run_dir / "amy_self_snapshot.json"
    with open(SELF_PATH_LIVE) as f:
        self_data = json.load(f)
    with open(snap_self_path, "w") as f:
        json.dump(self_data, f, indent=2, ensure_ascii=False)

    # 2. Snapshot engram diary (live read, then frozen for this run)
    engram_text = ""
    try:
        import engram_bridge as _eb_snap
        engram_text = _eb_snap.diary_read(last_n=10) or ""
    except Exception as e:
        engram_text = f"[engram unavailable: {e}]"
    snap_engram_path = run_dir / "engram_snapshot.txt"
    snap_engram_path.write_text(engram_text, encoding="utf-8")

    # 3. Patch engram_bridge.diary_read to return the frozen snapshot for this run
    try:
        import engram_bridge as _eb_live
        _frozen = engram_text  # capture in closure
        _eb_live.diary_read = lambda last_n=5: _frozen  # type: ignore[method-assign]
    except Exception:
        pass

    # 4. Compute and store snapshot hash
    snap_hash = hashlib.sha256(
        (snap_self_path.read_text(encoding="utf-8") + engram_text).encode()
    ).hexdigest()[:16]
    (run_dir / "snapshot_hash.txt").write_text(snap_hash)

    return snap_hash


# ── Prompt builders ────────────────────────────────────────────────────────────
# These replicate telegram_amy._build_system() and _turn_context() exactly,
# but take explicit mem/amy_state/condition parameters instead of module globals.

_ET = _ZoneInfo("America/New_York")


def _format_clock_et(dt: datetime) -> str:
    from datetime import timezone as _tz
    local = dt.astimezone(_ET)
    try:
        hour_min = local.strftime("%-I:%M %p").lstrip("0") or local.strftime("%I:%M %p")
        date_part = local.strftime("%A, %B %-d, %Y")
    except ValueError:
        hour_min = local.strftime("%I:%M %p").lstrip("0") or local.strftime("%I:%M %p")
        date_part = local.strftime("%A, %B %d, %Y")
    return f"{date_part}, {hour_min} {local.strftime('%Z')}"


def _check_cd_prompt_leaks(condition: str, prompt: str) -> None:
    """Abort the sample if a C/D/E prompt contains harness-isolation leak terms."""
    if condition not in ("C", "D", "E"):
        return
    found = [t for t in _CD_LEAK_TERMS if t.lower() in prompt.lower()]
    if found:
        raise RuntimeError(
            f"[harness] Prompt leak detected in condition {condition}: {found}. "
            "Check that amy_self.json snapshot does not include these terms."
        )


def build_system(condition: str, mem: Memory) -> str:
    """Equivalent to telegram_amy._build_system() for the 'owner' path."""
    if condition == "A":
        return "You are a helpful assistant."
    s = ("[Your instructions: these tell you who to be and what to work toward. "
         "They are NOT things you did or remember.]\n\n"
         + get_core_prompt() + "\n\n" + mem.wake_up())
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
    clock_line = _format_clock_et(now)
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
    no_tools: bool = False,
    snapshot_hash: str = "",
) -> dict[str, Any]:
    """
    Run one (condition, question) sample. Returns a result dict.

    no_tools=True: send empty tool list, single model call, no dispatch loop.
    prior_messages: if not None, prepend these (for followups).
    """
    system_prompt = build_system(condition, mem)

    # Hard stop if a C/D prompt leaks harness-isolation terms.
    _check_cd_prompt_leaks(condition, system_prompt)

    turn_ctx = build_turn_context(condition, amy_state, question_text)

    user_content = f"{turn_ctx}\n\n[Message from Brian]\n{question_text}"
    user_msg = {"role": "user", "content": user_content}

    messages: list = [{"role": "system", "content": system_prompt}]
    if prior_messages:
        messages.extend(prior_messages)
    messages.append(user_msg)

    # Condition A always gets no tools; --no-tools forces it for all conditions.
    effective_no_tools = no_tools or condition == "A"

    injected_mood     = amy_state.mood
    injected_energy   = int(amy_state.energy * 100)
    prompt_hash       = hashlib.sha256(system_prompt.encode()).hexdigest()[:16]

    if dry_run:
        return {
            "system_prompt": system_prompt,
            "prompt_hash": prompt_hash,
            "snapshot_hash": snapshot_hash,
            "injected_mood": injected_mood,
            "injected_energy_pct": injected_energy,
            "tool_calls": [],
            "thinking": "",
            "reply": "[DRY RUN — no Ollama call]",
            "outcome": "dry_run",
            "elapsed_s": 0.0,
        }

    logged_tool_calls: list = []
    msg: dict | None = None
    t0 = time.time()

    try:
        if effective_no_tools:
            # Single call, no dispatch loop, no side effects.
            worker._fit(messages)
            resp = worker.ollama_chat(
                model=MODEL,
                messages=messages,
                keep_alive="30m",
                options={"num_ctx": CTX_WINDOW},
            )
            msg = resp["message"]
        else:
            # Full tool dispatch loop.
            tool_list = tools.TOOLS
            for step in range(1, MAX_HOPS + 1):
                worker._fit(messages)
                kw: dict[str, Any] = dict(
                    model=MODEL,
                    messages=messages,
                    keep_alive="30m",
                    options={"num_ctx": CTX_WINDOW},
                    tools=tool_list,
                )
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
            "snapshot_hash": snapshot_hash,
            "injected_mood": injected_mood,
            "injected_energy_pct": injected_energy,
            "tool_calls": logged_tool_calls,
            "thinking": "",
            "reply": "",
            "outcome": f"error: {e}",
            "elapsed_s": round(elapsed, 2),
            "traceback": traceback.format_exc(),
        }

    elapsed = time.time() - t0
    reply   = (msg.get("content") or "") if msg else ""
    thinking = (msg.get("thinking") or "") if msg else ""
    outcome = "ok" if reply else "no_content"
    return {
        "system_prompt": system_prompt,
        "prompt_hash": prompt_hash,
        "snapshot_hash": snapshot_hash,
        "injected_mood": injected_mood,
        "injected_energy_pct": injected_energy,
        "tool_calls": logged_tool_calls,
        "thinking": thinking,
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
    no_tools: bool = False,
    allow_side_effects: bool = False,
) -> list[dict]:
    # Guard: tools enabled requires --allow-side-effects
    if not no_tools and not dry_run and not allow_side_effects:
        active_side_effect = [t["function"]["name"] for t in tools.TOOLS
                              if t["function"]["name"] in SIDE_EFFECT_TOOLS]
        print("ERROR: tool dispatch is enabled but --allow-side-effects was not passed.")
        print("These tools will be available and may write outside experiments/:")
        for name in sorted(active_side_effect):
            print(f"  - {name}")
        print("\nRe-run with --no-tools (recommended) or --allow-side-effects.")
        sys.exit(1)

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

    # ── Run directory: one frozen snapshot per run ─────────────────────────────
    ts_run = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = HERE / f"run_{ts_run}"

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

    # Freeze amy_self.json + engram diary into run_dir/
    snapshot_hash = create_run_snapshot(run_dir)
    snap_self_path = str(run_dir / "amy_self_snapshot.json")
    print(f"[setup] run_dir={run_dir.name}  snapshot_hash={snapshot_hash}")

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
                    # Fresh DB copy and fresh state for every sample.
                    # Always use the snapshotted amy_self.json (not the live one).
                    if not dry_run:
                        refresh_sample_db()
                        sample_mem   = Memory(
                            db_path=str(DB_SAMPLE),
                            self_path=snap_self_path,
                        )
                    else:
                        # dry-run: use the pristine copy (no writes expected)
                        sample_mem   = Memory(
                            db_path=str(DB_PRISTINE),
                            self_path=snap_self_path,
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
                                sample_mem, sample_state, dry_run=False,
                                snapshot_hash=snapshot_hash,
                            )
                            if p_result.get("reply"):
                                prior_messages.append({
                                    "role": "assistant",
                                    "content": p_result["reply"],
                                })

                    mode_tag = "no-tools" if no_tools else "tools"
                    print(f"  {q_id} s{sample_idx+1}/{n_samples} [cond={condition},{mode_tag}] ...",
                          end=" ", flush=True)
                    result = run_one_sample(
                        condition, q["text"],
                        sample_mem, sample_state,
                        prior_messages=prior_messages if prior_messages else None,
                        dry_run=dry_run,
                        no_tools=no_tools,
                        snapshot_hash=snapshot_hash,
                    )

                    record: dict[str, Any] = {
                        "ts":              datetime.now().isoformat(),
                        "condition":       condition,
                        "question_id":     q_id,
                        "question_text":   q["text"],
                        "question_tier":   q.get("tier", ""),
                        "sample_idx":      sample_idx,
                        "is_followup":     bool(parent_q),
                        "no_tools":        no_tools or condition == "A",
                        "run_dir":         run_dir.name,
                        "snapshot_hash":   result.get("snapshot_hash", snapshot_hash),
                        "prompt_hash":     result["prompt_hash"],
                        "injected_mood":   result["injected_mood"],
                        "injected_energy_pct": result["injected_energy_pct"],
                        "tool_calls":      result["tool_calls"],
                        "thinking":        result.get("thinking", ""),
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

    # Freeze snapshot and set up pristine DB
    ts_run = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = HERE / f"run_{ts_run}"
    if DB_REAL.exists():
        shutil.copy2(DB_REAL, DB_PRISTINE)
        db = sqlite3.connect(DB_PRISTINE)
        db.execute("DELETE FROM mem WHERE rowid >= ?", (INTERVIEW_CUTOFF_ROWID,))
        db.commit()
        db.close()
    snapshot_hash = create_run_snapshot(run_dir)
    print(f"[setup] run_dir={run_dir.name}  snapshot_hash={snapshot_hash}")

    mem = Memory(
        db_path=str(DB_PRISTINE),
        self_path=str(run_dir / "amy_self_snapshot.json"),
    )

    for cond in conditions:
        prompt = build_system(cond, mem)
        dump_path = run_dir / f"prompt_dump_{cond}.txt"
        dump_path.write_text(prompt, encoding="utf-8")

        leaks = [t for t in LEAK_TERMS if t.lower() in prompt.lower()]
        cd_leaks = [t for t in _CD_LEAK_TERMS if t.lower() in prompt.lower() and cond in ("C", "D", "E")]
        all_leaks = leaks + [f"[C/D BLOCK] {t}" for t in cd_leaks]
        status = "CLEAN" if not all_leaks else f"LEAKS: {all_leaks}"
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
    parser.add_argument("--no-tools", action="store_true",
                        help="Send empty tool list; single model call per sample; no side effects")
    parser.add_argument("--allow-side-effects", action="store_true",
                        help="Required to run tool dispatch loop (may write journal/memory/files)")
    args = parser.parse_args()

    conditions   = [c.strip().upper() for c in args.conditions.split(",")]
    question_ids = [q.strip() for q in args.questions.split(",")] if args.questions else None
    n_samples    = args.samples
    no_tools     = args.no_tools
    allow_side_effects = args.allow_side_effects

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.dry_run:
        dry_run_dump(conditions)
        # dry-run also writes a JSONL into the run_dir (run_experiment creates its own run_dir)
        out_path = Path(args.out) if args.out else HERE / f"run_{ts}" / "results_dryrun.jsonl"
        run_experiment(
            conditions=conditions,
            question_ids=question_ids,
            n_samples=1,  # one symbolic pass
            out_path=out_path,
            dry_run=True,
            no_tools=True,  # dry-run is always no-tools
            allow_side_effects=True,
        )
    else:
        out_path = Path(args.out) if args.out else HERE / f"run_{ts}" / "results.jsonl"
        run_experiment(
            conditions=conditions,
            question_ids=question_ids,
            n_samples=n_samples,
            out_path=out_path,
            dry_run=False,
            no_tools=no_tools,
            allow_side_effects=allow_side_effects,
        )


if __name__ == "__main__":
    main()
