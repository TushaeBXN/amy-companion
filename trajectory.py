"""Trajectory logger — captures good task runs as structured training data.

Each completed task run that passes quality filters is appended as one JSON
line to trajectories/YYYY-MM-DD.jsonl. These are Amy's own experiences,
formatted for future LoRA fine-tuning on instruction-following and tool use.

Quality filters (all must pass):
  - Task did not fail
  - At least 2 tools were used (trivial no-op tasks skipped)
  - Did not hit max_hops (incomplete runs aren't good training signal)

Format per line:
  {
    "task_id":        str,
    "timestamp":      ISO-8601,
    "duration_s":     float,
    "steps":          int,
    "tool_errors":    int,
    "tools_used":     [str, ...],
    "outcome":        "success" | "hit_limit" | "failed",
    "turns":          [{"role": str, "content": str?, "tool_calls": [...]}]
  }

System prompts are stripped before saving (privacy + size).
Tool results are truncated to 400 chars (they can be huge).
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
TRAJ_DIR = os.path.join(HERE, "trajectories")

_MAX_TOOL_RESULT_CHARS = 400
_MAX_CONTENT_CHARS = 2000


def _sanitize_content(content: Any) -> Any:
    """Strip or truncate content for storage."""
    if isinstance(content, str):
        return content[:_MAX_CONTENT_CHARS] if len(content) > _MAX_CONTENT_CHARS else content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                out.append({"type": "text", "text": text[:_MAX_CONTENT_CHARS]})
            # drop image parts — not useful for text fine-tuning
        return out or content
    return content


def _sanitize_turn(msg: dict) -> dict | None:
    """Return a cleaned turn dict, or None to drop it."""
    role = msg.get("role", "")

    # Always drop system messages — too large, contains private config
    if role == "system":
        return None

    turn: dict = {"role": role}

    content = msg.get("content")
    if content is not None:
        if role == "tool":
            # Truncate tool results more aggressively
            text = str(content)
            turn["content"] = text[:_MAX_TOOL_RESULT_CHARS] + ("…" if len(text) > _MAX_TOOL_RESULT_CHARS else "")
        else:
            turn["content"] = _sanitize_content(content)

    tool_calls = msg.get("tool_calls")
    if tool_calls:
        cleaned = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function", {})
            args = fn.get("arguments", "")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    pass
            cleaned.append({
                "name": fn.get("name", ""),
                "arguments": args,
            })
        if cleaned:
            turn["tool_calls"] = cleaned

    return turn


def save(
    task_id: str,
    messages: list,
    tools_used: list,
    final: str,
    started: float,
    steps: int,
    tool_errors: int,
    hit_limit: bool,
) -> None:
    """Append one trajectory to today's JSONL file if it passes quality filters."""

    # Quality gates
    failed = "(task failed" in (final or "")
    if failed:
        return
    if len(set(tools_used)) < 2:
        return  # too trivial
    if hit_limit:
        return  # incomplete run — not good signal

    turns = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        t = _sanitize_turn(msg)
        if t is not None:
            turns.append(t)

    record = {
        "id": str(uuid.uuid4()),
        "task_id": task_id,
        "timestamp": datetime.fromtimestamp(started, tz=timezone.utc).isoformat(),
        "duration_s": round(time.time() - started, 1),
        "steps": steps,
        "tool_errors": tool_errors,
        "tools_used": tools_used,
        "outcome": "success",
        "turns": turns,
    }

    try:
        os.makedirs(TRAJ_DIR, exist_ok=True)
        date_str = datetime.fromtimestamp(started).strftime("%Y-%m-%d")
        path = os.path.join(TRAJ_DIR, f"{date_str}.jsonl")
        with open(path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"  [trajectory] save failed ({task_id}): {exc}", flush=True)


# time is used in save() — import here to keep the module clean
import time
