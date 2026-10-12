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
    _SEARCHER = Searcher(get_backend("chromadb"), _PALACE)
    _LAYERS = LayerStack(_PALACE, _SEARCHER)
    _DIARY = AgentDiary("amy")


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
) -> str:
    """Store a new memory. Content is sanitized before storage."""
    _init()
    from engram.chateau import Drawer
    safe = _sanitize(content)
    drawer = Drawer(content=safe, wing=wing, room=room, hall=hall, pinned=pinned)
    _PALACE.save_drawer(drawer)
    _SEARCHER.backend.add(
        id=drawer.id,
        text=safe,
        metadata={"wing": wing, "room": room, "hall": hall,
                  "timestamp": drawer.timestamp, "pinned": pinned},
    )
    return json.dumps({"stored": True, "wing": wing, "room": room, "hall": hall})


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
    """Semantic search across all memories. Returns ranked results."""
    _init()
    results = _SEARCHER.search(query, n=n, wing=wing)
    out = []
    for r in results:
        out.append({
            "text": r["text"][:400],
            "score": _score(r),
            # Engram puts a hit's location under "metadata".
            "wing": (r.get("metadata") or {}).get("wing", ""),
            "room": (r.get("metadata") or {}).get("room", ""),
            "hall": (r.get("metadata") or {}).get("hall", ""),
        })
    return json.dumps(out, indent=2)


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
