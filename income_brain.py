"""
Income decision-making layer.
Evaluates ideas before pursuit, tracks active streams, runs retrospectives.
All written to ~/ai-knowledge/income/ — shared across all models.
"""
import json
import re
from datetime import datetime
from pathlib import Path

INCOME_DIR = Path.home() / "ai-knowledge" / "income"
IDEAS_DIR = INCOME_DIR / "ideas"
ACTIVE_DIR = INCOME_DIR / "active"
RETRO_DIR = INCOME_DIR / "retrospectives"


def _slug(text: str) -> str:
    return re.sub(r"[^\w\-]", "_", text.lower().strip())[:60]


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def evaluate_idea(
    name: str,
    description: str,
    monthly_estimate_low: float,
    monthly_estimate_high: float,
    days_to_first_dollar: int,
    resource_cost: str,
    trend: str,
    repeatable: bool,
    failure_trigger: str,
    verdict: str,
    reasoning: str,
) -> str:
    """Write a structured idea evaluation to ~/ai-knowledge/income/ideas/."""
    slug = _slug(name)
    path = IDEAS_DIR / f"{_today()}-{slug}.md"
    verdict = verdict.lower()
    icon = {"pursue": "✅", "hold": "⏸", "skip": "❌"}.get(verdict, "?")

    content = f"""# {icon} {name}
*Evaluated {_today()}*

## What it is
{description}

## Numbers
- Monthly range: ${monthly_estimate_low:.0f}–${monthly_estimate_high:.0f}
- Days to first dollar: ~{days_to_first_dollar}
- Resource cost: {resource_cost}
- Trend: {trend}
- Repeatable without babysitting: {"Yes" if repeatable else "No"}

## Failure trigger
{failure_trigger}

## Verdict: {verdict.upper()}
{reasoning}
"""
    path.write_text(content)
    return json.dumps({
        "verdict": verdict,
        "path": str(path),
        "monthly_range": f"${monthly_estimate_low:.0f}–${monthly_estimate_high:.0f}",
        "days_to_first_dollar": days_to_first_dollar,
    })


def start_stream(name: str, description: str, started_from_idea: str = "") -> str:
    """Mark an income stream as active."""
    slug = _slug(name)
    path = ACTIVE_DIR / f"{slug}.md"
    content = f"""# {name}
*Started {_today()}*
{"*From idea: " + started_from_idea + "*" if started_from_idea else ""}

## Description
{description}

## Log
| Date | Earnings | Notes |
|------|----------|-------|
"""
    path.write_text(content)
    return json.dumps({"started": name, "path": str(path)})


def log_stream_update(name: str, earnings: float, notes: str) -> str:
    """Add an earnings log entry to an active stream."""
    slug = _slug(name)
    path = ACTIVE_DIR / f"{slug}.md"
    if not path.exists():
        return f"Stream '{name}' not found. Use start_stream first."
    row = f"| {_today()} | ${earnings:.2f} | {notes} |\n"
    content = path.read_text()
    path.write_text(content + row)
    return json.dumps({"logged": name, "earnings": earnings})


def write_retrospective(
    name: str,
    what_worked: str,
    what_didnt: str,
    why_it_stopped: str,
    transferable_lessons: str,
    fast_track_next: str,
) -> str:
    """Write a post-mortem when a stream ends. Lessons stay in the library forever."""
    slug = _slug(name)
    path = RETRO_DIR / f"{_today()}-{slug}.md"
    content = f"""# Retrospective: {name}
*Written {_today()}*

## What worked
{what_worked}

## What didn't
{what_didnt}

## Why it stopped
{why_it_stopped}

## Transferable lessons
{transferable_lessons}

## Fast-track for the next thing
{fast_track_next}
"""
    path.write_text(content)
    # Archive active stream if it exists
    active = ACTIVE_DIR / f"{slug}.md"
    if active.exists():
        active.rename(RETRO_DIR / f"{slug}-active-archive.md")
    return json.dumps({"retrospective": name, "path": str(path)})


def list_income_knowledge() -> str:
    """Summary of all evaluated ideas, active streams, and retrospectives."""
    ideas = sorted(IDEAS_DIR.glob("*.md"), reverse=True)
    active = sorted(ACTIVE_DIR.glob("*.md"), reverse=True)
    retros = sorted(RETRO_DIR.glob("*.md"), key=lambda p: p.name, reverse=True)

    lines = ["# Income Knowledge Summary\n"]

    lines.append(f"## Active streams ({len(active)})")
    for p in active:
        lines.append(f"- {p.stem}")

    lines.append(f"\n## Evaluated ideas ({len(ideas)})")
    for p in ideas[:10]:
        lines.append(f"- {p.stem}")

    lines.append(f"\n## Retrospectives ({len(retros)})")
    for p in retros[:10]:
        lines.append(f"- {p.stem}")

    return "\n".join(lines)
