#!/usr/bin/env python3
"""
transcript_to_dataset.py  —  turn Claude Code session transcripts into a fine-tuning dataset.

Output formats
  --format chat    JSONL  {"messages": [{"role":…,"content":…}, …]}   (OpenAI / Unsloth)
  --format alpaca  JSONL  {"instruction":…, "input":…, "output":…}

Usage
  python3 scripts/transcript_to_dataset.py \
      ~/.claude/projects/-Users-anthosintelligence-ava-companion/SESSION.jsonl \
      --format chat --out data/amy_finetune.jsonl

  python3 scripts/transcript_to_dataset.py \
      --project ~/.claude/projects/-Users-anthosintelligence-ava-companion \
      --format chat --out data/amy_finetune.jsonl
"""

import argparse, glob, json, re, sys
from pathlib import Path

# ── noise to strip from message text ─────────────────────────────────────────

def _strip_noise(text: str) -> str:
    """Remove system reminders, tool XML, pasted-content wrappers, interrupt notices."""
    # system-reminder blocks
    text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # function_calls / tool result XML
    text = re.sub(r"<function_calls>.*?</function_calls>", "[tool call]", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<function_results>.*?</function_results>", "[tool result]", text, flags=re.DOTALL | re.IGNORECASE)
    # pasted_content wrappers (keep inner text)
    text = re.sub(r'<pasted_content[^>]*>', "", text, flags=re.IGNORECASE)
    text = re.sub(r'</pasted_content[^>]*>', "", text, flags=re.IGNORECASE)
    # interrupt notices
    text = re.sub(r"\[Request interrupted by user.*?\]", "", text)
    # collapse excess whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_text(content) -> str:
    """Pull plain text out of a message content field (str or list of blocks)."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            t = block.get("text", "")
            if t:
                parts.append(t)
    return "\n".join(parts)


# ── transcript reader ─────────────────────────────────────────────────────────

def load_turns(path: Path):
    """
    Read a .jsonl transcript and return a list of {"role": "user"|"assistant", "text": str}.
    Skips empty turns, pure-noise turns, and interrupted placeholders.
    """
    turns = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            role = rec.get("type")
            if role not in ("user", "assistant"):
                continue

            msg = rec.get("message", {})
            raw = _extract_text(msg.get("content", ""))
            text = _strip_noise(raw)

            if not text or len(text) < 10:
                continue
            # skip turns that are only system reminders / boilerplate
            if text.startswith("[Request interrupted") or text == "[tool call]":
                continue

            turns.append({"role": role, "text": text})

    return turns


# ── conversation segmenter ────────────────────────────────────────────────────

def segment_conversations(turns, min_turns=2):
    """
    Split a flat list of turns into individual conversations.
    A new conversation starts whenever the role sequence resets (user after assistant)
    after a long assistant response gap, or we just keep them all as one big convo.

    For a fine-tuning dataset we want each (user_msg → assistant_reply) pair as a
    separate example, so we emit sliding pairs.
    """
    pairs = []
    i = 0
    while i < len(turns) - 1:
        u = turns[i]
        a = turns[i + 1]
        if u["role"] == "user" and a["role"] == "assistant":
            pairs.append((u["text"], a["text"]))
            i += 2
        else:
            i += 1
    return pairs


# ── formatters ────────────────────────────────────────────────────────────────

def to_chat(user_text: str, assistant_text: str) -> dict:
    return {
        "messages": [
            {"role": "user",      "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ]
    }


def to_alpaca(user_text: str, assistant_text: str) -> dict:
    return {
        "instruction": user_text,
        "input": "",
        "output": assistant_text,
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Convert Claude Code transcripts to fine-tuning JSONL")
    ap.add_argument("files", nargs="*", help="Transcript .jsonl file(s)")
    ap.add_argument("--project", help="Load all .jsonl files from a project directory")
    ap.add_argument("--format", choices=["chat", "alpaca"], default="chat")
    ap.add_argument("--out", default="data/amy_finetune.jsonl", help="Output JSONL path")
    ap.add_argument("--min-chars", type=int, default=30,
                    help="Skip turns shorter than this many chars (default 30)")
    args = ap.parse_args()

    # collect input files
    paths = []
    for f in args.files:
        paths.extend(glob.glob(str(Path(f).expanduser())))
    if args.project:
        paths.extend(glob.glob(str(Path(args.project).expanduser() / "*.jsonl")))
    paths = sorted(set(paths))

    if not paths:
        sys.exit("No transcript files found. Pass .jsonl files or --project <dir>")

    print(f"Reading {len(paths)} transcript(s)…")

    all_pairs = []
    for p in paths:
        turns = load_turns(Path(p))
        pairs = segment_conversations(turns)
        # filter by min-chars
        pairs = [
            (u, a) for u, a in pairs
            if len(u) >= args.min_chars and len(a) >= args.min_chars
        ]
        print(f"  {Path(p).name}: {len(turns)} turns → {len(pairs)} examples")
        all_pairs.extend(pairs)

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)

    formatter = to_chat if args.format == "chat" else to_alpaca
    with open(out, "w", encoding="utf-8") as f:
        for u, a in all_pairs:
            f.write(json.dumps(formatter(u, a), ensure_ascii=False) + "\n")

    print(f"\nWrote {len(all_pairs)} examples → {out}")
    print("Preview of first example:")
    if all_pairs:
        print(json.dumps(formatter(all_pairs[0][0][:120], all_pairs[0][1][:120]), indent=2))


if __name__ == "__main__":
    main()
