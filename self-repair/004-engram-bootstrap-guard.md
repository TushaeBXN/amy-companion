# Fix 004 — Engram Bootstrap Guard (facts not indexed on first run)

**Date:** 2026-09-17  
**Symptom:** `bootstrap_from_amy_self()` returned "Already bootstrapped" but semantic search found 0 facts. The château appeared empty.  
**Affected:** `engram_bridge.py`, first-time Engram setup

---

## What broke

The original bootstrap guard checked if `~/.engram/identity.txt` existed and had content:

```python
if IDENTITY_PATH.exists() and IDENTITY_PATH.stat().st_size > 10:
    return "Already bootstrapped"
```

But `identity.txt` gets written first — before ChromaDB downloads its embedding model (all-MiniLM-L6-v2, ~79MB). When the model download ran, it took time. The bootstrap function had already written `identity.txt`, seen it exist on the next call, and returned early — before any facts were actually indexed into ChromaDB.

Result: identity file existed, ChromaDB was empty, bootstrap said "already done."

---

## Root cause

The guard checked the wrong thing. File existence ≠ facts indexed. The real source of truth is whether drawers exist in the château, not whether the identity file exists.

---

## The fix

**File changed:** `engram_bridge.py`, `bootstrap_from_amy_self()`

**Before:**
```python
if IDENTITY_PATH.exists() and IDENTITY_PATH.stat().st_size > 10:
    return "Already bootstrapped"
```

**After:**
```python
_init()
existing = list(_PALACE.iter_drawers(wing="amy", room="identity", hall="facts"))
if existing and IDENTITY_PATH.exists() and IDENTITY_PATH.stat().st_size > 10:
    return f"Already bootstrapped — {len(existing)} facts indexed."
```

**How it works:**
1. Call `_init()` first to ensure ChromaDB and the château are ready
2. Check if drawers actually exist in the `amy/identity/facts` location
3. Only return early if BOTH the identity file AND the drawer facts exist
4. This means if ChromaDB was empty (model hadn't downloaded yet), bootstrap will run again and fill it

---

## What to watch for next time

- If `engram_memory action=search` returns 0 results after bootstrap, the ChromaDB embedding model may not have finished downloading
- Run bootstrap again — the new guard will detect the empty château and re-index
- The embedding model is cached after first download at `~/.cache/chroma/` — subsequent runs are fast

---

## Pattern: Guard conditions must check the actual state, not a proxy

**Generalizable lesson:** "Already done" guards should check the real output of the work (facts in the database) not an intermediate artifact (a file that gets written early in the process). A file existing proves the process started, not that it finished.
