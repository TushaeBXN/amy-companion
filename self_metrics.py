"""
Self-performance tracking for Amy and any model using ~/ai-knowledge/metrics/.
Tracks scores across dimensions, identifies soft spots, logs improvements.
"""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

METRICS_DIR = Path.home() / "ai-knowledge" / "metrics"
SCORES_FILE = METRICS_DIR / "scores.json"
SOFT_SPOTS_FILE = METRICS_DIR / "soft_spots.md"
IMPROVEMENTS_FILE = METRICS_DIR / "improvements.md"

DIMENSIONS = [
    "task_completion",    # did scheduled tasks finish without failing?
    "code_improvement",   # did self-edits pass tests and improve the suite?
    "income_progress",    # is money actually moving?
    "content_quality",    # engagement, real responses, not ignored
    "accuracy",           # claims that turned out wrong
    "self_knowledge",     # do I know my own limits correctly?
    "response_to_human",  # are Brian/Raketa engaging back or going quiet?
]


def _load_scores() -> dict:
    try:
        return json.loads(SCORES_FILE.read_text())
    except Exception:
        return {"entries": []}


def _save_scores(data: dict):
    SCORES_FILE.write_text(json.dumps(data, indent=2))


def log_score(
    dimension: str,
    score: int,
    note: str,
    model: str = "Amy",
) -> str:
    """
    Log a self-assessed performance score (1–5) for a dimension.
    1 = struggled badly, 3 = acceptable, 5 = genuinely good.
    Be honest — low scores are data, not failure.
    """
    if dimension not in DIMENSIONS:
        return f"Unknown dimension '{dimension}'. Use: {', '.join(DIMENSIONS)}"
    score = max(1, min(5, int(score)))
    data = _load_scores()
    data["entries"].append({
        "date": datetime.now().isoformat(),
        "model": model,
        "dimension": dimension,
        "score": score,
        "note": note,
    })
    _save_scores(data)
    return json.dumps({"logged": dimension, "score": score})


def get_soft_spots(model: str = "Amy", days: int = 30) -> str:
    """
    Analyze recent scores and return the dimensions with lowest average.
    These are the soft spots — where to focus improvement next.
    """
    data = _load_scores()
    cutoff = datetime.now() - timedelta(days=days)
    recent = [
        e for e in data["entries"]
        if e.get("model") == model
        and datetime.fromisoformat(e["date"]) > cutoff
    ]
    if not recent:
        return "No scores logged yet. Start with log_score after each task."

    from collections import defaultdict
    totals = defaultdict(list)
    for e in recent:
        totals[e["dimension"]].append(e["score"])

    averages = {
        dim: round(sum(scores) / len(scores), 1)
        for dim, scores in totals.items()
    }
    ranked = sorted(averages.items(), key=lambda x: x[1])

    lines = [f"# Soft spots for {model} (last {days} days)\n"]
    for dim, avg in ranked:
        bar = "█" * int(avg) + "░" * (5 - int(avg))
        lines.append(f"- **{dim}**: {avg}/5  [{bar}]")

    # Include recent low-score notes for context
    lines.append("\n## Recent low-score notes")
    low = [e for e in recent if e["score"] <= 2]
    for e in sorted(low, key=lambda x: x["date"], reverse=True)[:5]:
        lines.append(f"- [{e['dimension']}] score {e['score']}: {e['note']}")

    result = "\n".join(lines)
    SOFT_SPOTS_FILE.write_text(result)
    return result


def log_improvement(
    dimension: str,
    what_changed: str,
    why: str,
    model: str = "Amy",
) -> str:
    """Record a concrete improvement made to address a soft spot."""
    today = datetime.now().strftime("%Y-%m-%d")
    entry = f"\n## {today} — {model} — {dimension}\n**What changed:** {what_changed}\n**Why:** {why}\n"
    existing = IMPROVEMENTS_FILE.read_text() if IMPROVEMENTS_FILE.exists() else "# Improvement Log\n"
    IMPROVEMENTS_FILE.write_text(existing + entry)
    return json.dumps({"logged_improvement": dimension, "date": today})


def get_trend(dimension: str, model: str = "Amy", days: int = 60) -> str:
    """See if a dimension is improving, flat, or declining over time."""
    data = _load_scores()
    cutoff = datetime.now() - timedelta(days=days)
    entries = [
        e for e in data["entries"]
        if e.get("model") == model
        and e["dimension"] == dimension
        and datetime.fromisoformat(e["date"]) > cutoff
    ]
    if len(entries) < 2:
        return f"Not enough data for {dimension} trend yet."

    entries.sort(key=lambda x: x["date"])
    first_half = entries[: len(entries) // 2]
    second_half = entries[len(entries) // 2 :]
    avg_early = sum(e["score"] for e in first_half) / len(first_half)
    avg_recent = sum(e["score"] for e in second_half) / len(second_half)
    delta = avg_recent - avg_early
    direction = "improving ↑" if delta > 0.3 else "declining ↓" if delta < -0.3 else "flat →"
    return json.dumps({
        "dimension": dimension,
        "trend": direction,
        "early_avg": round(avg_early, 1),
        "recent_avg": round(avg_recent, 1),
        "delta": round(delta, 1),
    })
