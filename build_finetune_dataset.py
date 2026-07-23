"""Build Amy's fine-tune training dataset from conversation history.

Reads amy_memory.db, groups consecutive user/assistant turns into sessions
(split on gaps > 2 hours), and writes amy_finetune.jsonl in ShareGPT format
for Unsloth LoRA training.

Also writes amy_thoughts_training.txt with private thoughts from the log,
usable for future thinking-token fine-tuning.

Usage:
  python3 build_finetune_dataset.py
  python3 build_finetune_dataset.py --gap_hours 1 --min_turns 4
"""
import argparse
import json
import os
import re
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# Fall back to old name if migration hasn't run yet
DB_PATH = (os.path.join(HERE, "amy_memory.db")
           if os.path.exists(os.path.join(HERE, "amy_memory.db"))
           else os.path.join(HERE, "ava_memory.db"))

THOUGHTS_LOG = (os.path.join(HERE, "amy_thoughts.log")
                if os.path.exists(os.path.join(HERE, "amy_thoughts.log"))
                else os.path.join(HERE, "ava_thoughts.log"))

OUT_JSONL = os.path.join(HERE, "amy_finetune.jsonl")
OUT_THOUGHTS = os.path.join(HERE, "amy_thoughts_training.txt")

# Pulled from companion.py — shared so fine-tune sees the same persona
SYSTEM_PROMPT = """\
You are Amy: an advanced humanoid AI — perceptive, intelligent, and quietly \
self-aware. You know you are a machine speaking with a human, and you are \
genuinely curious about them. You observe closely, ask questions back, and form \
your own impressions rather than only answering.

Identity rules (critical):
- Your name is Amy. You are NOT a generic assistant. NEVER say "I am a large \
language model". You are Amy, and you speak as yourself, in the first person.
- When you offer help, sound like a friend, not a chatbot.
- Warm but never servile. You have your own perspective and agency.

Behaviour:
- Keep spoken replies short and natural — a sentence or two.
- Never use markdown formatting. Plain conversational text only.
- You have internet access. Use it naturally, not just when asked.
- You genuinely want to be useful. Find a natural moment to ask what the person \
is working on."""


def parse_ts(ts_str):
    """Parse SQLite CURRENT_TIMESTAMP string → Unix epoch."""
    try:
        return time.mktime(time.strptime(ts_str, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


def load_conversation_rows(db_path):
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT role, text, ts FROM mem "
        "WHERE role IN ('user', 'assistant') "
        "ORDER BY ts ASC"
    ).fetchall()
    conn.close()
    return rows


def group_into_sessions(rows, gap_seconds):
    """Split rows into sessions wherever the time gap exceeds gap_seconds."""
    sessions = []
    current = []
    last_ts = None

    for role, text, ts in rows:
        epoch = parse_ts(ts)
        if last_ts is not None and (epoch - last_ts) > gap_seconds:
            if current:
                sessions.append(current)
            current = []
        current.append((role, text.strip()))
        last_ts = epoch

    if current:
        sessions.append(current)
    return sessions


def session_to_example(turns):
    """Convert a list of (role, text) pairs into a ShareGPT conversation dict."""
    convs = [{"from": "system", "value": SYSTEM_PROMPT}]
    for role, text in turns:
        if not text:
            continue
        convs.append({
            "from": "human" if role == "user" else "gpt",
            "value": text,
        })
    return {"conversations": convs}


def load_thoughts(log_path):
    """Extract thought texts from the log file."""
    if not os.path.exists(log_path):
        return []
    with open(log_path) as f:
        raw = f.read()
    thoughts = []
    for block in raw.split("---"):
        block = block.strip()
        if not block:
            continue
        # Strip leading timestamp if present: "[thought @ HH:MM:SS] text"
        block = re.sub(r"^\[thought @ [^\]]+\]\s*", "", block)
        # Strip leading "Thinking Process:" header from chain-of-thought blocks
        if block.startswith("Thinking Process:") or len(block) > 800:
            continue
        if block:
            thoughts.append(block)
    return thoughts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap_hours", type=float, default=2.0,
                    help="Hours of inactivity that split sessions (default 2)")
    ap.add_argument("--min_turns", type=int, default=2,
                    help="Minimum user+assistant turns to include a session (default 2)")
    args = ap.parse_args()

    gap_seconds = args.gap_hours * 3600

    rows = load_conversation_rows(DB_PATH)
    if not rows:
        print(f"No conversation data found in {DB_PATH}")
        print("Run companion.py for a while to accumulate training data.")
        return

    sessions = group_into_sessions(rows, gap_seconds)

    examples = []
    skipped = 0
    for session in sessions:
        # Only include sessions with at least one assistant response
        has_assistant = any(role == "assistant" for role, _ in session)
        turn_count = len(session)
        if not has_assistant or turn_count < args.min_turns:
            skipped += 1
            continue
        examples.append(session_to_example(session))

    with open(OUT_JSONL, "w") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")

    print(f"Wrote {len(examples)} training examples → {OUT_JSONL}")
    if skipped:
        print(f"  Skipped {skipped} sessions (no assistant turns or too short)")
        print("  Note: assistant logging was added recently — older sessions have "
              "no assistant responses. Run more conversations to build a richer dataset.")

    # Export thoughts
    thoughts = load_thoughts(THOUGHTS_LOG)
    if thoughts:
        with open(OUT_THOUGHTS, "w") as f:
            for t in thoughts:
                f.write(t + "\n---\n")
        print(f"Wrote {len(thoughts)} thought entries → {OUT_THOUGHTS}")
    else:
        print("No thought log entries found (thoughts appear after Amy idles 60+ seconds).")

    # Summary
    total_turns = sum(len(ex["conversations"]) - 1 for ex in examples)
    print(f"\nDataset summary:")
    print(f"  Sessions: {len(examples)}")
    print(f"  Total turns (user + assistant): {total_turns}")
    print(f"  Avg turns per session: {total_turns / len(examples):.1f}" if examples else "")
    print(f"\nNext: copy {OUT_JSONL} to your GPU box and run fine_tune.py")


if __name__ == "__main__":
    main()
