"""knowledge_watcher.py — auto-ingest ~/ai-knowledge/ into Amy's RAG.

Scans ~/ai-knowledge/ for new or modified .md and .txt files.
When a file is new or changed, old chunks for that source are replaced
with freshly-embedded ones — Amy's recall stays current automatically.

Called by worker.py every few ticks; also runnable standalone:
    python3 knowledge_watcher.py           # scan and ingest changes
    python3 knowledge_watcher.py --list    # show all indexed sources
    python3 knowledge_watcher.py --reset   # clear manifest, re-ingest everything
"""

import argparse
import json
import os
import struct
import textwrap

import numpy as np
import ollama
import sqlite3

HERE         = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_DIR = os.path.expanduser("~/ai-knowledge")
DB_PATH      = os.path.join(HERE, "amy_memory.db")
MANIFEST     = os.path.join(KNOWLEDGE_DIR, ".ingest_manifest.json")
EMBED_MODEL  = "nomic-embed-text"
SOURCE_PREFIX = "knowledge:"
MAX_WORDS     = 200
MAX_FILE_BYTES = 300_000  # skip files over 300 KB

# File types worth ingesting
INGEST_EXTS = {".md", ".txt"}

# Relative paths to skip (relative to KNOWLEDGE_DIR)
SKIP_NAMES = {"index.md", "README.md", "readme.md"}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db():
    db = sqlite3.connect(DB_PATH)
    db.execute(
        "CREATE TABLE IF NOT EXISTS mem ("
        "id INTEGER PRIMARY KEY, role TEXT, text TEXT, emb BLOB, "
        "ts TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    cols = [r[1] for r in db.execute("PRAGMA table_info(mem)").fetchall()]
    if "source" not in cols:
        db.execute("ALTER TABLE mem ADD COLUMN source TEXT")
    db.commit()
    return db


def _blob(vec):
    return struct.pack(f"<{len(vec)}f", *vec)


def _embed(text):
    r = ollama.embed(model=EMBED_MODEL, input=text)
    return np.array(r["embeddings"][0], dtype=np.float32)


# ── manifest (tracks {rel_path: mtime}) ──────────────────────────────────────

def _load_manifest():
    try:
        with open(MANIFEST) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_manifest(m):
    with open(MANIFEST, "w") as f:
        json.dump(m, f, indent=2)


# ── chunking ─────────────────────────────────────────────────────────────────

def _chunks(text, max_words=MAX_WORDS):
    import re
    paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    passages, current, wc = [], [], 0
    for para in paras:
        pw = len(para.split())
        if wc + pw > max_words and current:
            passages.append(" ".join(current))
            current, wc = [], 0
        if pw > max_words:
            words = para.split()
            for i in range(0, len(words), max_words):
                passages.append(" ".join(words[i:i + max_words]))
        else:
            current.append(para)
            wc += pw
    if current:
        passages.append(" ".join(current))
    return [p for p in passages if len(p.split()) >= 8]


# ── ingestion ─────────────────────────────────────────────────────────────────

def _ingest_file(rel_path, abs_path):
    """Read, chunk, embed, and store one file. Returns number of chunks stored."""
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return 0

    passages = _chunks(text)
    if not passages:
        return 0

    source = SOURCE_PREFIX + rel_path
    db = _db()
    db.execute("DELETE FROM mem WHERE source = ?", (source,))
    db.commit()

    for i, passage in enumerate(passages):
        tagged = f"[From: {rel_path}]\n{passage}"
        emb = _embed(tagged)
        db.execute(
            "INSERT INTO mem (role, text, emb, source) VALUES (?,?,?,?)",
            ("knowledge", tagged, _blob(emb.tolist()), source),
        )
        if (i + 1) % 10 == 0 or i + 1 == len(passages):
            db.commit()

    db.commit()
    db.close()
    return len(passages)


# ── scanner ───────────────────────────────────────────────────────────────────

def scan_and_ingest(verbose=False):
    """Scan ~/ai-knowledge/ and ingest any new or changed files.

    Returns (new_files, updated_files, total_chunks).
    """
    if not os.path.isdir(KNOWLEDGE_DIR):
        return 0, 0, 0

    manifest = _load_manifest()
    new_files = updated_files = total_chunks = 0

    for root, dirs, files in os.walk(KNOWLEDGE_DIR):
        # skip hidden dirs like .git
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            if not any(fname.endswith(ext) for ext in INGEST_EXTS):
                continue
            if fname in SKIP_NAMES:
                continue

            abs_path = os.path.join(root, fname)
            rel_path = os.path.relpath(abs_path, KNOWLEDGE_DIR)

            # skip oversized files
            try:
                size = os.path.getsize(abs_path)
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                continue

            try:
                mtime = os.path.getmtime(abs_path)
            except OSError:
                continue

            recorded = manifest.get(rel_path)
            if recorded is not None and abs(recorded - mtime) < 1.0:
                continue  # unchanged

            is_new = recorded is None
            n = _ingest_file(rel_path, abs_path)
            if n:
                manifest[rel_path] = mtime
                total_chunks += n
                if is_new:
                    new_files += 1
                    if verbose:
                        print(f"  [watcher] new    {rel_path!r:60s} ({n} chunks)")
                else:
                    updated_files += 1
                    if verbose:
                        print(f"  [watcher] update {rel_path!r:60s} ({n} chunks)")

    _save_manifest(manifest)
    return new_files, updated_files, total_chunks


# ── list / reset helpers ──────────────────────────────────────────────────────

def list_indexed():
    db = _db()
    rows = db.execute(
        "SELECT source, COUNT(*) as n, MIN(ts) as first "
        "FROM mem WHERE source LIKE ? AND role='knowledge' "
        "GROUP BY source ORDER BY first",
        (SOURCE_PREFIX + "%",),
    ).fetchall()
    db.close()
    if not rows:
        print("No ai-knowledge files indexed yet.")
        return
    print(f"\n{'Source':<65} {'Chunks':>6}  Indexed")
    print("-" * 85)
    for source, n, ts in rows:
        print(f"{source:<65} {n:>6}  {ts[:16]}")


def reset_manifest():
    if os.path.exists(MANIFEST):
        os.unlink(MANIFEST)
    print("Manifest cleared — next scan will re-ingest everything.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Sync ~/ai-knowledge/ into Amy's RAG.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          python3 knowledge_watcher.py           # ingest new/changed files
          python3 knowledge_watcher.py --list    # show indexed sources
          python3 knowledge_watcher.py --reset   # clear manifest, re-ingest all
        """),
    )
    p.add_argument("--list",  action="store_true", help="List indexed sources")
    p.add_argument("--reset", action="store_true", help="Clear manifest and re-ingest")
    args = p.parse_args()

    if args.list:
        list_indexed()
    elif args.reset:
        reset_manifest()
        new, upd, chunks = scan_and_ingest(verbose=True)
        print(f"\nDone — {new} files ingested, {chunks} total chunks.")
    else:
        new, upd, chunks = scan_and_ingest(verbose=True)
        if new + upd == 0:
            print("  [watcher] ai-knowledge up to date, nothing to ingest.")
        else:
            print(f"  [watcher] done — {new} new, {upd} updated, {chunks} total chunks.")


if __name__ == "__main__":
    main()
