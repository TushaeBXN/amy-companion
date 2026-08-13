"""Build Amy's unified programming-books knowledge base.

Parses two curated GitHub book lists into one SQLite table (books.db) with
per-row semantic embeddings (title + authors + subject) so Amy can do
natural-language search without ever storing full book text.

Sources
-------
  ebookfoundation  — free-programming-books (subjects list, ~830 entries)
  developery       — Developer-Y free-technical-ebooks (~130 high-quality titles)

Usage
-----
    python3 learn_books.py build               # both sources
    python3 learn_books.py build --source developery
    python3 learn_books.py build --dry-run
    python3 learn_books.py stats               # counts by source + subject
    python3 learn_books.py search "neural networks from scratch"
    python3 learn_books.py search "convex optimization" --subject "Optimization"
"""

import argparse
import os
import re
import sqlite3
import struct
import sys
import textwrap
import urllib.request

import numpy as np
import ollama

HERE    = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "books.db")

EMBED_MODEL = "nomic-embed-text"

SOURCES = {
    "ebookfoundation": {
        "label": "EbookFoundation/free-programming-books (subjects)",
        "url": (
            "https://raw.githubusercontent.com/EbookFoundation/"
            "free-programming-books/main/books/free-programming-books-subjects.md"
        ),
        "parser": "ebookfoundation",
    },
    "ebookfoundation-langs": {
        "label": "EbookFoundation/free-programming-books (by language)",
        "url": (
            "https://raw.githubusercontent.com/EbookFoundation/"
            "free-programming-books/main/books/free-programming-books-langs.md"
        ),
        "parser": "ebookfoundation",   # same markdown format
    },
    "developery": {
        "label": "Developer-Y/free-technical-ebooks",
        "url": (
            "https://raw.githubusercontent.com/Developer-Y/"
            "free-technical-ebooks/master/README.md"
        ),
        "parser": "developery",
    },
}


# ── DB schema ─────────────────────────────────────────────────────────────────

def get_db(path=DB_PATH):
    db = sqlite3.connect(path)
    db.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id      INTEGER PRIMARY KEY,
            title   TEXT    NOT NULL,
            authors TEXT,
            subject TEXT    NOT NULL,
            url     TEXT,
            format  TEXT,
            license TEXT,
            source  TEXT,
            raw     TEXT,
            emb     BLOB
        )
    """)
    # Migrations: add columns that may be missing from an older build
    cols = {r[1] for r in db.execute("PRAGMA table_info(books)").fetchall()}
    for col, defn in [("source", "TEXT"), ("license", "TEXT")]:
        if col not in cols:
            db.execute(f"ALTER TABLE books ADD COLUMN {col} {defn}")
    db.execute("CREATE INDEX IF NOT EXISTS idx_subject ON books(subject)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_source  ON books(source)")
    db.commit()
    return db


# ── embedding ─────────────────────────────────────────────────────────────────

def _to_blob(vec):
    return struct.pack(f"<{len(vec)}f", *vec)

def _from_blob(blob):
    n = len(blob) // 4
    return np.array(struct.unpack(f"<{n}f", blob), dtype=np.float32)

def embed(text):
    r = ollama.embed(model=EMBED_MODEL, input=text)
    return np.array(r["embeddings"][0], dtype=np.float32)


# ── fetch raw markdown ────────────────────────────────────────────────────────

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8")


# ── parsers ───────────────────────────────────────────────────────────────────

_FORMAT_RE  = re.compile(r"\b(PDF|ePub|HTML|Kindle|MOBI|AZW3|Daisy)\b", re.I)
_LICENSE_RE = re.compile(r"\*\((CC[^)]+|GNU[^)]+|MIT[^)]+|public domain[^)]*)\)\*", re.I)
_LINK_RE    = re.compile(r"\[(.+?)\]\((.+?)\)")


def _split_title_authors(raw):
    """Split 'Title by Author' or 'Title - Author' into (title, authors)."""
    # Try ' by ' first (Developer-Y style)
    m = re.match(r"^(.+?)\s+by\s+(.+)$", raw, re.I)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    # Then ' - ' (EbookFoundation style), only if authors look short
    parts = raw.rsplit(" - ", 1)
    if len(parts) == 2 and len(parts[1].split()) <= 8:
        return parts[0].strip(), parts[1].strip()
    return raw.strip(), ""


def parse_ebookfoundation(md):
    entries, subject = [], "Misc"
    for line in md.splitlines():
        h = re.match(r"^#{2,4}\s+(.+)", line)
        if h:
            subject = h.group(1).strip().replace("&amp;", "&")
            continue
        m = re.match(r"^\s*\*\s+\[(.+?)\]\((.+?)\)\s*(.*)?$", line)
        if not m:
            continue
        raw_title, url, annotation = m.group(1), m.group(2), m.group(3) or ""
        if url.startswith("#") or not url.startswith("http"):
            continue
        title, authors = _split_title_authors(raw_title)
        fmt_m = _FORMAT_RE.search(annotation) or _FORMAT_RE.search(raw_title)
        fmt   = fmt_m.group(0).upper() if fmt_m else "HTML"
        lic_m = _LICENSE_RE.search(annotation)
        lic   = lic_m.group(1).strip() if lic_m else ""
        entries.append({
            "title": title, "authors": authors, "subject": subject,
            "url": url, "format": fmt, "license": lic,
            "source": "ebookfoundation", "raw": line.strip(),
        })
    return entries


def parse_developery(md):
    """Parse Developer-Y README — `### Category` headings, `- [Title](url)` entries."""
    entries, subject = [], "General"
    for line in md.splitlines():
        # Category headings (### or lines followed by ----)
        h = re.match(r"^#{2,4}\s+(.+)", line)
        if h:
            subject = h.group(1).strip()
            continue

        # List item:  - [Title - Author](url) or - [Title](url)  or bare -  text
        # Some entries have multiple links on one line, e.g.  (url) ([PDF](url2))
        if not line.strip().startswith("-"):
            continue

        # Find all markdown links on this line
        links = _LINK_RE.findall(line)
        if not links:
            # Bare text entry with no link — skip (no URL to give Amy)
            continue

        # Primary link is the first one
        raw_title, primary_url = links[0]
        if not primary_url.startswith("http"):
            continue

        # Look for a secondary PDF link, e.g.  ([PDF](url))
        pdf_url = None
        for _, u in links[1:]:
            if u.lower().endswith(".pdf") or "PDF" in line[line.find(u)-5:line.find(u)]:
                pdf_url = u
                break

        url = pdf_url or primary_url
        fmt = "PDF" if (url.lower().endswith(".pdf") or
                        any(u.lower().endswith(".pdf") for _, u in links)) else "HTML"

        title, authors = _split_title_authors(raw_title)

        entries.append({
            "title": title, "authors": authors, "subject": subject,
            "url": url, "format": fmt, "license": "",
            "source": "developery", "raw": line.strip(),
        })
    return entries


# ── build ─────────────────────────────────────────────────────────────────────

def build(sources=None, dry_run=False):
    if sources is None:
        sources = list(SOURCES.keys())

    all_entries = []
    for key in sources:
        src = SOURCES[key]
        print(f"Fetching {src['label']}…")
        md = fetch(src["url"])
        parser = src.get("parser", key)
        if parser == "ebookfoundation":
            entries = parse_ebookfoundation(md)
            # Tag with the correct source key (not the parser name)
            for e in entries:
                e["source"] = key
        else:
            entries = parse_developery(md)
        print(f"  Parsed {len(entries)} entries")
        all_entries.extend(entries)

    print(f"\nTotal before dedup: {len(all_entries)} entries")

    # URL-level deduplication across sources — keep the first occurrence
    seen_urls = set()
    deduped = []
    for e in all_entries:
        url = (e.get("url") or "").strip().rstrip("/")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        deduped.append(e)

    skipped = len(all_entries) - len(deduped)
    if skipped:
        print(f"  Skipped {skipped} duplicate URLs")
    all_entries = deduped
    print(f"  Unique entries: {len(all_entries)}")

    if dry_run:
        for e in all_entries[:12]:
            print(f"  [{e['source']}] [{e['subject']}] {e['title']} — {e['authors']} ({e['format']})")
            print(f"    {e['url']}")
        print(f"\n… and {max(0, len(all_entries)-12)} more. (dry run — nothing written)")
        return

    db = get_db()

    # Remove only the sources we're about to rebuild
    for key in sources:
        existing = db.execute(
            "SELECT COUNT(*) FROM books WHERE source=?", (key,)
        ).fetchone()[0]
        if existing:
            db.execute("DELETE FROM books WHERE source=?", (key,))
            db.commit()
            print(f"  Cleared {existing} old '{key}' entries")

    # Also collect URLs already in DB from OTHER sources so we don't insert
    # entries whose URL is already indexed under a different source key
    other_sources = [s for s in SOURCES if s not in sources]
    existing_urls = set()
    if other_sources:
        placeholders = ",".join("?" * len(other_sources))
        rows = db.execute(
            f"SELECT url FROM books WHERE source IN ({placeholders})", other_sources
        ).fetchall()
        existing_urls = {r[0].rstrip("/") for r in rows if r[0]}

    ok = skipped_dup = 0
    for i, e in enumerate(all_entries):
        url = (e.get("url") or "").strip().rstrip("/")
        if url and url in existing_urls:
            skipped_dup += 1
            continue

        embed_text = f"{e['title']} {e['authors']} {e['subject']}".strip()
        vec = embed(embed_text)
        db.execute(
            "INSERT INTO books (title,authors,subject,url,format,license,source,raw,emb) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (e["title"], e["authors"], e["subject"], e["url"],
             e["format"], e["license"], e["source"], e["raw"],
             _to_blob(vec.tolist()))
        )
        ok += 1
        if ok % 20 == 0 or i + 1 == len(all_entries):
            db.commit()
            pct = int((i + 1) / len(all_entries) * 100)
            print(f"  [{pct:3d}%] {i+1}/{len(all_entries)} processed ({ok} new, {skipped_dup} cross-source dups skipped)",
                  end="\r", flush=True)

    db.commit()
    db.close()
    print(f"\n  Done — {ok} new books indexed, {skipped_dup} cross-source duplicates skipped")


# ── semantic search ───────────────────────────────────────────────────────────

def search_books(query, subject_filter=None, source_filter=None,
                 k=8, min_score=0.45, db_path=DB_PATH):
    if not os.path.exists(db_path):
        return []

    db = get_db(db_path)
    sql    = "SELECT id,title,authors,subject,url,format,license,source,emb FROM books WHERE 1=1"
    params = []
    if subject_filter:
        sql += " AND subject LIKE ?"
        params.append(f"%{subject_filter}%")
    if source_filter:
        sql += " AND source=?"
        params.append(source_filter)
    rows = db.execute(sql, params).fetchall()
    db.close()

    if not rows:
        return []

    q = embed(query)
    q /= (np.linalg.norm(q) + 1e-9)

    scored = []
    for row in rows:
        id_, title, authors, subject, url, fmt, lic, source, emb_blob = row
        v = _from_blob(emb_blob)
        v /= (np.linalg.norm(v) + 1e-9)
        score = float(np.dot(q, v))
        scored.append((score, {
            "title": title, "authors": authors, "subject": subject,
            "url": url, "format": fmt, "license": lic, "source": source,
            "score": round(score, 3),
        }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for s, r in scored[:k] if s >= min_score]


# ── stats ─────────────────────────────────────────────────────────────────────

def stats():
    if not os.path.exists(DB_PATH):
        print("No books.db yet — run: python3 learn_books.py build")
        return
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    by_source = db.execute(
        "SELECT source, COUNT(*) FROM books GROUP BY source"
    ).fetchall()
    by_subject = db.execute(
        "SELECT subject, source, COUNT(*) as n FROM books "
        "GROUP BY subject, source ORDER BY subject, source"
    ).fetchall()
    db.close()

    print(f"\nTotal: {total} books")
    for src, n in by_source:
        print(f"  {(src or 'unknown'):<20} {n}")

    print(f"\n{'Subject':<45} {'Source':<16} {'Count':>5}")
    print("-" * 68)
    for subject, source, n in by_subject:
        print(f"{(subject or ''):<45} {(source or 'unknown'):<16} {n:>5}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Build Amy's unified programming books index (two sources).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Workflow:
          python3 learn_books.py build               # parse + embed both sources
          python3 learn_books.py stats               # inspect counts
          python3 learn_books.py search "deep learning intro"
          python3 learn_books.py search "OS concepts" --source developery
        """),
    )
    sub = p.add_subparsers(dest="cmd")

    b = sub.add_parser("build", help="Parse + embed catalog(s) into books.db")
    b.add_argument("--source", choices=list(SOURCES.keys()), default=None,
                   help="Build only one source (default: both)")
    b.add_argument("--dry-run", action="store_true")

    sub.add_parser("stats", help="Show entry counts by subject + source")

    s = sub.add_parser("search", help="Semantic search")
    s.add_argument("query")
    s.add_argument("--subject", default=None)
    s.add_argument("--source", choices=list(SOURCES.keys()), default=None)
    s.add_argument("--k", type=int, default=8)

    args = p.parse_args()

    if args.cmd == "build":
        srcs = [args.source] if args.source else None
        build(sources=srcs, dry_run=getattr(args, "dry_run", False))

    elif args.cmd == "stats":
        stats()

    elif args.cmd == "search":
        results = search_books(
            args.query,
            subject_filter=getattr(args, "subject", None),
            source_filter=getattr(args, "source", None),
            k=args.k,
        )
        if not results:
            print("No results. Make sure the index is built: python3 learn_books.py build")
            return
        print(f"\n{len(results)} results for '{args.query}':\n")
        for r in results:
            print(f"  [{r['subject']}] {r['title']}")
            if r["authors"]:
                print(f"    Authors : {r['authors']}")
            print(f"    Format  : {r['format']}   Source: {r['source']}   Score: {r['score']}")
            print(f"    URL     : {r['url']}")
            print()

    else:
        p.print_help()


if __name__ == "__main__":
    main()
