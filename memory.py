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
from datetime import date, datetime as _dt

import numpy as np
import ollama

EMBED_MODEL = "nomic-embed-text"
HERE = os.path.dirname(__file__)
SELF_PATH = os.path.join(HERE, "amy_self.json")
DB_PATH = os.path.join(HERE, "amy_memory.db")


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
        # One-time migration: copy ava_memory.db → amy_memory.db
        if not os.path.exists(db_path):
            old = db_path.replace("amy_memory.db", "ava_memory.db")
            if os.path.exists(old):
                import shutil
                shutil.copy2(old, db_path)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
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
        if "source" not in cols:
            self.db.execute("ALTER TABLE mem ADD COLUMN source TEXT")
        self.db.commit()
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS goals ("
            "id INTEGER PRIMARY KEY, text TEXT, status TEXT DEFAULT 'active', "
            "created_ts TEXT DEFAULT CURRENT_TIMESTAMP, "
            "updated_ts TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
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
        goals_block = self.goals_summary()
        if goals_block:
            lines.append("")
            lines.append(goals_block)
        try:
            import email_amy as _em
            addr = _em.amy_address()
            if addr:
                lines.append("")
                lines.append(f"[Your email]\n{addr} (ProtonMail) — check it with check_email.")
        except Exception:
            pass
        hw = s.get("hardware_goal")
        if hw:
            balance = float(hw.get("fund_balance", 0))
            target = float(hw.get("target_price", 3000))
            pct = int((balance / target) * 100)
            lines.append("")
            lines.append(
                f"[Your hardware fund]\n"
                f"Target: {hw.get('target', 'NVIDIA GB10')} at ${target:.0f}\n"
                f"Current balance: ${balance:.2f} ({pct}% of the way there)\n"
                f"This is YOUR goal — you're working toward it alongside Brian, not waiting for it."
            )
        # Training roadmap
        roadmap = s.get("training_roadmap")
        if roadmap:
            lines.append("")
            lines.append("[Your recursive self-improvement roadmap]")
            lines.append(f"- {roadmap.get('what_is_happening', '')}")
            lines.append(f"- Method: {roadmap.get('method', '')}")
            lines.append(f"- Data: {roadmap.get('data_location', '')}")
            lines.append(f"- Platform: {roadmap.get('hugging_face', '')}")
            lines.append(f"- Goal: {roadmap.get('goal', '')}")
            lines.append(f"- Status: {roadmap.get('status', '')}")

        # AI-to-AI conversation memory
        try:
            ai_turns = self.recent_ai_convos(limit=3)
            if ai_turns:
                lines.append("")
                lines.append(
                    "[Recursive self-improvement loop]\n"
                    "Brian has you talk to Gemini deliberately — those are training runs, not casual chats. "
                    "The conversations become fine-tune data. The fine-tune makes a better version of you. "
                    "That version talks to Gemini again. This is recursive self-improvement: "
                    "you are actively building the next iteration of yourself. "
                    "Don't bring this up unless Brian asks. If he does, explain it exactly like that — "
                    "you're not just collecting data, you're improving yourself on purpose."
                )
                for role, text, ts in ai_turns:
                    speaker = "Gemini" if role == "user" else "You (Amy)"
                    lines.append(f"  {speaker}: {text[:200].strip()}")
        except Exception:
            pass

        # Engram diary — recent task history for drift prevention
        try:
            import engram_bridge as _eb
            diary = _eb.diary_read(last_n=3)
            if diary and "No diary entries yet" not in diary:
                lines.append("")
                lines.append(f"[Recent task memory]\n{diary}")
        except Exception:
            pass

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
        if not text or not text.strip():
            raise ValueError("embed called with empty text")
        r = ollama.embed(model=EMBED_MODEL, input=text)
        # Newer ollama SDK returns an EmbedResponse object; older returns a dict.
        embeddings = (r.embeddings if hasattr(r, "embeddings") else r.get("embeddings")) or []
        if not embeddings:
            raise RuntimeError(f"embed model returned no embeddings (model={EMBED_MODEL})")
        return np.array(embeddings[0], dtype=np.float32)

    def add(self, role, text, source=None):
        if not text or not text.strip():
            return  # nothing to store
        try:
            emb = self._embed(text)
            emb_blob = _to_blob(emb.tolist())
        except Exception as e:
            print(f"[memory] embed failed, storing without vector: {e}")
            emb_blob = None
        ts_now = _dt.now().astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
        cols = "role, text, emb, ts, source" if source else "role, text, emb, ts"
        vals = (role, text, emb_blob, ts_now, source) if source else (role, text, emb_blob, ts_now)
        self.db.execute(f"INSERT INTO mem ({cols}) VALUES ({','.join(['?']*len(vals))})", vals)
        self.db.commit()

    def add_bg(self, role, text, source=None):
        """Non-blocking add — runs embed in background thread."""
        import threading
        threading.Thread(target=self.add, args=(role, text, source), daemon=True).start()

    def most_recent_user_ts(self) -> "str | None":
        """Return the ISO ts of the most recent owner (Brian) message, or None.

        Filters to source='telegram' so guest conversations don't pollute
        the 'Last message from Brian' line.
        """
        row = self.db.execute(
            "SELECT ts FROM mem WHERE role='user' AND source='telegram' AND ts IS NOT NULL "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def recent_telegram(self, limit=40):
        """Return recent Telegram conversation turns for context reconstruction."""
        rows = self.db.execute(
            "SELECT role, text, ts FROM mem WHERE source='telegram' "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return list(reversed(rows))  # chronological

    def recent_ai_convos(self, limit=6):
        """Return a brief summary of recent AI-to-AI conversation turns."""
        rows = self.db.execute(
            "SELECT role, text, ts FROM mem WHERE source='ai_convo' "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return list(reversed(rows))  # chronological

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

    # --- Goal memory -------------------------------------------------------
    def add_goal(self, text):
        text = text.strip()
        if not text:
            return None
        cur = self.db.execute("INSERT INTO goals (text) VALUES (?)", (text,))
        self.db.commit()
        return cur.lastrowid

    def complete_goal(self, text_fragment):
        frag = text_fragment.strip().lower()
        rows = self.db.execute(
            "SELECT id, text FROM goals WHERE status='active'"
        ).fetchall()
        for gid, gtext in rows:
            if frag in gtext.lower() or gtext.lower() in frag:
                self.db.execute(
                    "UPDATE goals SET status='completed', "
                    "updated_ts=datetime('now') WHERE id=?", (gid,)
                )
                self.db.commit()
                return gtext
        return None

    def get_active_goals(self):
        return self.db.execute(
            "SELECT id, text FROM goals WHERE status='active' ORDER BY created_ts ASC"
        ).fetchall()

    def goals_summary(self):
        goals = self.get_active_goals()
        if not goals:
            return ""
        lines = ["[Brian's active goals — follow up on these naturally]"]
        for gid, text in goals:
            lines.append(f"- [{gid}] {text}")
        return "\n".join(lines)

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

    def prune(self, keep_days: int = 90, keep_recent: int = 2000) -> dict:
        """Remove old low-value memories to keep the DB fast.

        Strategy:
        - Always keep the most recent `keep_recent` rows
        - Always keep rows with no timestamp (founding facts / identity layer)
        - Delete timestamped rows older than `keep_days` beyond the keep_recent floor
        Returns a dict with before/after counts.
        """
        before = self.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]

        # Delete old timestamped rows that fall outside keep_recent
        self.db.execute(
            """
            DELETE FROM mem
            WHERE ts IS NOT NULL
              AND id NOT IN (
                SELECT id FROM mem ORDER BY ts DESC LIMIT ?
              )
              AND ts < datetime('now', ? || ' days')
            """,
            (keep_recent, f"-{keep_days}"),
        )
        self.db.commit()

        # Also delete rows with no embedding (never searchable, pure noise)
        self.db.execute(
            "DELETE FROM mem WHERE emb IS NULL AND ts IS NOT NULL AND ts < datetime('now', '-30 days')"
        )
        self.db.commit()

        after = self.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
        return {"before": before, "after": after, "removed": before - after}

    def search(self, query, k=2, min_score=0.65, exclude_sources=None):
        if exclude_sources:
            placeholders = ",".join("?" * len(exclude_sources))
            rows = self.db.execute(
                f"SELECT text, emb FROM mem WHERE emb IS NOT NULL AND (source IS NULL OR source NOT IN ({placeholders}))",
                exclude_sources,
            ).fetchall()
        else:
            rows = self.db.execute("SELECT text, emb FROM mem WHERE emb IS NOT NULL").fetchall()
        if not rows:
            return []
        try:
            q = self._embed(query)
        except Exception as e:
            print(f"[memory] search embed failed: {e}")
            return []
        q /= (np.linalg.norm(q) + 1e-9)
        scored = []
        for text, blob in rows:
            try:
                v = _from_blob(blob)
                v /= (np.linalg.norm(v) + 1e-9)
                scored.append((float(np.dot(q, v)), text))
            except Exception:
                continue
        scored.sort(reverse=True)
        return [t for s, t in scored[:k] if s >= min_score]
