"""Check the quotations in Amy's reply against what she actually has.

Her local model sometimes writes a quotation from memory and labels it with a
source ("the Fifth Amendment says ..."), and the words are not the source's.
Nothing in a prompt stops that reliably, so this checks the reply after it is
written: every quotation of a meaningful length must appear, word for word, in
the text she was given this turn (her context and tool results) or in her
reference library. A quotation that can't be found is flagged at the end of
the reply, so Brian knows not to rely on it. It does not rewrite her reply and
it does not judge whether a quote is *apt*, only whether it is *there*.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

MIN_QUOTE_CHARS = 60      # shorter quotations (a phrase, a name) are not checked
MIN_FRAGMENT_WORDS = 5    # between ellipses and [edits], only runs of this many words are checked

_QUOTED = re.compile(r'[“"]([^“”"\n]{%d,}?)[”"]' % MIN_QUOTE_CHARS)
_EDIT = re.compile(r"\w*\[[^\]]*\]\w*")          # th[e], [sic], [the Court] : an edit, not source text
_SPLIT = re.compile(r"\.{3}|…|\s\.\s\.\s\.")


def quoted_passages(reply: str) -> list[str]:
    """Quotations in *reply*: text in quotation marks, and lines starting with '>'."""
    found = [m.group(1) for m in _QUOTED.finditer(reply)]
    for line in reply.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith(">"):
            continue
        body = stripped.lstrip("> ").strip()
        if body.startswith(("—", "-- ", "– ")) or len(body) < MIN_QUOTE_CHARS:
            continue                                   # an attribution line or a short one
        found.append(body.strip('“”" '))
    return list(dict.fromkeys(found))


def normalize(text: str) -> str:
    """Lower case, one space between words, no punctuation, no emphasis marks."""
    text = text.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"[*_`]", "", text)
    return " ".join(re.sub(r"[^a-z0-9']+", " ", text.lower()).split())


def fragments(passage: str) -> list[str]:
    """The checkable runs of a quotation: split at ellipses and bracketed edits."""
    passage = _EDIT.sub(" ... ", passage)
    out = []
    for part in _SPLIT.split(passage):
        norm = normalize(part)
        if len(norm.split()) >= MIN_FRAGMENT_WORDS:
            out.append(norm)
    return out


def unverified(reply: str, sources: str, library_has: Optional[Callable[[str], bool]] = None) -> list[str]:
    """Quotations in *reply* whose words are not in *sources* or the library.

    *sources* is all the text she was given this turn. *library_has(fragment)*
    is asked about any fragment *sources* doesn't contain (a library search).
    """
    haystack = normalize(sources)
    missing = []
    for passage in quoted_passages(reply):
        for frag in fragments(passage):
            if frag in haystack:
                continue
            if library_has is not None:
                try:
                    if library_has(frag):
                        continue
                except Exception:
                    pass
            missing.append(passage)
            break
    return missing


def footnote(missing: list[str]) -> str:
    """The note appended to a reply that quotes something she can't back up."""
    if not missing:
        return ""
    shown = "\n".join(f"  • “{p[:110].rstrip()}{'…' if len(p) > 110 else ''}”" for p in missing[:3])
    plural = "these quotations" if len(missing) > 1 else "this quotation"
    return (f"\n\n⚠️ I couldn't find {plural} word for word in my library or the sources I just read, "
            f"so treat {'them' if len(missing) > 1 else 'it'} as unconfirmed:\n{shown}")
