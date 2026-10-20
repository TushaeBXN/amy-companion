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


def sources_text(messages: list) -> str:
    """The text a reply may legitimately quote: everything she was given, never her own earlier words.

    That is the system prompt, Brian's messages (with the memory and library blocks attached to
    them) and tool results. Her own earlier replies are left out: if she invented a case name or a
    quotation last turn, repeating it must not count as having a source.
    """
    parts = []
    for m in messages:
        data = m if isinstance(m, dict) else (m.model_dump() if hasattr(m, "model_dump") else dict(m))
        if data.get("role") == "assistant":
            continue
        parts.append(str(data.get("content") or ""))
    return "\n".join(parts)


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


# ── case names she can't source ──────────────────────────────────────────────

_CASE = re.compile(
    r"\b((?:[A-Z][\w'&-]*\s+){0,3}[A-Z][\w'&-]*)\s+v\.\s+((?:[A-Z][\w'&-]*\s+){0,2}[A-Z][\w'&-]*)")
_NOT_NAMES = {"the", "in", "see", "under", "and", "but", "also", "that", "this", "as", "of", "per", "from", "with"}


def case_names(reply: str) -> list[str]:
    """Case names like "Washington v. Davis" in *reply*, as written."""
    out = []
    for m in _CASE.finditer(reply):
        left = [w for w in m.group(1).split() if w.lower() not in _NOT_NAMES or w[0].isupper()]
        # keep the capitalised run directly before "v." (drops a leading "In"/"Under")
        while left and left[0].lower() in _NOT_NAMES:
            left.pop(0)
        if left:
            out.append(f"{' '.join(left)} v. {m.group(2).strip()}")
    return list(dict.fromkeys(out))


def unsourced_cases(reply: str, sources: str, library_has: Optional[Callable[[str], bool]] = None) -> list[str]:
    """Case names in *reply* that appear neither in *sources* nor in the library."""
    haystack = normalize(sources)
    missing = []
    for name in case_names(reply):
        norm = normalize(name)
        if norm in haystack:
            continue
        try:
            if library_has is not None and library_has(norm):
                continue
        except Exception:
            pass
        missing.append(name)
    return missing


# ── does the passage she cited belong to the claim she is defending? ─────────

_LIB_ID = re.compile(r"\blib-[0-9a-f]{8,20}\b")


def cited_ids(reply: str) -> list[str]:
    """Library passage IDs mentioned in *reply* (as shown to her: 'lib-' plus hex digits)."""
    return list(dict.fromkeys(_LIB_ID.findall(reply)))


def unsupported_citations(reply: str, claim: str, ids_for: Callable[[str], list[str]]) -> list[str]:
    """IDs cited in *reply* that don't turn up when the library is searched for *claim*.

    *claim* is what she said before (the thing being challenged); *ids_for(text)*
    returns the IDs of the library passages that best match *text*. A passage
    that supports a claim should be among the closest passages to that claim; if
    it isn't, she probably pulled whatever matched the challenge's wording.
    """
    ids = cited_ids(reply)
    if not ids or not (claim or "").strip():
        return []
    try:
        found = ids_for(claim)
    except Exception:
        return []
    return [i for i in ids if not any(f.startswith(i) or i.startswith(f) for f in found)]


def footnote(missing: list[str]) -> str:
    """The note appended to a reply that quotes something she can't back up."""
    if not missing:
        return ""
    shown = "\n".join(f"  • “{p[:110].rstrip()}{'…' if len(p) > 110 else ''}”" for p in missing[:3])
    plural = "these quotations" if len(missing) > 1 else "this quotation"
    return (f"\n\n⚠️ I couldn't find {plural} word for word in my library or the sources I just read, "
            f"so treat {'them' if len(missing) > 1 else 'it'} as unconfirmed:\n{shown}")


def notes(unverified_quotes: list[str], cases: list[str], weak_ids: list[str]) -> str:
    """One note covering everything she couldn't back up ('' when there is nothing)."""
    out = footnote(unverified_quotes)
    if cases:
        out += ("\n\n⚠️ I couldn't find " + ("these case names" if len(cases) > 1 else "this case name")
                + " in my library or the sources I just read, so I may be going from memory: "
                + "; ".join(cases[:4]) + ".")
    if weak_ids:
        out += ("\n\n⚠️ The passage I cited (" + ", ".join(weak_ids[:3]) + ") did not come up when I searched my "
                "library for what I'd just said, so it may not support that claim. Check it before relying on it.")
    return out
