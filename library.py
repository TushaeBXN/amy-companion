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
    python3 library.py add mandate.pdf --title "Mandate for Leadership (Project 2025)" --author "Heritage Foundation" --year 2023
                                             # e.g. Project 2025's public PDF, so Amy can quote it by chapter
    python3 library.py conan amendment-14    # Constitution Annotated: how courts read each clause, with the cases
                                             # (also article-1 … article-7, amendment-1 … amendment-27)
    python3 library.py list                  # what's in the library
    python3 library.py search "separation of powers"

Re-running is safe: sections keep the same IDs, so they are replaced rather than
duplicated. Stop Amy while building (Ctrl+C in her window), then start her again,
so she sees the new sections.

Texts come from Project Gutenberg and the National Archives (public domain). Each download is checked
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
    ("constitution", 5, "Constitution", "U.S. Constitution · {heading}",
     # Gutenberg #5 writes "Article 1", "ARTICLE 2", "ARTICLE THREE"; one heading per line.
     r"^\s*(?:article|amendment)\.?\s+(?:[ivxl]+|\d+|[a-z]+(?:-[a-z]+)?)\.?\s*$"),
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
# Official transcripts from the National Archives (Gutenberg's Constitution
# leaves out the amendments). The heading pattern accepts "Amendment I" and
# "AMENDMENT XI"; each amendment becomes its own section.
_AMENDMENT_RE = r"^\s*amendment\s+(?:[ivxl]+|\d+)\.?\s*$"
CORE += [
    ("amendments-1-10", "https://www.archives.gov/founding-docs/bill-of-rights-transcript", "Amendment",
     "U.S. Constitution · {heading} (Bill of Rights)", _AMENDMENT_RE),
    ("amendments-11-27", "https://www.archives.gov/founding-docs/amendments-11-27", "Amendment",
     "U.S. Constitution · {heading}", _AMENDMENT_RE),
]
HTML_SECTION_MAX = 3500   # the longest amendment (the 14th) is ~2,600 characters; the rest is page footer

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


def split_sections(text: str, heading_re: Optional[str], min_chars: int = MIN_SECTION_CHARS) -> list[tuple[str, str]]:
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
            if len(body) >= min_chars:
                sections.append((m.group(0).strip(), body))
    if len(sections) < 2:
        parts = _size_chunks(text)
        return [(f"part {i + 1} of {len(parts)}" if len(parts) > 1 else "", p) for i, p in enumerate(parts)]
    return sections


def citation(template: str, heading: str) -> str:
    """Fill a citation template: {n} is the section number, {heading} its heading."""
    heading = heading.strip()
    nice = heading if heading.startswith("part ") else re.sub(
        r"\b[IVXLivxl]+\b", lambda m: m.group(0).upper(), heading.rstrip(".").title())  # keep "IV", not "Iv"
    if "{n}" in template:
        n = re.search(r"\d+", heading)
        return template.replace("{n}", n.group(0) if n else nice)
    if "{heading}" in template:
        return template.replace("{heading}", nice).strip(" ·")
    return f"{template} · {nice}" if heading else template


def section_id(key: str, heading: str, n: int) -> str:
    return "lib-" + hashlib.sha256(f"{key}|{n}|{heading}".encode()).hexdigest()[:20]


def make_sections(key: str, title_template: str, text: str, heading_re: Optional[str],
                  min_chars: int = MIN_SECTION_CHARS) -> list[dict]:
    """The drawers for one document: id, citation and text."""
    out = []
    for n, (heading, body) in enumerate(split_sections(text, heading_re, min_chars)):
        cite = citation(title_template, heading)
        out.append({"id": section_id(key, heading, n), "room": key, "cite": cite,
                    "content": f"[{cite}]\n{body}"})
    return out


# ── Engram ────────────────────────────────────────────────────────────────────

def _engram():
    import engram_bridge as eb
    eb._init()
    return eb._PALACE, eb._SEARCHER.backend


def store(sections: list[dict], source: str, palace=None, backend=None, kind: str = "primary") -> int:
    """Save and index sections in the library wing. Returns how many.

    With Engram 1.9+ each section also records what kind of source it is
    (*kind*: "primary" for the documents themselves, "secondary" for
    commentary such as the Constitution Annotated) and its citation, so
    Amy's views can point at it as evidence and `why` can tell text from
    explanation.
    """
    from engram.chateau import Drawer
    from engram.indexing import index_drawers

    if palace is None:
        palace, backend = _engram()
    # Rebuilding a document: drop its old sections that the new split doesn't have.
    from engram.indexing import unindex_drawer
    keep = {s["id"] for s in sections}
    for room in {s["room"] for s in sections}:
        for old in list(palace.iter_drawers(wing=WING, room=room)):
            if old.id not in keep:
                unindex_drawer(backend, old.id)
                palace.delete_drawer(old.wing, old.room, old.hall, old.id)
    items = []
    for s in sections:
        extra = ({"source": kind, "source_ref": s.get("cite", "")}
                 if kind and "source" in Drawer.__dataclass_fields__ else {})
        drawer = Drawer(content=s["content"], wing=WING, room=s["room"], hall=HALL, id=s["id"],
                        tags=["library", f"source:{source}"], **extra)
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


def html_text(html: str) -> str:
    """Readable text of a web page's main content, one block per line."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    lines = [ln.strip() for ln in root.get_text("\n").splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def download(url: str, path: Path) -> str:
    """The page at *url*, cached in *path*."""
    if not path.exists():
        print(f"  downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (amy-companion library loader)"})
        with urllib.request.urlopen(req, timeout=60) as r:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.read())
    return path.read_text(encoding="utf-8", errors="replace")


def fetch_html(key: str, url: str, expect: str) -> Optional[str]:
    CACHE.mkdir(exist_ok=True)
    text = html_text(download(url, CACHE / f"{key}.html"))
    if expect.lower() not in text.lower():
        print(f"  ! skipped: {url} doesn't mention {expect!r}")
        return None
    return text


def build(extended: bool = False) -> None:
    total = 0
    for key, source, expect, template, heading_re in CORE + (EXTENDED if extended else []):
        print(f"{key}:")
        is_web = isinstance(source, str)
        try:
            text = fetch_html(key, source, expect) if is_web else fetch_gutenberg(source, expect)
        except OSError as exc:
            print(f"  ! could not download: {exc}")
            continue
        if not text:
            continue
        # Official transcripts have no table of contents, and some amendments are one sentence.
        sections = make_sections(key, template, text, heading_re, min_chars=40 if is_web else MIN_SECTION_CHARS)
        if is_web:  # trim page footers that follow the last section
            for s in sections:
                s["content"] = s["content"][:HTML_SECTION_MAX]
        n = store(sections, source if is_web else f"gutenberg:{source}")
        total += n
        print(f"  {n} sections, e.g. [{sections[0]['cite']}]")
    print(f"\nDone: {total} sections in Amy's library. Restart Amy so she sees them.")


def read_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return "\n\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    return path.read_text(encoding="utf-8", errors="replace")


def pdf_pages(path: Path) -> list[str]:
    from pypdf import PdfReader
    return [page.extract_text() or "" for page in PdfReader(str(path)).pages]


def page_sections(key: str, template: str, pages: list[str], size: int = FALLBACK_CHUNK_CHARS) -> list[dict]:
    """Group PDF pages into sections cited by page range ("pp. 12–14")."""
    out, start, buf = [], 0, []
    for i, text in enumerate(pages + [None]):
        if text is not None and (not buf or sum(map(len, buf)) + len(text) <= size):
            buf.append(text)
            continue
        if buf and "".join(buf).strip():
            first, last = start + 1, start + len(buf)
            pages_label = f"p. {first}" if first == last else f"pp. {first}–{last}"
            cite = f"{template} · {pages_label}"
            out.append({"id": section_id(key, pages_label, len(out)), "room": key, "cite": cite,
                        "content": f"[{cite}]\n" + "\n\n".join(b.strip() for b in buf if b.strip())})
        start, buf = i, [text] if text is not None else []
    return out


def add_file(path: Path, title: str, author: str = "", year: str = "", split: Optional[str] = None) -> int:
    key = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "document"
    template = " · ".join(x for x in (title, author, year) if x)
    if path.suffix.lower() == ".pdf" and not split:
        sections = page_sections(key, template, pdf_pages(path))  # cite by page number
    else:
        text = read_file(path)
        if not text.strip():
            raise SystemExit(f"No text found in {path}")
        sections = make_sections(key, template, text, split)
    if not sections:
        raise SystemExit(f"No text found in {path}")
    n = store(sections, f"file:{path.name}", kind="")  # could be anything: don't guess
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


# ── Constitution Annotated (Congressional Research Service) ──────────────────
# The official, public-domain explanation of how courts have read each clause,
# with the cases. Published at constitution.congress.gov and mirrored by
# Cornell's Legal Information Institute; the importer tries them in order.
CONAN_SITES = [
    ("https://constitution.congress.gov/browse/{part}/", "/browse/essay/"),
    ("https://www.law.cornell.edu/constitution-conan/{part}", "/constitution-conan/{part}/"),
]
CONAN_TITLE = "Constitution Annotated (Congressional Research Service)"


def page_links(html: str, base: str, contains: str) -> list[str]:
    """Absolute URLs of links on a page whose path contains *contains*, in page order."""
    from urllib.parse import urljoin, urldefrag

    from bs4 import BeautifulSoup
    out = []
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        url = urldefrag(urljoin(base, a["href"]))[0]
        if contains in url and url.rstrip("/") != base.rstrip("/") and url not in out:
            out.append(url)
    return out


def page_title(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("h1") or soup.find("title")
    return re.sub(r"\s+", " ", tag.get_text(" ")).strip() if tag else ""


def add_conan(part: str = "amendment-14", limit: int = 300, delay: float = 0.5) -> int:
    """Load the Constitution Annotated essays for *part* ("amendment-14", "article-1", ...).

    Each essay becomes a section cited by its title, e.g.
    "[Constitution Annotated (Congressional Research Service) · Amdt14.S1.8.1 Overview of Equal Protection]".
    """
    import time

    key = f"conan-{part}"
    for index_tpl, contains_tpl in CONAN_SITES:
        index_url, contains = index_tpl.format(part=part), contains_tpl.format(part=part)
        try:
            index = download(index_url, CACHE / key / f"index-{hashlib.sha256(index_url.encode()).hexdigest()[:12]}.html")
        except OSError as exc:
            print(f"  ! {index_url}: {exc}")
            continue
        links = page_links(index, index_url, contains)[:limit]
        if not links:
            print(f"  ! no essay links found on {index_url}")
            continue
        print(f"  {len(links)} essays listed on {index_url}")
        sections = []
        for n, url in enumerate(links):
            path = CACHE / key / (re.sub(r"[^A-Za-z0-9.-]+", "_", url.split("://", 1)[-1])[-120:] + ".html")
            fresh = not path.exists()
            try:
                html = download(url, path)
            except OSError as exc:
                print(f"  ! {url}: {exc}")
                continue
            title, text = page_title(html), html_text(html)
            if len(text) < 200:
                continue
            cite = f"{CONAN_TITLE} · {title}" if title else f"{CONAN_TITLE} · {part}"
            sections.append({"id": section_id(key, url, n), "room": key, "cite": cite,
                             "content": f"[{cite}]\n{text}"})
            if fresh and delay:
                time.sleep(delay)  # be polite to the server
        if sections:
            n = store(sections, index_url, kind="secondary")  # official explanation, not the text
            print(f"  {n} essays stored, e.g. [{sections[0]['cite']}]")
            return n
    print("  ! could not load the Constitution Annotated from any site; paste this output to get it fixed")
    return 0


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
    c = sub.add_parser("conan", help="load Constitution Annotated essays, e.g. amendment-14 or article-1")
    c.add_argument("part", nargs="?", default="amendment-14")
    c.add_argument("--limit", type=int, default=300)
    sub.add_parser("list")
    s = sub.add_parser("search")
    s.add_argument("query")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        build(args.extended)
    elif args.cmd == "add":
        add_file(args.file, args.title, args.author, args.year, args.split)
    elif args.cmd == "conan":
        print(f"conan {args.part}:")
        add_conan(args.part, limit=args.limit)
        print("Restart Amy so she sees them.")
    elif args.cmd == "list":
        list_library()
    else:
        import engram_bridge as eb
        for hit in json.loads(eb.library_search(args.query)):
            print(f"[{hit['cite']}]\n  {hit['text'][:300]}\n")


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    main()
