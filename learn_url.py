"""Ingest any URL (HTML page or direct PDF link) into Amy's memory (RAG).

Handles:
  - Direct PDF URLs  → downloaded, chunked like learn_pdf.py
  - HTML pages       → scraped (main content extracted), chunked, embedded
  - GitHub markdown  → raw.githubusercontent.com fetched as plain text

Usage:
    python3 learn_url.py https://example.com/book
    python3 learn_url.py https://example.com/book.pdf --label "My Book"
    python3 learn_url.py --list
    python3 learn_url.py --forget "My Book"
"""

import argparse
import os
import re
import struct
import sys
import tempfile
import textwrap
import urllib.request

import numpy as np
import ollama
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "amy_memory.db")
EMBED_MODEL = "nomic-embed-text"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_db():
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


def _to_blob(vec):
    return struct.pack(f"<{len(vec)}f", *vec)


def _embed(text):
    r = ollama.embed(model=EMBED_MODEL, input=text)
    return np.array(r["embeddings"][0], dtype=np.float32)


# ── URL normalisation ─────────────────────────────────────────────────────────

def _github_to_raw(url):
    """Convert github.com blob URL → raw.githubusercontent.com"""
    # https://github.com/user/repo/blob/branch/path
    m = re.match(
        r"https://github\.com/([^/]+/[^/]+)/blob/([^/]+)/(.+)", url
    )
    if m:
        return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return url


def _is_pdf_url(url):
    return url.lower().split("?")[0].endswith(".pdf")


# ── fetching ──────────────────────────────────────────────────────────────────

def fetch_text_from_html(url):
    """Fetch URL and extract readable text from the main content."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        sys.exit("pip install beautifulsoup4")

    import requests
    s = requests.Session()
    s.headers.update(HEADERS)
    resp = s.get(url, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "noscript", "form"]):
        tag.decompose()

    # Try to find main content
    body = (soup.find("article") or soup.find("main") or
            soup.find(id="content") or soup.find(class_="content") or soup.body)
    if not body:
        return ""

    text = body.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def fetch_raw_text(url):
    """Fetch plain text / markdown URL."""
    import requests
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def fetch_pdf(url):
    """Download PDF to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        tmp.write(r.read())
    tmp.close()
    return tmp.name


# ── chunking (shared with learn_pdf.py) ──────────────────────────────────────

def split_passages(text, max_words=200):
    import re as _re
    paragraphs = [p.strip() for p in _re.split(r"\n{2,}", text) if p.strip()]
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

def ingest_url(url, label=None, max_words=200, dry_run=False):
    """Main entry point: fetch URL content and store in Amy's memory."""
    url = _github_to_raw(url)
    label = label or url

    print(f"\nIngesting URL: {url}")
    print(f"        Label: {label}")

    # ── fetch content ────────────────────────────────────────────────────────
    if _is_pdf_url(url):
        print("  Type: PDF — downloading…")
        pdf_path = fetch_pdf(url)
        try:
            import learn_pdf as lp
            return lp.ingest(pdf_path, label=label, max_words=max_words,
                             dry_run=dry_run)
        finally:
            try:
                os.unlink(pdf_path)
            except OSError:
                pass

    # Plain text / markdown (GitHub raw, .txt, .md)
    if any(url.endswith(ext) for ext in (".md", ".txt", ".rst")) or \
       "raw.githubusercontent.com" in url:
        print("  Type: markdown/text — fetching…")
        text = fetch_raw_text(url)
    else:
        print("  Type: HTML — scraping…")
        text = fetch_text_from_html(url)

    if not text.strip():
        print("  (no readable content found)")
        return 0

    passages = split_passages(text, max_words)
    print(f"  Chunks: {len(passages)}")

    if dry_run:
        print("\n-- DRY RUN --")
        for i, p in enumerate(passages[:5]):
            print(f"\n[{i}] {p[:120]}…")
        return 0

    db = _get_db()
    existing = db.execute(
        "SELECT COUNT(*) FROM mem WHERE source = ?", (label,)
    ).fetchone()[0]
    if existing:
        print(f"  ⚠  {existing} chunks from '{label}' already in memory.")
        ans = input("   Re-ingest anyway? [y/N] ").strip().lower()
        if ans != "y":
            print("  Skipped.")
            db.close()
            return 0
        db.execute("DELETE FROM mem WHERE source = ?", (label,))
        db.commit()

    for i, passage in enumerate(passages):
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
    print(f"\n  Done — {len(passages)} chunks stored. Amy knows it now.")
    return len(passages)


def list_sources():
    db = _get_db()
    rows = db.execute(
        "SELECT source, COUNT(*) as n, MIN(ts) as first "
        "FROM mem WHERE source IS NOT NULL AND role='knowledge' "
        "GROUP BY source ORDER BY first"
    ).fetchall()
    db.close()
    if not rows:
        print("No knowledge ingested yet.")
        return
    print(f"\n{'Source / Label':<50} {'Chunks':>6}  Ingested")
    print("-" * 75)
    for source, n, ts in rows:
        print(f"{source:<50} {n:>6}  {ts[:16]}")


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
        description="Ingest any URL (HTML or PDF) into Amy's RAG memory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          python3 learn_url.py https://example.com/tutorial
          python3 learn_url.py https://example.com/book.pdf --label "CLRS"
          python3 learn_url.py https://github.com/user/repo/blob/main/README.md
          python3 learn_url.py --list
          python3 learn_url.py --forget "CLRS"
        """),
    )
    p.add_argument("url", nargs="?", help="URL to ingest")
    p.add_argument("--label", default=None, help="Human-readable name")
    p.add_argument("--max-words", type=int, default=200)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--list", action="store_true")
    p.add_argument("--forget", metavar="LABEL")
    args = p.parse_args()

    if args.list:
        list_sources()
    elif args.forget:
        forget(args.forget)
    elif args.url:
        ingest_url(args.url, label=args.label, max_words=args.max_words,
                   dry_run=args.dry_run)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
