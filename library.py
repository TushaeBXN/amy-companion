"""Amy's reference library: primary sources she can quote instead of paraphrasing.

Founding documents, speeches and classic works are split into citable
sections (each Federalist paper, each chapter) and stored in Engram's
"library" wing. Every section starts with its citation, for example
"[Federalist No. 51 · Hamilton, Madison and Jay · 1788]", so when Amy finds a
passage she knows exactly where it came from.

Usage:
    python3 library.py build                 # the core set (founding documents and speeches)
    python3 library.py build --extended      # plus Democracy in America and The Wealth of Nations
    python3 library.py add FILE --title "Title" [--author A] [--year Y] [--split REGEX]
                                             # your own .txt, .md or .pdf
    python3 library.py list                  # what's in the library
    python3 library.py search "separation of powers"

Re-running is safe: sections keep the same IDs, so they are replaced rather than
duplicated. Stop Amy while building (Ctrl+C in her window), then start her again,
so she sees the new sections.

Texts come from Project Gutenberg (public domain). Each download is checked
against the title it should have; a mismatch is skipped with a warning.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
CACHE = HERE / "library_cache"
WING = "library"
HALL = "discoveries"            # not "facts": keeps the library out of the wake-up summary
MIN_SECTION_CHARS = 400        # shorter "sections" are tables of contents or headers
FALLBACK_CHUNK_CHARS = 6000    # documents without headings are cut into parts this size

_GUTENBERG = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt"

# (key, Gutenberg id, expected word in its title, citation, heading regex for sections)
CORE = [
    ("declaration", 1, "Declaration", "Declaration of Independence · 1776", None),
    ("constitution", 5, "Constitution", "U.S. Constitution and Amendments",
     r"^\s*(?:Article\.?\s+[IVX]+\b.*|Amendment\s+[IVXL0-9]+\b.*|ARTICLE\s+[IVX]+\b.*|AMENDMENT\s+[IVXL0-9]+\b.*)$"),
    ("gettysburg", 4, "Gettysburg", "Gettysburg Address · Lincoln · 1863", None),
    ("common-sense", 147, "Common Sense", "Common Sense · Thomas Paine · 1776",
     r"^\s*(?:OF THE ORIGIN AND DESIGN.*|OF MONARCHY AND HEREDITARY SUCCESSION.*|"
     r"THOUGHTS ON THE PRESENT STATE OF AMERICAN AFFAIRS.*|OF THE PRESENT ABILITY OF AMERICA.*|"
     r"INTRODUCTION.*|APPENDIX.*)$"),
    ("federalist", 1404, "Federalist", "Federalist No. {n} · Hamilton, Madison and Jay · 1787–88",
     r"^\s*FEDERALIST\.?\s+No\.\s*(\d+)\s*$"),
    ("douglass-narrative", 23, "Douglass",
     "Narrative of the Life of Frederick Douglass · 1845 · {heading}",
     r"^\s*(?:CHAPTER\s+[IVXL]+\.?|PREFACE|LETTER FROM WENDELL PHILLIPS.*|APPENDIX)\s*$"),
]
EXTENDED = [
    ("democracy-in-america", 815, "Democracy in America",
     "Democracy in America, Vol. 1 · Tocqueville · 1835 · {heading}", r"^\s*Chapter\s+[IVXL]+[.:]?.*$"),
    ("wealth-of-nations", 3300, "Wealth of Nations",
     "The Wealth of Nations · Adam Smith · 1776 · {heading}", r"^\s*CHAPTER\s+[IVXL]+\.?.*$"),
]


# ── text handling ─────────────────────────────────────────────────────────────

def strip_gutenberg(text: str) -> str:
    """The book itself, without Project Gutenberg's header and licence."""
    start = re.search(r"^\*\*\* ?START OF (?:THE|THIS) PROJECT GUTENBERG.*$", text, re.M)
    end = re.search(r"^\*\*\* ?END OF (?:THE|THIS) PROJECT GUTENBERG.*$", text, re.M)
    body = text[start.end() if start else 0: end.start() if end else len(text)]
    return body.replace("\r\n", "\n").strip()


def gutenberg_title(text: str) -> str:
    m = re.search(r"^Title:\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


def _size_chunks(text: str, size: int = FALLBACK_CHUNK_CHARS) -> list[str]:
    paras, chunks, cur = re.split(r"\n\s*\n", text), [], ""
    for p in paras:
        if cur and len(cur) + len(p) > size:
            chunks.append(cur.strip())
            cur = ""
        cur += p + "\n\n"
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def split_sections(text: str, heading_re: Optional[str]) -> list[tuple[str, str]]:
    """``(heading, text)`` pairs. Headings that introduce less than
    MIN_SECTION_CHARS of text (a table of contents) are dropped. Without a
    pattern, or if it finds fewer than two real sections, the text is cut into
    parts of about FALLBACK_CHUNK_CHARS."""
    sections: list[tuple[str, str]] = []
    if heading_re:
        rx = re.compile(heading_re, re.M | re.I)
        marks = list(rx.finditer(text))
        for i, m in enumerate(marks):
            body = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)].strip()
            if len(body) >= MIN_SECTION_CHARS:
                sections.append((m.group(0).strip(), body))
    if len(sections) < 2:
        parts = _size_chunks(text)
        return [(f"part {i + 1} of {len(parts)}" if len(parts) > 1 else "", p) for i, p in enumerate(parts)]
    return sections


def citation(template: str, heading: str) -> str:
    """Fill a citation template: {n} is the section number, {heading} its heading."""
    heading = heading.strip()
    nice = heading if heading.startswith("part ") else heading.rstrip(".").title()
    if "{n}" in template:
        n = re.search(r"\d+", heading)
        return template.replace("{n}", n.group(0) if n else nice)
    if "{heading}" in template:
        return template.replace("{heading}", nice).strip(" ·")
    return f"{template} · {nice}" if heading else template


def section_id(key: str, heading: str, n: int) -> str:
    return "lib-" + hashlib.sha256(f"{key}|{n}|{heading}".encode()).hexdigest()[:20]


def make_sections(key: str, title_template: str, text: str, heading_re: Optional[str]) -> list[dict]:
    """The drawers for one document: id, citation and text."""
    out = []
    for n, (heading, body) in enumerate(split_sections(text, heading_re)):
        cite = citation(title_template, heading)
        out.append({"id": section_id(key, heading, n), "room": key, "cite": cite,
                    "content": f"[{cite}]\n{body}"})
    return out


# ── Engram ────────────────────────────────────────────────────────────────────

def _engram():
    import engram_bridge as eb
    eb._init()
    return eb._PALACE, eb._SEARCHER.backend


def store(sections: list[dict], source: str, palace=None, backend=None) -> int:
    """Save and index sections in the library wing. Returns how many."""
    from engram.chateau import Drawer
    from engram.indexing import index_drawers

    if palace is None:
        palace, backend = _engram()
    items = []
    for s in sections:
        drawer = Drawer(content=s["content"], wing=WING, room=s["room"], hall=HALL, id=s["id"],
                        tags=["library", f"source:{source}"])
        palace.save_drawer(drawer)
        items.append((drawer.id, drawer.content,
                      {"wing": WING, "room": s["room"], "hall": HALL,
                       "timestamp": drawer.timestamp, "pinned": False}))
    for i in range(0, len(items), 50):
        index_drawers(backend, items[i:i + 50], replace=True)
    return len(items)


def fetch_gutenberg(gid: int, expect: str) -> Optional[str]:
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"pg{gid}.txt"
    if not path.exists():
        url = _GUTENBERG.format(id=gid)
        print(f"  downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "amy-companion library loader"})
        with urllib.request.urlopen(req, timeout=60) as r:
            path.write_bytes(r.read())
    raw = path.read_text(encoding="utf-8", errors="replace")
    title = gutenberg_title(raw)
    if expect.lower() not in title.lower():
        print(f"  ! skipped: Gutenberg #{gid} is titled {title!r}, expected {expect!r}")
        return None
    return strip_gutenberg(raw)


def build(extended: bool = False) -> None:
    total = 0
    for key, gid, expect, template, heading_re in CORE + (EXTENDED if extended else []):
        print(f"{key}:")
        try:
            text = fetch_gutenberg(gid, expect)
        except OSError as exc:
            print(f"  ! could not download: {exc}")
            continue
        if not text:
            continue
        sections = make_sections(key, template, text, heading_re)
        n = store(sections, f"gutenberg:{gid}")
        total += n
        print(f"  {n} sections, e.g. [{sections[0]['cite']}]")
    print(f"\nDone: {total} sections in Amy's library. Restart Amy so she sees them.")


def read_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return "\n\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    return path.read_text(encoding="utf-8", errors="replace")


def add_file(path: Path, title: str, author: str = "", year: str = "", split: Optional[str] = None) -> int:
    text = read_file(path)
    if not text.strip():
        raise SystemExit(f"No text found in {path}")
    key = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "document"
    template = " · ".join(x for x in (title, author, year) if x)
    sections = make_sections(key, template, text, split)
    n = store(sections, f"file:{path.name}")
    print(f"Added {n} sections from {path.name}, e.g. [{sections[0]['cite']}]")
    return n


def list_library() -> None:
    palace, _ = _engram()
    counts: dict[str, int] = {}
    for d in palace.iter_drawers(wing=WING):
        counts[d.room] = counts.get(d.room, 0) + 1
    if not counts:
        print("The library is empty. Run: python3 library.py build")
    for room, n in sorted(counts.items()):
        print(f"{room:28} {n} sections")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--extended", action="store_true")
    a = sub.add_parser("add")
    a.add_argument("file", type=Path)
    a.add_argument("--title", required=True)
    a.add_argument("--author", default="")
    a.add_argument("--year", default="")
    a.add_argument("--split", default=None, help="regex for section headings (one per line)")
    sub.add_parser("list")
    s = sub.add_parser("search")
    s.add_argument("query")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        build(args.extended)
    elif args.cmd == "add":
        add_file(args.file, args.title, args.author, args.year, args.split)
    elif args.cmd == "list":
        list_library()
    else:
        import engram_bridge as eb
        for hit in json.loads(eb.library_search(args.query)):
            print(f"[{hit['cite']}]\n  {hit['text'][:300]}\n")


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    main()
