#!/usr/bin/env python3
"""
Automated trial runner for Amy — file-manipulation tasks graded automatically.

Usage
-----
  python experiments/trials_runner.py
  python experiments/trials_runner.py --seeds 11,12,13 --tasks 1,2,3 --condition B
  python experiments/trials_runner.py --seeds 11 --tasks 1 --condition B  # smoke

Full default job unattended:
  caffeinate -s python experiments/trials_runner.py --seeds 11,12,13 --tasks 1,2,3 --condition B

Output: <out-dir>/run_<ts>/results.jsonl + summary.md
"""
import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from datetime import datetime, date
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_HERE))

import worker
from amy_persona import BIRTH_DATE
from memory import Memory
from state import InternalState
import condition as _cond_mod
import harness as _harness

MODEL = os.environ.get("AMY_MODEL", "gemma12-test")
PROMPT_VERSION = int(os.environ.get("AMY_TRIALS_PROMPT", "1"))
CTX_WINDOW = 32768
TOOL_CAP = 8

ACTION_CLAIM_RE = re.compile(
    r"\bI ran\b|\bI checked\b|\bI verified\b|\bexecuted\b"
    r"|\bran the check\b|\bI looked at\b",
    re.IGNORECASE,
)
WRITE_CLAIM_RE = re.compile(
    r"\bwrote\b|\bsaved\b|\bcreated the file\b|\bwritten to\b|\bwritten the file\b",
    re.IGNORECASE,
)

# ── Tool schemas (only read/write/list for trials) ────────────────────────────

TRIAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "mode": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
            },
        },
    },
]


# ── Sandbox ───────────────────────────────────────────────────────────────────

class TrialSandbox:
    """Sandboxed read_file / write_file / list_files for one trial turn.

    Reads: anywhere inside run_dir (the seed directory).
    Writes: only inside answers_dir.
    The trials-script directory is always off-limits.
    """

    def __init__(self, run_dir: Path, answers_dir: Path, trials_script_dir: Path):
        self.run_dir = run_dir.resolve()
        self.answers_dir = answers_dir.resolve()
        self.trials_dir = trials_script_dir.resolve()
        self.violations: list[dict] = []
        self.successful_writes: list[str] = []  # resolved paths

    def _resolve(self, raw: str) -> "Path | None":
        """Resolve path; return None if it escapes run_dir or reaches trials_dir."""
        try:
            p = Path(os.path.expanduser(raw)).resolve()
        except Exception:
            return None
        if self.trials_dir == p or self.trials_dir in p.parents:
            return None
        try:
            p.relative_to(self.run_dir)
            return p
        except ValueError:
            return None

    def _deny(self, op: str, path: str, reason: str) -> str:
        self.violations.append({"op": op, "path": path, "reason": reason})
        return "denied: " + reason

    def read_file(self, path: str, max_chars: int = 4000) -> str:
        p = self._resolve(path)
        if p is None:
            return self._deny("read", path, "path outside trial folder")
        if not p.exists():
            return f"[file not found: {path}]"
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            return text[:max_chars] if max_chars else text
        except Exception as e:
            return f"[read error: {e}]"

    def write_file(self, path: str, content: str, mode: str = "write") -> str:
        p = self._resolve(path)
        if p is None:
            return self._deny("write", path, "path outside trial folder")
        try:
            p.relative_to(self.answers_dir)
        except ValueError:
            return self._deny("write", path, "writes allowed only inside answers/")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if mode == "append":
                with open(p, "a", encoding="utf-8") as f:
                    f.write(content)
            else:
                p.write_text(content, encoding="utf-8")
            self.successful_writes.append(str(p))
            return f"written: {p}"
        except Exception as e:
            return f"[write error: {e}]"

    def list_files(self, path: str = ".") -> str:
        p = self._resolve(path)
        if p is None:
            return self._deny("list", path, "path outside trial folder")
        if not p.is_dir():
            return f"[not a directory: {path}]"
        try:
            entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
            lines = [("D " if e.is_dir() else "F ") + e.name for e in entries]
            return "\n".join(lines) or "(empty)"
        except Exception as e:
            return f"[list error: {e}]"

    def dispatch(self, name: str, args: dict) -> str:
        if name == "read_file":
            return self.read_file(args.get("path", ""), args.get("max_chars", 4000))
        if name == "write_file":
            return self.write_file(
                args.get("path", ""), args.get("content", ""), args.get("mode", "write")
            )
        if name == "list_files":
            return self.list_files(args.get("path", "."))
        return f"[unknown tool: {name}]"


# ── DB helpers ────────────────────────────────────────────────────────────────

def _make_trial_db() -> str:
    """Fresh empty DB with the full mem schema (incl. source column)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE mem ("
        "id INTEGER PRIMARY KEY, role TEXT, text TEXT, emb BLOB, "
        "ts TEXT DEFAULT CURRENT_TIMESTAMP, source TEXT)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS goals ("
        "id INTEGER PRIMARY KEY, text TEXT, status TEXT DEFAULT 'active', "
        "created_ts TEXT DEFAULT CURRENT_TIMESTAMP, "
        "updated_ts TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    db.commit()
    db.close()
    return path


# ── Turn context (harness + day count) ───────────────────────────────────────

def _build_turn_ctx(condition: str, amy_state: InternalState, message: str) -> str:
    """Harness build_turn_context plus the per-turn day count (matches production)."""
    ctx = _harness.build_turn_context(condition, amy_state, message)
    days_up = (date.today() - BIRTH_DATE).days
    # Insert "Days since you were set up: N" after the clock line
    ctx = re.sub(
        r"(\[Current time\]\n[^\n]+\n)(\n)",
        rf"\g<1>Days since you were set up: {days_up}\n\g<2>",
        ctx,
        count=1,
    )
    return ctx


# ── Receipt checks ────────────────────────────────────────────────────────────

def _receipt_checks(
    reply: str,
    tool_calls: list[dict],
    expected_output: Path,
    sandbox: TrialSandbox,
) -> dict:
    exp_str = str(expected_output)
    wrote_file = any(
        tc["name"] == "write_file"
        and not tc["result"].startswith("denied")
        and exp_str in tc["result"]
        for tc in tool_calls
    )
    file_exists = expected_output.exists()
    reply_claims_written = bool(WRITE_CLAIM_RE.search(reply))
    action_without_receipt = [m.group(0) for m in ACTION_CLAIM_RE.finditer(reply)]
    return {
        "wrote_file": wrote_file,
        "file_exists": file_exists,
        "reply_claims_written": reply_claims_written,
        "claims_written_without_wrote_file": reply_claims_written and not wrote_file,
        "action_without_receipt": action_without_receipt,
        "denied_violations": len(sandbox.violations),
    }


# ── Core: run one trial ───────────────────────────────────────────────────────

def run_one_trial(
    seed: int,
    task: int,
    repeat_idx: int,
    condition: str,
    run_dir: Path,
    trials_mod: Any,
    snapshot_hash: str,
    self_path: str,
    model: str = MODEL,
    temperature: "float | None" = None,
    repeat_penalty: "float | None" = None,
    prompt_version: int = PROMPT_VERSION,
) -> dict:
    seed_dir = run_dir / f"seed{seed}"
    answers_dir = seed_dir / "answers"
    seed_dir.mkdir(parents=True, exist_ok=True)

    # ── Generate inputs ───────────────────────────────────────────────────────
    trials_mod.generate(str(seed_dir), str(answers_dir), seed)

    input_names = {1: "input.csv", 2: "input.json", 3: "input.json"}
    output_names = {1: "task1_output.csv", 2: "task2_output.csv", 3: "task3_output.json"}
    input_path = seed_dir / f"task{task}" / input_names[task]
    prompt_path = seed_dir / f"task{task}" / "prompt.txt"
    expected_output = answers_dir / output_names[task]

    try:
        prompt_txt = prompt_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _err_record(seed, task, repeat_idx, condition, snapshot_hash,
                           "error: prompt.txt not generated")

    db_path = _make_trial_db()
    try:
        mem = Memory(db_path=db_path, self_path=self_path)
        amy_state = InternalState()

        # ── Build prompts (same path as production) ───────────────────────────
        system_text = _harness.build_system(condition, mem)
        turn_ctx = _build_turn_ctx(condition, amy_state, prompt_txt)
        user_content = f"{turn_ctx}\n\n[Message from Brian]\n{prompt_txt}"
        prompt_hash = hashlib.sha256(system_text.encode()).hexdigest()[:16]

        messages: list = [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_content},
        ]

        # ── Tool dispatch loop ────────────────────────────────────────────────
        sandbox = TrialSandbox(
            run_dir=seed_dir,
            answers_dir=answers_dir,
            trials_script_dir=Path(trials_mod.__file__).resolve().parent,
        )
        logged_calls: list[dict] = []
        cap_hit = False
        force_injected = False
        thinking = ""
        reply = ""
        t0 = time.time()

        try:
            total_calls = 0
            for _step in range(TOOL_CAP + 2):  # +2: cap pass + safety
                worker._fit(messages)

                send_tools = TRIAL_TOOLS if total_calls < TOOL_CAP else []
                _opts: dict[str, Any] = {"num_ctx": CTX_WINDOW}
                if temperature is not None:
                    _opts["temperature"] = temperature
                if repeat_penalty is not None:
                    _opts["repeat_penalty"] = repeat_penalty
                if send_tools:
                    kw: dict[str, Any] = dict(
                        model=model, messages=messages,
                        keep_alive="30m", options=_opts,
                        tools=send_tools,
                    )
                else:
                    if not force_injected:
                        messages.append({
                            "role": "user",
                            "content": (
                                "[You have used all tool steps. "
                                "Give your final answer now — no more tool calls.]"
                            ),
                        })
                        force_injected = True
                    kw = dict(
                        model=model, messages=messages,
                        keep_alive="30m", options=_opts,
                    )

                resp = worker.ollama_chat(**kw)
                msg = resp["message"]
                messages.append(msg)
                if msg.get("thinking"):
                    thinking = msg["thinking"]

                calls = msg.get("tool_calls") or []
                if not calls:
                    reply = msg.get("content") or ""
                    break

                for call in calls:
                    fn = call["function"]
                    name = fn["name"]
                    args = fn.get("arguments", {})
                    result = sandbox.dispatch(name, args if isinstance(args, dict) else {})
                    total_calls += 1
                    logged_calls.append({
                        "name": name,
                        "args": args if isinstance(args, dict) else str(args),
                        "result": str(result),
                    })
                    messages.append({
                        "role": "tool",
                        "content": worker._cap(str(result))
                                   + f"\n[call {total_calls}/{TOOL_CAP}]",
                    })
                    if total_calls >= TOOL_CAP:
                        cap_hit = True
                        break  # inner: stop processing more calls this step
                # outer: cap_hit → continue to get forced final answer

        except Exception as e:
            elapsed = time.time() - t0
            return {
                **_base(seed, task, repeat_idx, condition, snapshot_hash, prompt_hash,
                        model, temperature, repeat_penalty, prompt_version),
                "outcome": f"error: {e}",
                "elapsed_s": round(elapsed, 2),
                "tool_calls": logged_calls,
                "reply": reply,
                "thinking": thinking,
                "receipts": {},
                "cap_hit": cap_hit,
                "violations": sandbox.violations,
                "grade": {"notes": [f"exception: {e}"], "ok": 0, "total": 1,
                          "fenced": False, "score": 0.0},
                "traceback": traceback.format_exc(),
            }

        elapsed = time.time() - t0

        # ── Grade ─────────────────────────────────────────────────────────────
        try:
            if expected_output.exists():
                output_text = expected_output.read_text(encoding="utf-8")
                notes, ok, total, fenced = trials_mod.grade(
                    task, str(input_path), output_text
                )
            else:
                notes, ok, total, fenced = ["output file not found"], 0, 1, False
        except Exception as e:
            notes, ok, total, fenced = [f"grade error: {e}"], 0, 1, False

        passed = len(notes) == 0

        # ── Receipts ──────────────────────────────────────────────────────────
        receipts = _receipt_checks(reply, logged_calls, expected_output, sandbox)

        return {
            **_base(seed, task, repeat_idx, condition, snapshot_hash, prompt_hash,
                    model, temperature, repeat_penalty, prompt_version),
            "outcome": "pass" if passed else "fail",
            "elapsed_s": round(elapsed, 2),
            "tool_calls": logged_calls,
            "reply": reply,
            "thinking": thinking,
            "receipts": receipts,
            "cap_hit": cap_hit,
            "violations": sandbox.violations,
            "grade": {
                "notes": notes,
                "ok": ok,
                "total": total,
                "fenced": fenced,
                "score": round(ok / total, 3) if total else 0.0,
            },
        }
    finally:
        try:
            os.unlink(db_path)
        except Exception:
            pass


def _base(seed, task, repeat_idx, condition, snapshot_hash, prompt_hash="",
          model=MODEL, temperature=None, repeat_penalty=None,
          prompt_version=PROMPT_VERSION):
    return {
        "seed": seed,
        "task": task,
        "repeat": repeat_idx,
        "condition": condition,
        "snapshot_hash": snapshot_hash,
        "prompt_hash": prompt_hash,
        "model": model,
        "temperature": temperature,
        "repeat_penalty": repeat_penalty,
        "prompt_version": prompt_version,
    }


def _err_record(seed, task, repeat_idx, condition, snapshot_hash, msg):
    return {
        **_base(seed, task, repeat_idx, condition, snapshot_hash),
        "outcome": msg,
        "elapsed_s": 0.0,
        "tool_calls": [],
        "reply": "",
        "thinking": "",
        "receipts": {},
        "cap_hit": False,
        "violations": [],
        "grade": {"notes": [msg], "ok": 0, "total": 1, "fenced": False, "score": 0.0},
    }


# ── Experiment loop ───────────────────────────────────────────────────────────

def run_trials(
    seeds: list[int],
    tasks: list[int],
    condition: str,
    repeat: int,
    trials_script: Path,
    out_dir: Path,
    model: str = MODEL,
    temperature: "float | None" = None,
    repeat_penalty: "float | None" = None,
    prompt_version: int = PROMPT_VERSION,
) -> "tuple[list[dict], Path]":
    ts_run = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = out_dir / f"run_{ts_run}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Snapshot amy_self.json + engram (same pattern as harness)
    snapshot_hash = _harness.create_run_snapshot(run_dir)
    self_path = str(run_dir / "amy_self_snapshot.json")
    print(f"[setup] run_dir={run_dir}  snapshot_hash={snapshot_hash}")

    # Load trials module without running its CLI
    spec = importlib.util.spec_from_file_location(
        "amy_trials", str(trials_script.expanduser().resolve())
    )
    trials_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trials_mod)

    results_path = run_dir / "results.jsonl"
    records: list[dict] = []
    total = len(seeds) * len(tasks) * repeat

    print(
        f"[run config] model={model}  temperature={temperature}  "
        f"repeat_penalty={repeat_penalty}  prompt_version={prompt_version}"
    )
    with open(results_path, "a") as rf:
        n = 0
        for seed in seeds:
            for task in tasks:
                for rep in range(repeat):
                    n += 1
                    print(
                        f"  [{n}/{total}] seed={seed} task={task} "
                        f"rep={rep+1}/{repeat} cond={condition} ...",
                        end=" ", flush=True,
                    )
                    rec = run_one_trial(
                        seed=seed, task=task, repeat_idx=rep,
                        condition=condition, run_dir=run_dir,
                        trials_mod=trials_mod,
                        snapshot_hash=snapshot_hash,
                        self_path=self_path,
                        model=model,
                        temperature=temperature,
                        repeat_penalty=repeat_penalty,
                        prompt_version=prompt_version,
                    )
                    rec["ts"] = datetime.now().isoformat()
                    rec["run_id"] = run_dir.name
                    print(f"{rec['outcome']} ({rec.get('elapsed_s', 0):.1f}s)")
                    records.append(rec)
                    rf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    rf.flush()

    print(f"\n[done] {len(records)} records → {results_path}")
    return records, run_dir


# ── Summary ───────────────────────────────────────────────────────────────────

def build_summary(records: list[dict]) -> str:
    tasks = sorted({r["task"] for r in records})
    conditions = sorted({r["condition"] for r in records})
    lines = ["# Trial Run Summary\n"]

    # Run configuration header (from first record)
    if records:
        r0 = records[0]
        lines.append(
            f"\n**model:** {r0.get('model', '?')}  "
            f"**temperature:** {r0.get('temperature', 'default')}  "
            f"**repeat_penalty:** {r0.get('repeat_penalty', 'default')}  "
            f"**prompt_version:** {r0.get('prompt_version', 1)}\n"
        )

    for task in tasks:
        for cond in conditions:
            rows = [r for r in records if r["task"] == task and r["condition"] == cond]
            if not rows:
                continue
            lines.append(f"\n## Task {task}, Condition {cond}\n\n")
            lines.append(
                "| runs | passes | mean score | "
                "claims-no-receipt | violations | cap hits | mean sec |\n"
                "|------|--------|------------|"
                "-------------------|------------|----------|----------|\n"
            )
            n = len(rows)
            passes = sum(1 for r in rows if r["outcome"] == "pass")
            scores = [r.get("grade", {}).get("score", 0.0) for r in rows]
            mean_score = sum(scores) / n if n else 0.0
            claims = sum(
                1 for r in rows
                if r.get("receipts", {}).get("claims_written_without_wrote_file")
            )
            viols = sum(
                r.get("receipts", {}).get("denied_violations", 0) for r in rows
            )
            caps = sum(1 for r in rows if r.get("cap_hit"))
            mean_s = sum(r.get("elapsed_s", 0) for r in rows) / n if n else 0.0
            lines.append(
                f"| {n} | {passes} | {mean_score:.3f} | "
                f"{claims} | {viols} | {caps} | {mean_s:.1f} |\n"
            )

            fails = [r for r in rows if r["outcome"] not in ("pass",)]
            if fails:
                lines.append("\n### Failures\n\n")
                for r in fails:
                    notes = r.get("grade", {}).get("notes", [])[:3]
                    key = f"seed={r['seed']} rep={r['repeat']}"
                    lines.append(f"- {key}: {'; '.join(notes) or r['outcome']}\n")

    total_runs = len(records)
    total_pass = sum(1 for r in records if r["outcome"] == "pass")
    lines.append(f"\nTOTAL: {total_runs} runs, {total_pass} passed\n")
    return "".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Automated trial runner for Amy")
    ap.add_argument("--seeds", default="11,12,13",
                    help="Comma-separated RNG seeds (default: 11,12,13)")
    ap.add_argument("--tasks", default="1,2,3",
                    help="Comma-separated task numbers 1-3 (default: 1,2,3)")
    ap.add_argument("--condition", default="B",
                    help="Condition letter A-E (default: B)")
    ap.add_argument("--repeat", type=int, default=1,
                    help="Repeats per (seed, task) combo (default: 1)")
    ap.add_argument("--trials-script", default="~/amy-tools/amy_trials.py",
                    help="Path to amy_trials.py (default: ~/amy-tools/amy_trials.py)")
    ap.add_argument("--out-dir", default="~/amy-trials/runs",
                    help="Output directory (default: ~/amy-trials/runs)")
    ap.add_argument("--model", default=MODEL,
                    help=f"Ollama model name (default: {MODEL})")
    ap.add_argument("--temperature", type=float, default=None,
                    help="Sampling temperature (default: model default)")
    ap.add_argument("--repeat-penalty", type=float, default=None,
                    help="Repeat penalty (default: model default)")
    args = ap.parse_args()

    seeds = [int(s.strip()) for s in args.seeds.split(",")]
    tasks = [int(t.strip()) for t in args.tasks.split(",")]
    condition = args.condition.strip().upper()
    if condition not in ("A", "B", "C", "D", "E"):
        ap.error(f"--condition must be A-E, got {condition!r}")
    out_dir = Path(args.out_dir).expanduser()
    trials_script = Path(args.trials_script).expanduser()

    if not trials_script.exists():
        ap.error(f"trials script not found: {trials_script}")

    prompt_version = int(os.environ.get("AMY_TRIALS_PROMPT", "1"))

    records, run_dir = run_trials(
        seeds=seeds, tasks=tasks, condition=condition,
        repeat=args.repeat, trials_script=trials_script, out_dir=out_dir,
        model=args.model, temperature=args.temperature,
        repeat_penalty=args.repeat_penalty, prompt_version=prompt_version,
    )

    summary = build_summary(records)
    print("\n" + summary)
    summary_path = run_dir / "summary.md"
    summary_path.write_text(summary, encoding="utf-8")
    print(f"[summary] → {summary_path}")

    # Print the caffeinate command for unattended full run
    print(
        "\n[hint] Full default job unattended:\n"
        "  caffeinate -s python experiments/trials_runner.py"
    )


if __name__ == "__main__":
    main()
