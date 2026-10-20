"""
Engram bridge for Amy — wraps Engram's Python API for tool use.

Provides: memory storage, semantic search, agent diary, knowledge graph.
All data lives in ~/.engram/ — never leaves the machine, never committed to git.

Security rules enforced here:
- Sanitize content before storage: strip anything that looks like a token/key
- No network calls — everything is local (ChromaDB, SQLite)
- ~/.engram/ is outside the git repo; this module never writes to ava-companion/
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Optional

# ── init helpers ──────────────────────────────────────────────────────────────

_PALACE = None
_SEARCHER = None
_LAYERS = None
_DIARY = None

# Patterns that look like secrets — refuse to store these
_SECRET_RE = re.compile(
    r"(AAAA[a-zA-Z0-9_\-]{20,}|"     # Telegram bot token shape
    r"sk-[a-zA-Z0-9]{20,}|"           # OpenAI-style keys
    r"AIza[a-zA-Z0-9_\-]{30,}|"       # Google API keys
    r"ghp_[a-zA-Z0-9]{30,}|"          # GitHub tokens
    r"[a-f0-9]{32,64})",               # raw hex secrets
    re.IGNORECASE,
)


def _sid(memory_id: str) -> str:
    """The part of an ID to show: enough to look the memory up again.

    Library passages have IDs like "lib-<20 hex>", so 8 characters would keep
    only 4 hex digits and collide across a few hundred passages; Engram's own
    short form keeps the prefix plus 8 characters.
    """
    try:
        from engram.lineage import short_id
        return short_id(memory_id)
    except ImportError:  # Engram older than 1.6
        return memory_id[:8]


def _sanitize(text: str) -> str:
    """Replace anything that looks like a secret with [REDACTED]."""
    return _SECRET_RE.sub("[REDACTED]", text)


def _init():
    """Lazy init — only runs on first use."""
    global _PALACE, _SEARCHER, _LAYERS, _DIARY
    if _PALACE is not None:
        return
    from engram.chateau import Chateau
    from engram.backends import get_backend
    from engram.searcher import Searcher
    from engram.layers import LayerStack
    from engram.agents import AgentDiary
    from engram.config import ensure_engram_dir

    ensure_engram_dir()
    _PALACE = Chateau()
    _SEARCHER = Searcher(_with_keyword_index(get_backend("chromadb")), _PALACE)
    _LAYERS = LayerStack(_PALACE, _SEARCHER)
    _DIARY = AgentDiary("amy")


def _with_keyword_index(backend):
    """Add Engram's exact-word index (Engram 1.5+, on by default there).

    It finds names, numbers and rare words that meaning search misses. Older
    Engram versions, or "keyword_search": false in ~/.engram/config.json,
    keep the plain backend.
    """
    try:
        from engram.config import load_config
        from engram.keyword_index import with_keyword_index
    except ImportError:
        return backend
    cfg = load_config()
    if not cfg.get("keyword_search", False):
        return backend
    try:
        return with_keyword_index(backend, cfg)
    except Exception:
        return backend


# ── L0 identity setup ─────────────────────────────────────────────────────────

def set_identity(text: str) -> str:
    """Write Amy's L0 identity block (~50 tokens, always loaded)."""
    _init()
    _LAYERS.set_identity(_sanitize(text))
    return "Identity set."


def get_identity() -> str:
    """Read the current L0 identity."""
    _init()
    return _LAYERS.l0()


# ── wake-up context ──────────────────────────────────────────────────────────

def wake_up(wing: str = "amy", rebuild_l1: bool = False) -> str:
    """
    Return L0+L1 cold-start context (~170 tokens, ES-compressed).
    Call at the start of every task to prevent drift.
    """
    _init()
    if rebuild_l1:
        _LAYERS.rebuild_l1(wing=wing)
    l0 = _LAYERS.l0()
    l1 = _LAYERS.l1()
    return f"{l0}\n\n{l1}"


# ── memory storage ────────────────────────────────────────────────────────────

def add_memory(
    content: str,
    wing: str = "amy",
    room: str = "general",
    hall: str = "facts",
    pinned: bool = False,
    source: str = "",
    source_ref: str = "",
    evidence=None,
) -> str:
    """Store a new memory. Content is sanitized before storage.

    With Engram 1.6+ the reply also lists "possible_updates": existing
    memories this one may replace. If it does, call correct_memory on the old
    one instead of keeping both.

    With Engram 1.9+, *source* says where it came from (user, primary,
    secondary, tool, inference), *source_ref* gives the citation or URL, and
    *evidence* lists IDs of memories it rests on. Older Engram ignores them.
    """
    _init()
    from engram.chateau import Drawer
    safe = _sanitize(content)
    rel = _relations()
    links = []
    if rel is not None:
        try:
            links = rel.resolve_evidence(_PALACE, based_on=_id_list(evidence))
            extra = {"source": rel.check_source(source), "source_ref": _sanitize(source_ref)}
        except Exception as e:  # checked before anything is saved
            return json.dumps({"stored": False, "error": str(e)})
    else:
        extra = {}
    drawer = Drawer(content=safe, wing=wing, room=room, hall=hall, pinned=pinned, **extra)
    _PALACE.save_drawer(drawer)
    _SEARCHER.backend.add(
        id=drawer.id,
        text=safe,
        metadata={"wing": wing, "room": room, "hall": hall,
                  "timestamp": drawer.timestamp, "pinned": pinned},
    )
    out = {"stored": True, "id": drawer.id, "wing": wing, "room": room, "hall": hall}
    if links:
        rel.attach_evidence(_PALACE, drawer.id, links, by="amy")
        out["evidence"] = [_sid(target) for _, target in links]
    try:
        from engram.lineage import possible_updates
    except ImportError:
        return json.dumps(out)
    hints = possible_updates(_SEARCHER.backend, _PALACE, drawer)
    if hints:
        out["possible_updates"] = [{"id": h["id"], "text": h["text"][:200]} for h in hints]
        out["note"] = ("If this new memory replaces one of these, call correct_memory on the "
                       "old one instead, so the outdated fact is marked as replaced.")
    return json.dumps(out)


# ── semantic search ───────────────────────────────────────────────────────────

def _score(result: dict) -> float:
    """The score the results are ordered by.

    With Engram's local reranker on (`engram setup-reranker`), results come back
    in reranker order, so report its 0-1 relevance; otherwise Engram's own score.
    """
    rerank = result.get("rerank_score")
    if rerank is not None:
        return round(1 / (1 + math.exp(-rerank)), 4)
    return round(result.get("final_score", 0), 3)


def search(query: str, n: int = 8, wing: Optional[str] = None) -> str:
    """Semantic search across all memories. Returns ranked results.

    With Engram 1.6+, a memory that was corrected is still returned (it is
    history), directly below its replacement, with "status": "OUTDATED", the
    ID of the current version and its text prefixed "[OUTDATED …]". Only the
    current version should be used as fact.
    """
    _init()
    results = _SEARCHER.search(query, n=n, wing=wing)
    out = []
    for r in results:
        meta = r.get("metadata") or {}
        item = {}
        if r.get("id"):
            item["id"] = r["id"]
        status = r.get("status")
        text = r["text"][:400]
        if status == "superseded":
            current = r.get("current_id") or r.get("superseded_by") or ""
            item["status"] = "OUTDATED"
            item["replaced_by"] = current
            if r.get("replaced_at"):
                item["replaced_on"] = r["replaced_at"][:10]
            if r.get("reason"):
                item["why"] = r["reason"]
            text = f"[OUTDATED, replaced by {_sid(current)}; do not use as current fact] {text}"
        elif status == "disputed":
            item["status"] = "DISPUTED"
            item["disputed_with"] = r.get("disputed_with", [])
            text = "[DISPUTED: conflicts with another memory; check before relying on it] " + text
        elif r.get("supersedes"):
            item["status"] = "CURRENT"
            item["replaces"] = r["supersedes"]
        item.update({
            "text": text,
            "score": _score(r),
            # Engram puts a hit's location under "metadata".
            "wing": meta.get("wing", ""),
            "room": meta.get("room", ""),
            "hall": meta.get("hall", ""),
        })
        out.append(item)
    return json.dumps(out, indent=2)


# ── corrections (Engram 1.6+) ─────────────────────────────────────────────────

def _lineage():
    import importlib
    try:
        lineage = importlib.import_module("engram.lineage")
    except ImportError:
        raise RuntimeError("corrections need Engram 1.6 or newer (pip install -U engram-chateau)")
    return lineage


def _relations():
    """engram.relations (Engram 1.9+: evidence, sources, why), or None on older Engram."""
    import importlib
    try:
        return importlib.import_module("engram.relations")
    except ImportError:
        return None


def _id_list(ids) -> list:
    """Evidence IDs from a list, or a comma/space separated string."""
    if not ids:
        return []
    if isinstance(ids, str):
        ids = re.split(r"[,\s]+", ids)
    return [str(i).strip() for i in ids if str(i).strip()]


def correct_memory(memory_id: str, content: str, reason: str = "") -> str:
    """Replace an outdated memory with corrected information.

    The old memory is kept as history (search shows it marked OUTDATED under
    the new one). *memory_id* is the "id" from a search result; the first 8+
    characters are enough. Retrying with the same content changes nothing.
    """
    _init()
    lineage = _lineage()
    try:
        res = lineage.correct_memory(_PALACE, _SEARCHER.backend, memory_id, _sanitize(content),
                                     reason=_sanitize(reason), by="amy")
    except lineage.LineageError as e:
        return json.dumps({"corrected": False, "error": str(e)})
    return json.dumps({"corrected": True, "already_done": not res["created"],
                       "old_id": res["old"]["id"], "new_id": res["new"]["id"],
                       "old_text": res["old"]["text"], "new_text": res["new"]["text"]})


def memory_history(memory_id: str) -> str:
    """Whether a memory is current, and what replaced what, when and why."""
    _init()
    lineage = _lineage()
    try:
        h = lineage.memory_history(_PALACE, memory_id)
    except lineage.LineageError as e:
        return json.dumps({"error": str(e)})
    return json.dumps({
        "id": h["memory"]["id"], "text": h["memory"]["text"], "status": h["status"],
        "current_id": h["current_id"],
        "history": [{"on": s["at"][:10], "old_id": s["old"], "new_id": s["new"],
                     "why": s.get("reason", ""), "was": s.get("old_text"), "now": s.get("new_text")}
                    for s in h["history"]],
    }, indent=2)


# ── evidence: why a memory says what it says (Engram 1.9+) ───────────────────

_NEEDS_19 = json.dumps({"error": "evidence and explanations need Engram 1.9 or newer "
                                 "(pip install -U engram-chateau)"})


def _brief(item: dict) -> dict:
    out = {"id": _sid(item["id"])}
    if item.get("missing"):
        return {**out, "missing": True}
    text = item.get("text", "")
    drawer = _PALACE.find_drawer(item["id"]) if len(text) >= 300 else None
    if drawer is not None:  # Engram trims; give enough of a passage to quote
        text = drawer.text().strip()
    first = text.split("\n", 1)[0]
    if first.startswith("[") and first.endswith("]") and "\n" in text:
        text = text.split("\n", 1)[1]  # the citation is already in "cite"
    out["text"] = text[:700]
    out["source"] = item.get("source") or "not recorded"
    if item.get("source_ref"):
        out["cite"] = item["source_ref"]
    if item.get("status") == "superseded":
        out["status"] = f"OUTDATED, replaced by {_sid(item.get('current_id', ''))}"
    elif item.get("status") == "disputed":
        out["status"] = "DISPUTED"
    if item.get("relation"):
        out["relation"] = item["relation"]
    if item.get("evidence"):
        out["rests_on"] = [_brief(e) for e in item["evidence"]]
    return out


def explain_memory(memory_id: str, query: str = "") -> str:
    """Why a memory says what it says: its source, the evidence it rests on (and
    whether that evidence was since corrected), earlier versions, conflicts, and
    what needs review. Use it when challenged on a view or before relying on one.
    """
    _init()
    rel = _relations()
    if rel is None:
        return _NEEDS_19
    try:
        w = rel.explain(_PALACE, memory_id, query=query or None, searcher=_SEARCHER if query else None)
    except Exception as e:
        return json.dumps({"error": str(e)})
    m = w["memory"]
    out = {
        "id": _sid(m["id"]), "text": m["text"][:600], "saved": m["timestamp"][:10],
        "source": w["source"], "kind": {"direct": "recorded as given", "supported": "conclusion resting on evidence",
                                        "inference": "inference"}[w["claim"]],
        "rests_on": [_brief(e) for e in w["evidence"]],
        "earlier_versions": [{"id": _sid(h["id"]), "was": h.get("text", "")[:240],
                              "replaced": h["replaced_at"][:10], "why": h.get("reason") or "",
                              "rested_on": [_brief(e) for e in h.get("evidence", [])]}
                             for h in w["history"]],
        "conflicts_with": [_brief(c) for c in w["conflicts"]],
        "needs_review": w["concerns"],
    }
    if m.get("source_ref"):
        out["cite"] = m["source_ref"]
    if w.get("relevance"):
        out["relevance"] = w["relevance"]
    return json.dumps(out, indent=2)


def link_memories(memory_id: str, relation: str, target_id: str, reason: str = "") -> str:
    """Record how two memories relate: based_on (target is evidence for memory),
    derived_from, contradicts, supersedes."""
    _init()
    rel = _relations()
    if rel is None:
        return _NEEDS_19
    try:
        out = rel.link_memories(_PALACE, memory_id, relation, target_id, reason=_sanitize(reason), by="amy")
    except Exception as e:
        return json.dumps({"linked": False, "error": str(e)})
    return json.dumps({"linked": True, "already": not out["created"], "memory": _sid(out["memory"]["id"]),
                       "relation": relation, "target": _sid(out["target"]["id"])})


def check_memory() -> str:
    """What needs review: views resting on corrected or disputed evidence,
    inferences with no evidence, unresolved conflicts. Changes nothing."""
    _init()
    rel = _relations()
    if rel is None:
        return _NEEDS_19
    r = rel.check(_PALACE)
    return json.dumps({
        "ok": r["ok"],
        "evidence_changed": [{"memory": _brief(i["memory"]), "evidence": _brief(i["evidence"])}
                             for i in r["stale_support"]],
        "no_evidence": [_brief(m) for m in r["unsupported_inferences"]],
        "unresolved_conflicts": [{"a": _brief(c["a"]), "b": _brief(c["b"])} for c in r["unresolved_conflicts"]],
        "broken_links": len(r["broken_links"]),
    }, indent=2)


# ── agent diary ───────────────────────────────────────────────────────────────

def diary_write(entry: str, tags: Optional[list] = None) -> str:
    """
    Write a diary entry (ES-compressed). Use after tasks to maintain
    continuity across sessions and prevent drift.
    """
    _init()
    safe = _sanitize(entry)
    _DIARY.write(safe, tags=tags or [])
    return "Diary entry written."


def diary_read(last_n: int = 5) -> str:
    """Read recent diary entries (decompressed for readability)."""
    _init()
    entries = _DIARY.read(last_n=last_n)
    if not entries:
        return "No diary entries yet."
    lines = []
    for e in entries:
        lines.append(f"[{e.timestamp[:10]}] {e.readable()}")
    return "\n\n".join(lines)


# ── knowledge graph ───────────────────────────────────────────────────────────

def kg_add(
    subject: str,
    predicate: str,
    obj: str,
    confidence: float = 1.0,
) -> str:
    """Add a fact triple to the temporal knowledge graph."""
    _init()
    from engram.knowledge_graph import KnowledgeGraph
    kg = KnowledgeGraph()
    kg.add(subject=subject, predicate=predicate, obj=obj, confidence=confidence)
    return json.dumps({"added": f"{subject} {predicate} {obj}"})


def kg_query(entity: str, predicate: Optional[str] = None) -> str:
    """Query the knowledge graph for what's known about an entity."""
    _init()
    from engram.knowledge_graph import KnowledgeGraph
    kg = KnowledgeGraph()
    triples = kg.query(entity=entity, predicate=predicate)
    if not triples:
        return f"Nothing in the knowledge graph about '{entity}'."
    return json.dumps([
        {"subject": t.subject, "predicate": t.predicate, "object": t.object,
         "confidence": t.confidence}
        for t in triples[:20]
    ], indent=2)


# ── initialise Amy's château on first run ─────────────────────────────────────

def bootstrap_from_amy_self(self_path: str) -> str:
    """
    One-time migration: seed Engram from amy_self.json.
    Writes L0 identity and migrates founding facts to the Amy wing.
    Safe to call repeatedly — won't duplicate if already done.
    """
    import os
    from engram.config import IDENTITY_PATH

    # Check if facts are already indexed (not just identity file)
    _init()
    existing = list(_PALACE.iter_drawers(wing="amy", room="identity", hall="facts"))
    if existing and IDENTITY_PATH.exists() and IDENTITY_PATH.stat().st_size > 10:
        return f"Already bootstrapped — {len(existing)} facts indexed."

    with open(self_path) as f:
        data = json.load(f)

    # L0 identity
    identity = data.get("identity", "")
    if identity:
        set_identity(identity[:800])  # cap for token budget

    # L1 founding facts → engram facts hall
    for fact in data.get("facts", []):
        text = fact.get("text", "").strip()
        if text and len(text) > 5:
            add_memory(text, wing="amy", room="identity", hall="facts",
                       pinned=fact.get("confidence", 0) >= 0.9)

    # Relationships → knowledge graph
    for rel in data.get("relationships", {}).get("people", []):
        name = rel.get("name", "")
        role = rel.get("role", "")
        if name and role:
            kg_add("Amy", "knows", name, confidence=1.0)
            kg_add(name, "role", role, confidence=1.0)

    return f"Bootstrapped: identity set, {len(data.get('facts', []))} facts migrated."


# ── reference library (library.py fills it) ──────────────────────────────────

def library_search(query: str, n: int = 4) -> str:
    """Passages from primary sources (Constitution, Federalist Papers, …), each with its citation.

    Quote these instead of paraphrasing from memory, and name the citation.
    """
    _init()
    results = _SEARCHER.search(query, n=n, wing="library")
    out = []
    for r in results:
        drawer = _PALACE.find_drawer(r["id"]) if hasattr(_PALACE, "find_drawer") else None
        first = drawer.content.split("\n", 1)[0] if drawer else ""
        cite = first[1:-1] if first.startswith("[") and first.endswith("]") else (r.get("metadata") or {}).get("room", "")
        text = r["text"]
        if text.startswith(first):
            text = text[len(first):].lstrip()
        out.append({"cite": cite, "text": text[:700], "id": r["id"]})
    if not out:
        return json.dumps({"note": "The library is empty or has nothing on this. "
                                   "Brian can fill it with: python3 library.py build"})
    return json.dumps(out, indent=2)


def library_has(fragment: str) -> bool:
    """True if the (already normalized) *fragment* appears word for word in a library passage."""
    _init()
    for r in _SEARCHER.search(fragment, n=6, wing="library"):
        drawer = _PALACE.find_drawer(r["id"]) if hasattr(_PALACE, "find_drawer") else None
        from quote_check import normalize
        if fragment in normalize(drawer.text() if drawer is not None else r["text"]):
            return True
    return False


_FOLLOW_UP = re.compile(
    r"\b(you (just )?(said|wrote|claimed)|your (answer|claim|view|last)|that (sounds|claim|answer|passage)|"
    r"supports? (that|this|it|what)|which (exact )?(passage|source|case|part)|what (about )?(that|this|it)\b|"
    r"back (that|it) up|prove (that|it)|are you sure|where (does|did) (that|it) come from)", re.I)


def follow_up_query(message: str, prior: str = "") -> str:
    """What to search the library for: *message*, plus her last answer when it only makes sense with it.

    "Which exact passage supports what you just said?" names no topic, so searching for those words alone
    returns whatever happens to match them. A short message, or one that points back at her last answer,
    is searched together with the tail of that answer.
    """
    prior = " ".join((prior or "").split())
    if not prior or not message:
        return message
    if len(message) < 120 or _FOLLOW_UP.search(message):
        return f"{prior[-600:]} {message}"
    return message


def library_context(query: str, n: int = 3, max_chars: int = 550) -> str:
    """Relevant library passages for *query*, ready to put in Amy's context ('' if none).

    Used automatically for big-question messages, so Amy sees the real text
    without having to decide to search. Only passages that actually match are
    included: meaning similarity of at least 0.25, a reranker relevance of at
    least 0.2, or half the question's keywords.
    """
    try:
        _init()
        results = _SEARCHER.search(query, n=n * 2, wing="library")
    except Exception:
        return ""
    picked = []
    for r in results:
        rerank = r.get("rerank_score")
        relevant = (r.get("semantic_score", 0) >= 0.25
                    or (rerank is not None and 1 / (1 + math.exp(-rerank)) >= 0.2)
                    or r.get("keyword_overlap", 0) >= 0.5)
        if not relevant:
            continue
        drawer = _PALACE.find_drawer(r["id"]) if hasattr(_PALACE, "find_drawer") else None
        first = drawer.content.split("\n", 1)[0] if drawer else ""
        cite = first[1:-1] if first.startswith("[") and first.endswith("]") else ""
        text = r["text"][len(first):].lstrip() if first and r["text"].startswith(first) else r["text"]
        picked.append(f"[{cite or 'library'} · id {_sid(r['id'])}]\n{text[:max_chars].strip()}")
        if len(picked) >= n:
            break
    if not picked:
        return ""
    return ("\n\n[From your library: exact text. Quote it and name the citation. If it doesn't "
            "answer the question, say you're going from memory.]\n" + "\n\n".join(picked))


# Wings that automatic memory lookup never draws from: the reference library (quoted on request, not
# recalled), and "default", where mined conversation logs land. Her own chat transcripts ("That's kind of you
# to say…", task status lines) are not memories Brian saved, and they match everyday questions better than
# the memories that should answer them. Override with AMY_MEMORY_SKIP_WINGS="library,default,other".
MEMORY_CONTEXT_SKIP_WINGS = tuple(
    w.strip() for w in os.environ.get("AMY_MEMORY_SKIP_WINGS", "library,default").split(",") if w.strip())


def memory_context(query: str) -> str:
    """Saved memories that clearly match *query*, ready to put in Amy's context ('' if none).

    Used on every message so she does not depend on deciding to search: "I'm
    meeting a friend for coffee" finds the coffee-shop preference without being
    asked. It is Engram's own auto-recall: only memories above the relevance
    bar, the current version of anything that was corrected first and the
    outdated one labelled, never the reference library, and nothing at all
    when nothing matches.
    """
    try:
        _init()
        from engram.recall import recall
        cfg = {**_SEARCHER.config, "recall": True, "recall_max": 3,
               "recall_exclude_wings": list(MEMORY_CONTEXT_SKIP_WINGS)}
        note = recall(query, searcher=_SEARCHER, cfg=cfg, remember=False)
    except Exception:  # older Engram, or the index is unavailable: carry on without it
        return ""
    return "\n\n" + note if note else ""


# ── Amy's views: what she currently thinks, and how that changed ──────────────

_VIEW_WING, _VIEW_ROOM, _VIEW_HALL = "amy", "views", "discoveries"


def _topic_key(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (topic or "").lower()).strip("-")[:60]


def _current_view(key: str):
    """The current drawer holding Amy's view on *key*, or None."""
    lineage = _lineage()
    led = lineage.Lineage(_PALACE.path)
    for d in _PALACE.iter_drawers(wing=_VIEW_WING, room=_VIEW_ROOM):
        if f"view:{key}" in d.tags and led.status(d.id)["status"] != "superseded":
            return d
    return None


def record_view(topic: str, view: str, reason: str = "", evidence=None) -> str:
    """Write down what Amy currently thinks about *topic*.

    If she already had a view on it, the new one replaces it (Engram keeps the
    old one as history, with *reason* and the evidence it rested on).
    Recording the same view again changes nothing, except to add evidence.

    *evidence* lists IDs of the library passages or memories the view rests
    on (Engram 1.9+). The view itself is saved as an inference, so it is never
    mistaken for a source.
    """
    _init()
    lineage = _lineage()
    rel = _relations()
    key = _topic_key(topic)
    if not key or not view.strip():
        return json.dumps({"recorded": False, "error": "a topic and a view are both needed"})
    links = []
    if rel is not None:
        try:
            links = rel.resolve_evidence(_PALACE, based_on=_id_list(evidence))
        except Exception as e:  # checked before anything is saved
            return json.dumps({"recorded": False, "error": str(e)})
    content = f"My view on {topic.strip()}: {_sanitize(view.strip())}"
    old = _current_view(key)

    def done(out: dict, view_id: str) -> str:
        if links:
            rel.attach_evidence(_PALACE, view_id, links, by="amy")
            out["evidence"] = [_sid(target) for _, target in links]
        elif rel is not None:
            out["note"] = (out.get("note", "") + " No evidence linked: pass evidence=[ids of the "
                           "library passages or memories this view rests on].").strip()
        elif evidence:
            out["note"] = "evidence links need Engram 1.9 or newer; the view was saved without them"
        return json.dumps(out)

    if old is None:
        from engram.chateau import Drawer
        from engram.indexing import index_drawer
        extra = {"source": "inference"} if rel is not None else {}
        drawer = Drawer(content=content, wing=_VIEW_WING, room=_VIEW_ROOM, hall=_VIEW_HALL,
                        tags=["view", f"view:{key}"], **extra)
        _PALACE.save_drawer(drawer)
        index_drawer(_SEARCHER.backend, drawer.id, drawer.content,
                     {"wing": _VIEW_WING, "room": _VIEW_ROOM, "hall": _VIEW_HALL,
                      "timestamp": drawer.timestamp, "pinned": False})
        return done({"recorded": True, "changed": False, "id": drawer.id, "topic": key}, drawer.id)
    if old.content.split(": ", 1)[-1].strip() == _sanitize(view.strip()):
        return done({"recorded": True, "changed": False, "id": old.id, "topic": key,
                     "note": "same as your current view."}, old.id)
    try:
        extra = {"source": "inference"} if rel is not None else {}
        res = lineage.correct_memory(_PALACE, _SEARCHER.backend, old.id, content,
                                     reason=_sanitize(reason), by="amy", **extra)
    except lineage.LineageError as e:
        return json.dumps({"recorded": False, "error": str(e)})
    return done({"recorded": True, "changed": True, "id": res["new"]["id"], "topic": key,
                 "previous_view": old.content, "since": old.timestamp[:10]}, res["new"]["id"])


def get_views(topic: str = "") -> str:
    """Amy's current view on *topic* with how it changed over time, or all her current views."""
    _init()
    lineage = _lineage()
    if topic:
        current = _current_view(_topic_key(topic))
        if current is None:
            return json.dumps({"topic": _topic_key(topic), "view": None,
                               "note": "You haven't recorded a view on this yet."})
        h = lineage.memory_history(_PALACE, current.id)
        out = {
            "topic": _topic_key(topic), "id": _sid(current.id), "view": current.content,
            "since": current.timestamp[:10],
            "how_it_changed": [{"on": s["at"][:10], "was": s.get("old_text"), "became": s.get("new_text"),
                                "why": s.get("reason", "")} for s in h["history"]],
        }
        rel = _relations()
        if rel is not None:
            w = rel.explain(_PALACE, current.id, depth=1)
            out["rests_on"] = [_brief(e) for e in w["evidence"]]
            out["earlier_views_rested_on"] = {_sid(v["id"]): [_brief(e) for e in v.get("evidence", [])]
                                              for v in w["history"] if v.get("evidence")}
            if w["concerns"]:
                out["needs_review"] = w["concerns"]
        return json.dumps(out, indent=2)
    led = lineage.Lineage(_PALACE.path)
    views = [{"topic": next((t[5:] for t in d.tags if t.startswith("view:")), ""),
              "id": _sid(d.id), "view": d.content, "since": d.timestamp[:10]}
             for d in _PALACE.iter_drawers(wing=_VIEW_WING, room=_VIEW_ROOM)
             if "view" in d.tags and led.status(d.id)["status"] != "superseded"]
    return json.dumps(sorted(views, key=lambda v: v["topic"]), indent=2)
