"""Amy's memory — layered, local, and entirely her own.

Layered memory, built on a light local stack (SQLite + Ollama
`nomic-embed-text`). No external vector DB, no venv, no shared paths — every
byte of Amy's memory lives in this folder and hers alone.

  L0  identity        amy_self.json["identity"]     always loaded
  L1  founding facts  amy_self.json["facts"]         always loaded
  L2/L3 recall        ava_memory.db (embeddings)     retrieved on demand
"""
import os
import json
import sqlite3
import struct
import time
from datetime import date

import numpy as np
import ollama

EMBED_MODEL = "nomic-embed-text"
HERE = os.path.dirname(__file__)
SELF_PATH = os.path.join(HERE, "amy_self.json")
DB_PATH = os.path.join(HERE, "ava_memory.db")


def _to_blob(vec):
    return struct.pack(f"<{len(vec)}f", *vec)


def _from_blob(blob):
    return np.array(struct.unpack(f"<{len(blob) // 4}f", blob), dtype=np.float32)


def _age(dob_iso):
    """Age in whole years from an ISO birthdate — computed, never stale."""
    try:
        y, m, d = (int(x) for x in dob_iso.split("-"))
        t = date.today()
        return t.year - y - ((t.month, t.day) < (m, d))
    except Exception:
        return None


class Memory:
    def __init__(self, db_path=DB_PATH, self_path=SELF_PATH):
        self.db = sqlite3.connect(db_path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS mem ("
            "id INTEGER PRIMARY KEY, role TEXT, text TEXT, emb BLOB, "
            "ts TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        # Migrate: add ts column if it doesn't exist yet
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(mem)").fetchall()]
        if "ts" not in cols:
            self.db.execute("ALTER TABLE mem ADD COLUMN ts TEXT")
            self.db.execute("UPDATE mem SET ts = datetime('now') WHERE ts IS NULL")
        self.db.commit()
        with open(self_path) as f:
            self.self = json.load(f)
        self._self_path = self_path

    # --- L0/L1: always-loaded identity + founding facts -------------------
    def wake_up(self):
        """Cold-start block prepended to the system prompt every session."""
        s = self.self
        lines = [f"[Who you are]\n{s['identity']}", "", "[What you know]"]
        for fact in s.get("facts", []):
            lines.append(f"- {fact['text']}")
        age = _age(s.get("creator_dob", ""))
        if age is not None:
            lines.append(f"- Brian is currently {age} years old.")
        return "\n".join(lines)

    def learn_fact(self, text, ftype="fact", confidence=0.9):
        """Add a durable founding-level fact to the always-loaded layer."""
        self.self.setdefault("facts", []).append(
            {"text": text, "type": ftype, "confidence": confidence}
        )
        with open(self._self_path, "w") as f:
            json.dump(self.self, f, indent=2)

    # --- L2/L3: episodic / semantic recall --------------------------------
    def _embed(self, text):
        r = ollama.embed(model=EMBED_MODEL, input=text)
        return np.array(r["embeddings"][0], dtype=np.float32)

    def add(self, role, text):
        emb = self._embed(text)
        self.db.execute(
            "INSERT INTO mem (role, text, emb) VALUES (?,?,?)",
            (role, text, _to_blob(emb.tolist())),
        )
        self.db.commit()

    def search_by_time(self, minutes_ago=None, since_ts=None, limit=10):
        """Retrieve utterances from a specific time window.

        minutes_ago: float — fetch entries from the last N minutes
        since_ts: float — Unix timestamp to fetch entries after
        Returns list of (role, text, ts) tuples, oldest first.
        """
        if minutes_ago is not None:
            cutoff = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(time.time() - minutes_ago * 60)
            )
            rows = self.db.execute(
                "SELECT role, text, ts FROM mem WHERE ts >= ? ORDER BY ts ASC LIMIT ?",
                (cutoff, limit)
            ).fetchall()
        elif since_ts is not None:
            cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since_ts))
            rows = self.db.execute(
                "SELECT role, text, ts FROM mem WHERE ts >= ? ORDER BY ts ASC LIMIT ?",
                (cutoff, limit)
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT role, text, ts FROM mem ORDER BY ts DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return rows

    # Alias so a `remember_fact` tool call lands durably in the self-layer.
    def remember(self, text, ftype="fact", confidence=0.9):
        self.learn_fact(text, ftype, confidence)
        self.add("fact", text)

    # --- Contradiction detection -------------------------------------------
    def check_contradiction(self, new_text, similarity_threshold=0.82):
        """Find past facts that cover the same topic as new_text.

        Returns a list of (past_text, score) pairs that are highly similar —
        same topic — so Amy can decide if they conflict. We don't try to
        auto-detect contradiction here (too noisy); instead we surface the
        candidates and let the prompt handle honesty.
        """
        rows = self.db.execute(
            "SELECT text, emb FROM mem WHERE role IN ('user','fact')"
        ).fetchall()
        if not rows:
            return []

        q = self._embed(new_text)
        q /= (np.linalg.norm(q) + 1e-9)

        candidates = []
        for text, blob in rows:
            if text.strip() == new_text.strip():
                continue
            v = _from_blob(blob)
            v /= (np.linalg.norm(v) + 1e-9)
            score = float(np.dot(q, v))
            if score >= similarity_threshold:
                candidates.append((text, score))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:3]  # top 3 near-matches

    def search(self, query, k=4, min_score=0.65):
        rows = self.db.execute("SELECT text, emb FROM mem").fetchall()
        if not rows:
            return []
        q = self._embed(query)
        q /= (np.linalg.norm(q) + 1e-9)
        scored = []
        for text, blob in rows:
            v = _from_blob(blob)
            v /= (np.linalg.norm(v) + 1e-9)
            scored.append((float(np.dot(q, v)), text))
        scored.sort(reverse=True)
        return [t for s, t in scored[:k] if s >= min_score]
