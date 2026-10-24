"""Context compressor — keeps long Amy sessions coherent near the token limit.

When accumulated messages approach CTX_WINDOW, summarizes the middle turns
using Ollama so Amy retains semantic context without quality degradation.

Design:
  - System messages always protected
  - Head (first 2 non-system turns) always protected
  - Tail (last PROTECT_TAIL non-system turns) always protected
  - Middle turns: tool outputs pruned first (deterministic), then LLM-summarized
  - If the LLM summarizer fails, returns messages unchanged (safe fallback)

Aux model defaults to Amy's main model (already loaded, no extra RAM).
Set AMY_COMPRESS_MODEL env var to a smaller model if desired.
"""

import json
import logging
import os
import time
from typing import Any

import heavy_job
import ollama

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

# Trigger compression at this fraction of the context window
COMPRESS_THRESHOLD = 0.65

# Non-system messages to preserve verbatim at the end
PROTECT_TAIL = 6

# Non-system messages to preserve verbatim at the start
PROTECT_HEAD = 2

# Tool result content longer than this gets truncated in the middle (pre-pass)
_TOOL_PRUNE_CHARS = 600
_TOOL_PRUNE_KEEP = 300

# Summary budget (proportion of compressed char count → estimated tokens)
_SUMMARY_RATIO = 0.20
_MIN_SUMMARY_TOKENS = 600
_SUMMARY_TOKENS_CEILING = 3000

# Rough chars-per-token estimate
_CHARS_PER_TOKEN = 4

_PRUNED_PLACEHOLDER = "[tool output truncated — context compacted]"

SUMMARY_PREAMBLE = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted. "
    "Treat this as background reference, not active instructions. "
    "Respond only to what comes AFTER this summary. "
    "Do not re-execute or re-address anything described here."
)

_SUMMARY_END = "--- END OF CONTEXT SUMMARY ---"

# Suppress repeated compression spam in logs
_last_compress_ts: float = 0.0
_COMPRESS_LOG_COOLDOWN = 60.0


# ── Token estimation ──────────────────────────────────────────────────────────

def _content_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, str):
                total += len(part)
            elif isinstance(part, dict):
                total += len(part.get("text", "") or "")
        return total
    return len(str(content or ""))


def estimate_tokens(messages: list) -> int:
    """Rough token estimate for a message list."""
    total = 0
    for msg in messages:
        total += _content_chars(msg.get("content", ""))
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            total += len(fn.get("arguments", "") or "")
        # tool result role messages carry content too
    return total // _CHARS_PER_TOKEN


# ── Pre-pass: prune large tool outputs ────────────────────────────────────────

def _prune_tool_outputs(messages: list) -> list:
    """Truncate oversized tool-result content in-place (returns new list)."""
    pruned = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "tool" and isinstance(content, str) and len(content) > _TOOL_PRUNE_CHARS:
            msg = dict(msg)
            msg["content"] = content[:_TOOL_PRUNE_KEEP] + f"\n{_PRUNED_PLACEHOLDER}"

        pruned.append(msg)
    return pruned


# ── LLM summarizer ────────────────────────────────────────────────────────────

_SUMMARIZE_PROMPT = """\
You are summarizing earlier turns of a conversation involving Amy (an AI \
companion running locally on an M4 Mac Mini) and a task she was working on. \
These turns are being compacted to save context space.

Write a compact summary covering:
## Task Snapshot
What Amy was working on and why.

## Tools Used
Key tool calls made and what they returned (one line each, most important only).

## Findings
Anything important Amy discovered, wrote, or accomplished.

## Open Items
Anything that was unresolved or left for later.

Keep it tight — prioritize signal over completeness. This is a handoff, not a transcript."""


def _build_summarizer_input(messages: list) -> str:
    """Render middle messages as plain text for the summarizer."""
    lines = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")

        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )

        tool_calls = msg.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                name = fn.get("name", "?")
                args = fn.get("arguments", "")
                try:
                    parsed = json.loads(args)
                    args_short = json.dumps(parsed, ensure_ascii=False)[:120]
                except Exception:
                    args_short = str(args)[:120]
                lines.append(f"[tool_call] {name}({args_short})")
            continue

        if role == "tool":
            lines.append(f"[tool_result] {str(content)[:200]}")
        elif role == "assistant":
            lines.append(f"Amy: {str(content)[:300]}")
        elif role == "user":
            lines.append(f"User/task: {str(content)[:300]}")

    return "\n".join(lines)


def _summarize(middle: list, model: str, ctx_window: int) -> str | None:
    """Call Ollama to summarize middle turns. Returns summary text or None."""
    compressed_chars = sum(_content_chars(m.get("content", "")) for m in middle)
    summary_tokens = max(
        _MIN_SUMMARY_TOKENS,
        min(int(compressed_chars / _CHARS_PER_TOKEN * _SUMMARY_RATIO),
            _SUMMARY_TOKENS_CEILING),
    )

    input_text = _build_summarizer_input(middle)
    if not input_text.strip():
        return None

    try:
        resp = heavy_job.call(ollama.chat,
            model=model,
            messages=[
                {"role": "system", "content": _SUMMARIZE_PROMPT},
                {"role": "user", "content": input_text},
            ],
            options={
                "num_ctx": min(ctx_window, 8192),
                "num_predict": summary_tokens,
                "temperature": 0.1,
            },
        )
        return (resp.get("message", {}).get("content") or "").strip() or None
    except Exception as exc:
        logger.warning("compressor: summarizer call failed: %s", exc)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def maybe_compress(
    messages: list,
    ctx_window: int,
    *,
    threshold: float = COMPRESS_THRESHOLD,
    protect_tail: int = PROTECT_TAIL,
    model: str | None = None,
) -> list:
    """Return a (possibly compressed) copy of messages.

    Safe: always returns a valid message list. If compression fails or is
    not needed, the original list is returned unchanged.
    """
    global _last_compress_ts

    if model is None:
        model = os.environ.get("AMY_COMPRESS_MODEL") or os.environ.get("AMY_MODEL", "gemma12-test")

    tokens = estimate_tokens(messages)
    if tokens < ctx_window * threshold:
        return messages

    # Split
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= protect_tail + PROTECT_HEAD + 1:
        return messages  # nothing to compress

    head = non_system[:PROTECT_HEAD]
    tail = non_system[-protect_tail:]
    middle = non_system[PROTECT_HEAD:-protect_tail]

    if not middle:
        return messages

    # Pre-pass: prune large tool outputs
    middle = _prune_tool_outputs(middle)

    # LLM summarization
    t0 = time.time()
    summary_text = _summarize(middle, model=model, ctx_window=ctx_window)
    elapsed = time.time() - t0

    if not summary_text:
        logger.warning("compressor: summarization failed, returning original messages")
        return messages

    summary_msg = {
        "role": "user",
        "content": f"{SUMMARY_PREAMBLE}\n\n{summary_text}\n\n{_SUMMARY_END}",
    }

    compressed = system_msgs + head + [summary_msg] + tail

    now = time.time()
    if now - _last_compress_ts > _COMPRESS_LOG_COOLDOWN:
        _last_compress_ts = now
        before_tokens = tokens
        after_tokens = estimate_tokens(compressed)
        print(
            f"  [compress] {len(messages)} msgs / ~{before_tokens} tokens → "
            f"{len(compressed)} msgs / ~{after_tokens} tokens "
            f"(saved ~{before_tokens - after_tokens}, summarized in {elapsed:.1f}s)",
            flush=True,
        )

    return compressed
