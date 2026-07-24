"""Load Nia's educational knowledge base into Amy's memory RAG.

Reads Nia's knowledge files and stores them as searchable embeddings in
amy_memory.db so Amy can surface them naturally when conversations touch
US history, civil rights, systemic inequality, or current policy.

Run once (or re-run to refresh):
    python3 load_nia_knowledge.py

Uses mem.add("knowledge", text) — writes to L2/L3 RAG only, NOT to
amy_self.json, so the context window isn't bloated every turn.
"""

import json
import os
import re
import sys
import textwrap

HERE = os.path.dirname(__file__)
NIA_DIR = os.path.expanduser("~/nia-ai")

sys.path.insert(0, HERE)
from memory import Memory

MAX_CHUNK_WORDS = 280    # keep chunks within nomic-embed-text's sweet spot
OVERLAP_LINES   = 2     # context lines carried across chunk boundaries


# ── Chunking helpers ───────────────────────────────────────────────────────

def chunk_markdown(text: str, label: str) -> list[str]:
    """Split a markdown document on ## / ### headings, then by word count."""
    # Drop boilerplate review-status lines
    text = re.sub(r"\*\*Review Status:.*?\n", "", text)
    # Split on ## headings (keep heading in chunk)
    sections = re.split(r"(?m)^(#{1,3} .+)", text)
    raw_chunks: list[str] = []
    i = 0
    while i < len(sections):
        part = sections[i].strip()
        if not part:
            i += 1
            continue
        if re.match(r"^#{1,3} ", part) and i + 1 < len(sections):
            part = part + "\n" + sections[i + 1].strip()
            i += 2
        else:
            i += 1
        if part:
            raw_chunks.append(part)

    # Further split any chunk that's too long
    result = []
    for chunk in raw_chunks:
        words = chunk.split()
        if len(words) <= MAX_CHUNK_WORDS:
            result.append(f"[{label}]\n{chunk}")
        else:
            # Split by paragraphs first
            paras = [p.strip() for p in chunk.split("\n\n") if p.strip()]
            current: list[str] = []
            current_w = 0
            for para in paras:
                pw = len(para.split())
                if current_w + pw > MAX_CHUNK_WORDS and current:
                    result.append(f"[{label}]\n" + "\n\n".join(current))
                    current = []
                    current_w = 0
                current.append(para)
                current_w += pw
            if current:
                result.append(f"[{label}]\n" + "\n\n".join(current))
    return [c for c in result if len(c.split()) > 10]


def chunk_json_resources(path: str, label: str) -> list[str]:
    """Turn a national resources JSON into individual org chunks."""
    with open(path) as f:
        data = json.load(f)
    chunks = []
    for org in data.get("organizations", []):
        name    = org.get("name", "")
        domains = ", ".join(org.get("domains", []))
        phone   = org.get("phone") or ""
        url     = org.get("website") or ""
        elig    = org.get("eligibility", "")
        what    = org.get("what_they_help_with", "")
        chunk = (
            f"[{label}]\n"
            f"Resource: {name}\n"
            f"Domains: {domains}\n"
            f"Phone: {phone}   Website: {url}\n"
            f"Who can use it: {elig}\n"
            f"What they help with: {what}"
        )
        chunks.append(chunk)
    return chunks


# ── Knowledge sources ──────────────────────────────────────────────────────

def gather_chunks() -> list[str]:
    chunks: list[str] = []

    # 1. Mission Charter — overall context on US inequity
    path = os.path.join(NIA_DIR, "MISSION_CHARTER.md")
    if os.path.exists(path):
        chunks += chunk_markdown(open(path).read(), "Nia: US Systemic Context")

    # 2. Nia's soul / operating philosophy on helping people
    path = os.path.join(NIA_DIR, "agents/nia/SOUL.md")
    if os.path.exists(path):
        chunks += chunk_markdown(open(path).read(), "Nia: Helping Philosophy")

    # 3. Civil rights law guides
    for fname, label in [
        ("title_vii.md",          "Nia: Title VII — Workplace Rights"),
        ("fair_housing_act.md",   "Nia: Fair Housing Act"),
        ("maternal_health.md",    "Nia: Maternal Health Rights"),
    ]:
        path = os.path.join(NIA_DIR, "knowledge/rights/federal", fname)
        if os.path.exists(path):
            chunks += chunk_markdown(open(path).read(), label)

    # 4. Active policy threats (current as of July 2026)
    path = os.path.join(NIA_DIR, "knowledge/policy_tracker/active_threats.md")
    if os.path.exists(path):
        chunks += chunk_markdown(open(path).read(), "Nia: Active Policy Threats (2026)")

    # 5. Deep policy matrix — change → vulnerability → navigation by domain
    path = os.path.join(NIA_DIR, "knowledge/policy_tracker/project2025_policy_matrix.md")
    if os.path.exists(path):
        chunks += chunk_markdown(open(path).read(), "Nia: Policy Matrix — Navigation Guide")

    # 6. Institutional map — who controls resources
    path = os.path.join(NIA_DIR, "knowledge/policy_tracker/project2025_institutional_map.md")
    if os.path.exists(path):
        chunks += chunk_markdown(open(path).read(), "Nia: Institutional Power Map")

    # 7. National resource organizations
    for fname, label in [
        ("economic.json",    "Nia: National Economic Resources"),
        ("healthcare.json",  "Nia: National Healthcare Resources"),
        ("housing.json",     "Nia: National Housing Resources"),
        ("education.json",   "Nia: National Education Resources"),
        ("employment.json",  "Nia: National Employment Resources"),
        ("legal_aid.json",   "Nia: National Legal Aid Resources"),
        ("immigration.json", "Nia: National Immigration Resources"),
    ]:
        path = os.path.join(NIA_DIR, "knowledge/resources/national", fname)
        if os.path.exists(path):
            chunks += chunk_json_resources(path, label)

    return chunks


# ── Deduplicate against existing knowledge ─────────────────────────────────

def already_loaded(mem: Memory, chunk: str) -> bool:
    """Check if this exact chunk is already in the DB (first 150 chars as key)."""
    key = chunk[:150]
    rows = mem.db.execute(
        "SELECT 1 FROM mem WHERE role='knowledge' AND text LIKE ? LIMIT 1",
        (f"{key}%",)
    ).fetchone()
    return rows is not None


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    print("Loading Nia knowledge into Amy's memory...\n")
    mem = Memory()

    chunks = gather_chunks()
    print(f"Found {len(chunks)} knowledge chunks to load.\n")

    loaded = 0
    skipped = 0
    for i, chunk in enumerate(chunks, 1):
        first_line = chunk.split("\n")[0]
        if already_loaded(mem, chunk):
            skipped += 1
            continue
        try:
            mem.add("knowledge", chunk)
            loaded += 1
            label = first_line[:60]
            print(f"  [{i:03d}] ✓ {label}")
        except Exception as e:
            print(f"  [{i:03d}] ✗ failed: {e}")

    print(f"\nDone. Loaded {loaded} chunks, skipped {skipped} already present.")
    print("Amy will now surface this knowledge through RAG when relevant topics come up.")


if __name__ == "__main__":
    main()
