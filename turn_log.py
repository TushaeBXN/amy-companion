"""Structured per-turn log at logs/turns.jsonl.

Each newline-delimited JSON record:
  ts              ISO-8601 local timestamp with offset
  condition       AMY_CONDITION value (A/B/C/D)
  model           model name string
  prompt_hash     first 16 hex chars of sha256(system_prompt)
  mood            mood category string
  energy_pct      int 0-100
  tool_calls      list of {name, args, result_snippet}
  user_msg        first 500 chars of the user message
  assistant_reply first 500 chars of the final reply
"""
import hashlib
import json
import os
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(_HERE, "logs", "turns.jsonl")


def log_turn(
    *,
    condition: str,
    model: str,
    system_prompt: str,
    mood: str,
    energy_pct: int,
    tool_calls: list,
    user_msg: str,
    assistant_reply: str,
) -> None:
    record = {
        "ts": datetime.now().astimezone().isoformat(),
        "condition": condition,
        "model": model,
        "prompt_hash": hashlib.sha256(system_prompt.encode()).hexdigest()[:16],
        "mood": mood,
        "energy_pct": energy_pct,
        "tool_calls": tool_calls,
        "user_msg": (user_msg or "")[:500],
        "assistant_reply": (assistant_reply or "")[:500],
    }
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[turn_log] write failed: {e}")
