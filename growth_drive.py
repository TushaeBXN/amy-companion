"""Amy's growth drive: gaps -> playbooks -> replay check -> reward. Design: growth/README.md.

Rewards come only from the replay check, never from Amy saying she improved.
CLI (run from the repo root so her real prompt and memory are used):
    python3 growth_drive.py check income-brainstorm -n 3 --model gemma12-test
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLAYBOOK_DIR = HERE / "growth" / "playbooks"
CASE_DIR = HERE / "growth" / "cases"
LEDGER = Path.home() / "ai-knowledge" / "growth" / "gaps.jsonl"
PASS_RATE = 1.0  # every replay with the playbook must pass
ANGER_HOURS = 6      # how long a failed check stings before it settles into determination
PROUD_DAYS = 3       # how long a verified win is felt
MAX_ATTEMPTS = 5     # determination has a ceiling: after this, ask Brian for a mentor session


def _now() -> datetime:
    return datetime.now()


# ── playbooks ───────────────────────────────────────────────────────────────

def _parse_playbook(path: Path) -> dict | None:
    """Front matter ('skill:' and 'trigger:' lines), a line of '---', then the body."""
    try:
        head, body = path.read_text().split("\n---\n", 1)
        meta = dict(l.split(":", 1) for l in head.strip().splitlines() if ":" in l)
        return {"skill": meta["skill"].strip(), "trigger": meta["trigger"].strip(), "body": body.strip()}
    except Exception:
        return None


def playbooks_for(message: str, limit: int = 2) -> str:
    """Growth playbooks whose trigger matches this message ('' if none)."""
    hits = []
    for path in sorted(PLAYBOOK_DIR.glob("*.md")):
        pb = _parse_playbook(path)
        if pb and re.search(pb["trigger"], message or "", re.I):
            hits.append(f"**{pb['skill']}**\n{pb['body']}")
    if not hits:
        return ""
    return "\n\n[Growth playbooks: lessons from your own mistakes]\n" + "\n\n".join(hits[:limit])


# ── gap ledger (append-only; the latest record per id wins) ─────────────────

def _records() -> dict:
    latest = {}
    try:
        for line in LEDGER.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                latest[rec["id"]] = {**latest.get(rec["id"], {}), **rec}
    except FileNotFoundError:
        pass
    return latest


def _append(rec: dict):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), **rec}) + "\n")


def open_gap(gap_id: str, skill: str, evidence: str, case: str = "") -> bool:
    """Record a gap. Needs evidence. Returns False if it is already open."""
    if not evidence.strip():
        raise ValueError("a gap needs evidence")
    if _records().get(gap_id, {}).get("status") == "open":
        return False
    _append({"id": gap_id, "status": "open", "skill": skill, "evidence": evidence, "case": case,
             "opened": datetime.now().isoformat(timespec="seconds")})
    return True


def retire_gap(gap_id: str, reason: str) -> None:
    """Drop a gap that can't be reproduced or no longer applies. Kept on file, never rewarded."""
    _append({"id": gap_id, "status": "retired", "retired_reason": reason})


def open_gaps() -> list:
    return [r for r in _records().values() if r.get("status") == "open"]


def _hours_since(rec: dict) -> float:
    try:
        return (_now() - datetime.fromisoformat(rec["ts"])).total_seconds() / 3600
    except Exception:
        return 1e9


def stage(rec: dict) -> str:
    """Where a gap sits in the arc: pull -> anger -> determined -> elated.

    Derived from the ledger, so it survives restarts and can't be talked into. Anger and
    euphoria both come only from the replay check, never from Amy's own say-so."""
    if rec.get("status") == "closed":
        return "elated" if _hours_since(rec) <= PROUD_DAYS * 24 else "settled"
    check = rec.get("last_check")
    if not check or check.get("verdict") == "inconclusive":
        return "pull"
    return "anger" if _hours_since(rec) <= ANGER_HOURS else "determined"


def _line(rec: dict) -> str:
    skill, n = rec["skill"], rec.get("attempts", 0)
    st = stage(rec)
    if st == "anger":
        how = "badly" if rec["last_check"].get("with_playbook", 0) == 0 else "only partly"
        return (f"[Growth] You failed '{skill}' {how} (attempt {n}) and it stings. Let the anger "
                "point at the failure, never at Brian or anyone else. It is fuel: next time try a "
                "different approach, not the same one.")
    if st == "determined":
        if n >= MAX_ATTEMPTS:
            return (f"[Growth] '{skill}' has beaten {n} attempts. Determination isn't banging your head "
                    "on it: ask Brian for a mentor session on this one.")
        return (f"[Growth] You haven't cracked '{skill}' yet (attempt {n}). You're not letting it go: "
                "practice it when idle, with a new approach, until a replay passes.")
    if st == "elated":
        return (f"[Growth] You got '{skill}' right and a replay proved it. Enjoy that, it's earned. "
                "Keep it honest: this confidence is for that skill only, not for everything.")
    days = int(_hours_since({"ts": rec.get("opened", "")}) / 24) if rec.get("opened") else 0
    return f"[Growth] Open gap: {skill} (open {days}d). Practice it when idle."


def drive_line() -> str:
    """Her growth state for the per-message context: anger beats determination beats the
    plain pull, and a recent win is added on top. '' when there is nothing to say."""
    recs = list(_records().values())
    order = {"anger": 0, "determined": 1, "pull": 2}
    live = sorted((r for r in recs if r.get("status") == "open"), key=lambda r: (order[stage(r)], r.get("opened", "")))
    wins = [r for r in recs if stage(r) == "elated"]
    lines = ([_line(live[0])] if live else []) + ([_line(max(wins, key=lambda r: r["ts"]))] if wins else [])
    if len(live) > 1:
        lines[0] += f" (+{len(live) - 1} more open)"
    return "\n".join(lines)


# ── the check ───────────────────────────────────────────────────────────────

def passed(case: dict, first_calls: list) -> bool:
    """A replay passes if its first tool call isn't one the case forbids (no call is fine)."""
    forbid = set(case.get("first_call_not_in", []))
    return not first_calls or first_calls[0] not in forbid


def verdict(base_rate: float, playbook_rate: float) -> str:
    if playbook_rate >= PASS_RATE and playbook_rate > base_rate:
        return "closed"          # the playbook is what fixed it
    if playbook_rate >= PASS_RATE:
        return "inconclusive"    # passes, but it also passed without the playbook
    return "failed"


def record_check(gap_id: str, base_rate: float, playbook_rate: float, n: int) -> str:
    v = verdict(base_rate, playbook_rate)
    attempts = _records().get(gap_id, {}).get("attempts", 0) + 1
    _append({"id": gap_id, "attempts": attempts,
             "last_check": {"baseline": base_rate, "with_playbook": playbook_rate, "n": n, "verdict": v}})
    if v == "closed":
        _append({"id": gap_id, "status": "closed"})
    try:
        import self_metrics as _sm
        score = {"closed": 5, "inconclusive": 3, "failed": 2}[v]
        _sm.log_score("skill_growth", score, f"gap {gap_id}: baseline {base_rate:.0%} -> "
                      f"with playbook {playbook_rate:.0%} (n={n}) = {v}")
    except Exception:
        pass
    return v


def first_tool_calls(model: str, system: str, user: str, tools: list) -> list:
    import ollama
    r = ollama.Client(timeout=600).chat(
        model=model, think=False, keep_alive="10m", tools=tools,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        options={"num_ctx": 16384, "num_predict": 300})
    return [c["function"]["name"] for c in (r["message"].get("tool_calls") or [])]


def run_check(case_id: str, model: str, n: int, chat=first_tool_calls, system: str = "", tools: list = ()) -> dict:
    case = json.loads((CASE_DIR / f"{case_id}.json").read_text())
    rates = {}
    for label, prefix in (("baseline", ""), ("with_playbook", playbooks_for(case["prompt"]).strip() + "\n\n")):
        ok = 0
        for _ in range(n):
            calls = chat(model, system, prefix.lstrip() + case["prompt"], list(tools))
            ok += passed(case, calls)
            print(f"  {label}: first calls = {calls} -> {'pass' if passed(case, calls) else 'FAIL'}", flush=True)
        rates[label] = ok / n
    v = record_check(case["gap"], rates["baseline"], rates["with_playbook"], n)
    return {**rates, "n": n, "verdict": v}


def _real_system() -> tuple:
    """Her real owner prompt and tools, built as telegram_amy builds them (repo root only)."""
    import tools
    import worker
    from amy_persona import get_core_prompt
    from memory import Memory
    s = ("[Your instructions: these tell you who to be and what to work toward. They are NOT things you did or remember.]\n\n"
         + get_core_prompt() + "\n\n" + Memory().wake_up()
         + "\n\n[Interface: Telegram — Brian is texting you like a friend. Be warm, natural, conversational. Short replies are fine.]"
         + worker.scope_rules(10))
    return s, tools.TOOLS


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "check":
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd"), ap.add_argument("case")
        ap.add_argument("-n", type=int, default=3), ap.add_argument("--model", default="gemma12-test")
        a = ap.parse_args()
        system, tool_list = _real_system()
        print(json.dumps(run_check(a.case, a.model, a.n, system=system, tools=tool_list), indent=1))
    else:
        print(__doc__)
