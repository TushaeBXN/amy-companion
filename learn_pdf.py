"""Ingest a PDF into Amy's memory so she can recall it during conversations.

Uses the same nomic-embed-text + SQLite stack Amy already runs on.
Chunks appear in search results automatically — no restart needed.

Usage:
    python3 learn_pdf.py document.pdf
    python3 learn_pdf.py document.pdf --label "iPhone manual"
    python3 learn_pdf.py document.pdf --max-words 150 --dry-run
    python3 learn_pdf.py --list          # show all ingested sources
    python3 learn_pdf.py --forget "iPhone manual"   # remove by label
"""

import argparse
import os
import re
import struct
import sys
import textwrap

import fitz          # PyMuPDF
import numpy as np
import ollama
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "amy_memory.db")
EMBED_MODEL = "nomic-embed-text"


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_db():
    db = sqlite3.connect(DB_PATH)
    # Core table (already exists from memory.py)
    db.execute(
        "CREATE TABLE IF NOT EXISTS mem ("
        "id INTEGER PRIMARY KEY, role TEXT, text TEXT, emb BLOB, "
        "ts TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    # Add source column for tracking which PDF each chunk came from
    cols = [r[1] for r in db.execute("PRAGMA table_info(mem)").fetchall()]
    if "source" not in cols:
        db.execute("ALTER TABLE mem ADD COLUMN source TEXT")
    db.commit()
    return db


def _to_blob(vec):
    return struct.pack(f"<{len(vec)}f", *vec)


def _embed(text):
    r = ollama.embed(model=EMBED_MODEL, input=text)
    return np.array(r["embeddings"][0], dtype=np.float32)


# ── PDF extraction + chunking ─────────────────────────────────────────────────

def _ocr_page(page):
    """Render a page to image and run Tesseract OCR on it."""
    try:
        from PIL import Image
        import pytesseract
        mat = fitz.Matrix(2, 2)  # 2x scale for better OCR accuracy
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        return pytesseract.image_to_string(img)
    except Exception as e:
        return ""


def extract_pages(pdf_path):
    doc = fitz.open(pdf_path)
    pages = []
    text_pages = 0
    for i, page in enumerate(doc):
        text = page.get_text("text").strip()
        if text:
            text_pages += 1
            pages.append((i, text))

    # If no text layer found, fall back to OCR
    if text_pages == 0:
        print("  [ocr] no text layer — running Tesseract OCR on each page...")
        for i, page in enumerate(doc):
            text = _ocr_page(page).strip()
            if text:
                pages.append((i, text))
            if (i + 1) % 10 == 0:
                print(f"  [ocr] {i+1}/{len(doc)} pages processed", end="\r")
        if pages:
            print(f"\n  [ocr] extracted text from {len(pages)} pages")

    doc.close()
    return pages


def clean_text(text):
    text = re.sub(r"-\n(\w)", r"\1", text)       # fix hyphenated line-breaks
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Drop lines that are just page numbers
    lines = [l for l in text.splitlines()
             if l.strip() and not re.fullmatch(r"\d{1,4}", l.strip())]
    return "\n".join(lines).strip()


def split_passages(text, max_words=200):
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    passages, current, wc = [], [], 0
    for para in paragraphs:
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
    return [p for p in passages if len(p.split()) >= 10]


# ── ingestion ─────────────────────────────────────────────────────────────────

def ingest(pdf_path, label=None, max_words=200, dry_run=False):
    label = label or os.path.basename(pdf_path)
    print(f"\nIngesting: {pdf_path}")
    print(f"   Label : {label}")

    pages = extract_pages(pdf_path)
    print(f"   Pages : {len(pages)} with text")

    passages = []
    for _, text in pages:
        passages.extend(split_passages(clean_text(text), max_words))
    print(f"  Chunks : {len(passages)}")

    if dry_run:
        print("\n-- DRY RUN — nothing written --")
        for i, p in enumerate(passages[:5]):
            print(f"\n[{i}] {p[:120]}…")
        return 0

    db = _get_db()

    # Check for existing entries from this label to avoid double-ingestion
    existing = db.execute(
        "SELECT COUNT(*) FROM mem WHERE source = ?", (label,)
    ).fetchone()[0]
    if existing:
        print(f"\n  ⚠  {existing} chunks from '{label}' already in memory.")
        ans = input("   Re-ingest anyway? [y/N] ").strip().lower()
        if ans != "y":
            print("  Skipped.")
            return 0
        db.execute("DELETE FROM mem WHERE source = ?", (label,))
        db.commit()
        print(f"  Removed {existing} old chunks — re-ingesting.")

    stored = 0
    for i, passage in enumerate(passages):
        # Prefix with label so the model knows the source
        tagged = f"[From: {label}]\n{passage}"
        emb = _embed(tagged)
        db.execute(
            "INSERT INTO mem (role, text, emb, source) VALUES (?,?,?,?)",
            ("knowledge", tagged, _to_blob(emb.tolist()), label),
        )
        if (i + 1) % 10 == 0 or i + 1 == len(passages):
            db.commit()
            pct = int((i + 1) / len(passages) * 100)
            print(f"  [{pct:3d}%] {i+1}/{len(passages)} chunks embedded", end="\r")

    db.commit()
    db.close()
    print(f"\n  Done — {stored or len(passages)} chunks stored. Amy knows it now.")
    return len(passages)


# ── list / forget ─────────────────────────────────────────────────────────────

def list_sources():
    db = _get_db()
    cols = [r[1] for r in db.execute("PRAGMA table_info(mem)").fetchall()]
    if "source" not in cols:
        print("No PDF knowledge ingested yet.")
        return
    rows = db.execute(
        "SELECT source, COUNT(*) as n, MIN(ts) as first "
        "FROM mem WHERE source IS NOT NULL AND role='knowledge' "
        "GROUP BY source ORDER BY first"
    ).fetchall()
    db.close()
    if not rows:
        print("No PDF knowledge ingested yet.")
        return
    print(f"\n{'Source / Label':<40} {'Chunks':>6}  Ingested")
    print("-" * 65)
    for source, n, ts in rows:
        print(f"{source:<40} {n:>6}  {(ts or '')[:16]}")


def forget(label):
    db = _get_db()
    n = db.execute(
        "SELECT COUNT(*) FROM mem WHERE source = ?", (label,)
    ).fetchone()[0]
    if not n:
        print(f"No chunks found for '{label}'.")
        db.close()
        return
    db.execute("DELETE FROM mem WHERE source = ?", (label,))
    db.commit()
    db.close()
    print(f"Removed {n} chunks for '{label}' from Amy's memory.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Ingest a PDF into Amy's memory (RAG).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          python3 learn_pdf.py book.pdf
          python3 learn_pdf.py book.pdf --label "Rich Dad Poor Dad"
          python3 learn_pdf.py --list
          python3 learn_pdf.py --forget "Rich Dad Poor Dad"
        """),
    )
    p.add_argument("pdf", nargs="?", help="Path to PDF file")
    p.add_argument("--label", default=None, help="Human-readable name (default: filename)")
    p.add_argument("--max-words", type=int, default=200, help="Words per chunk (default: 200)")
    p.add_argument("--dry-run", action="store_true", help="Show chunks without writing")
    p.add_argument("--list", action="store_true", help="List all ingested PDF sources")
    p.add_argument("--forget", metavar="LABEL", help="Remove all chunks for a source")
    args = p.parse_args()

    if args.list:
        list_sources()
    elif args.forget:
        forget(args.forget)
    elif args.pdf:
        if not os.path.exists(args.pdf):
            sys.exit(f"File not found: {args.pdf}")
        ingest(args.pdf, label=args.label, max_words=args.max_words, dry_run=args.dry_run)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
