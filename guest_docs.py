"""Private document library for a guest (e.g. Raketa's research files).

Files she sends on Telegram are split into passages, embedded locally with
nomic-embed-text, and stored in her own database under people/<name>/. On each
message, the most relevant passages are handed to Amy with their file name so she
can quote real text instead of improvising.
"""
import os, re, sqlite3, subprocess, tempfile

import numpy as np
import ollama

HERE        = os.path.dirname(os.path.abspath(__file__))
EMBED_MODEL = "nomic-embed-text"
CHUNK       = 1200      # chars per passage
OVERLAP     = 200
TOP_K       = 6         # passages per question (~1.8k tokens)

SUPPORTED = (".pdf", ".docx", ".doc", ".rtf", ".txt", ".md", ".odt")


def _db(person):
    folder = os.path.join(HERE, "people", person.lower())
    os.makedirs(folder, exist_ok=True)
    db = sqlite3.connect(os.path.join(folder, "docs.db"), check_same_thread=False)
    db.execute("CREATE TABLE IF NOT EXISTS chunks (doc TEXT, idx INTEGER, text TEXT, emb BLOB)")
    return db


def _embed(text):
    r = ollama.embed(model=EMBED_MODEL, input=text)
    embs = r.embeddings if hasattr(r, "embeddings") else r.get("embeddings")
    return np.array(embs[0], dtype=np.float32)


def extract(path):
    """Plain text from a PDF, Word, RTF or text file."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        return "\n\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    if ext in (".docx", ".doc", ".rtf", ".odt"):
        out = subprocess.run(["textutil", "-convert", "txt", "-stdout", path],
                             capture_output=True, text=True, timeout=60)
        return out.stdout
    with open(path, errors="replace") as f:
        return f.read()


def _chunks(text):
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    out, start = [], 0
    while start < len(text):
        end = min(start + CHUNK, len(text))
        if end < len(text):                      # prefer to break at a paragraph/sentence
            cut = max(text.rfind("\n\n", start + CHUNK // 2, end),
                      text.rfind(". ", start + CHUNK // 2, end))
            end = cut + 1 if cut > 0 else end
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - OVERLAP, start + 1)
    return [c for c in out if c]


def add(person, filename, path):
    """Store a document. Returns (n_passages, n_chars). Replaces a same-named file."""
    text = extract(path)
    if not text.strip():
        raise ValueError("no readable text found (is it a scanned image PDF?)")
    chunks = _chunks(text)
    db = _db(person)
    db.execute("DELETE FROM chunks WHERE doc = ?", (filename,))
    for i, c in enumerate(chunks):
        db.execute("INSERT INTO chunks VALUES (?,?,?,?)",
                   (filename, i, c, _embed(f"{filename}: {c}").tobytes()))
    db.commit()
    return len(chunks), len(text)


def search(person, query, k=TOP_K):
    db = _db(person)
    rows = db.execute("SELECT doc, idx, text, emb FROM chunks").fetchall()
    if not rows or not query.strip():
        return []
    q = _embed(query[:2000])
    q /= (np.linalg.norm(q) or 1)
    scored = []
    for doc, idx, text, emb in rows:
        v = np.frombuffer(emb, dtype=np.float32)
        scored.append((float(v @ q / (np.linalg.norm(v) or 1)), doc, idx, text))
    scored.sort(reverse=True)
    return [(doc, idx, text) for s, doc, idx, text in scored[:k] if s > 0.35]


def list_docs(person):
    return _db(person).execute(
        "SELECT doc, COUNT(*) FROM chunks GROUP BY doc ORDER BY MIN(rowid)").fetchall()


def forget(person, name):
    db = _db(person)
    docs = [d for d, _ in list_docs(person) if name.lower() in d.lower()]
    for d in docs:
        db.execute("DELETE FROM chunks WHERE doc = ?", (d,))
    db.commit()
    return docs


def context_block(person, query):
    """Passages to show Amy for this message, or '' if nothing relevant."""
    hits = search(person, query)
    if not hits:
        return ""
    parts = [f'[{doc} — passage {idx + 1}]\n{text}' for doc, idx, text in hits]
    return ("\n\n[Excerpts from her documents — the ONLY material you may quote. "
            "Cite the file name when quoting. If what she asks isn't covered here, say so.]\n\n"
            + "\n\n".join(parts))
