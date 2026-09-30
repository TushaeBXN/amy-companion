"""Task queue — anti-drift working memory for Amy and sibling models.

Keeps a persistent, ordered list of tasks so the model (and the human)
always know what the current focus is. The active task is pinned into the
system prompt so context bleed from prior turns can't bury it.

Design goals
------------
- Simple: one JSON file, no external dependencies.
- Thread-safe: a single lock guards every read/write.
- Secure: file path is hardcoded relative to this module (no user-supplied
  paths). Input is stripped and length-capped before storage.
- Transparent: the context_snippet() method returns a compact, human-readable
  block ready to append to any system prompt.

Usage (standalone)
------------------
    from task_queue import TaskQueue
    q = TaskQueue()
    q.push("Refactor companion.py", "Clean up build_context, add task injection")
    q.push("Run tests", "pytest, then push to GitHub")
    print(q.context_snippet())
    q.complete()          # marks the first task done, activates next
    print(q.context_snippet())
"""

import json
import os
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_PATH = os.path.join(HERE, "task_queue.json")

# Input limits — prevent prompt injection via oversized task text
_MAX_TITLE  = 200
_MAX_DETAIL = 1000
_MAX_TASKS  = 100   # hard cap; oldest done/skipped tasks are pruned first


class TaskQueue:
    """Persistent, thread-safe task queue with system-prompt injection."""

    def __init__(self, path: str = _DEFAULT_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._tasks: list[dict] = []
        self._load()

    # ── public API ────────────────────────────────────────────────────────────

    def push(self, title: str, details: str = "") -> str:
        """Add a new task. Returns its id. Thread-safe."""
        title   = str(title).strip()[:_MAX_TITLE]
        details = str(details).strip()[:_MAX_DETAIL]
        if not title:
            raise ValueError("Task title cannot be empty")
        task = {
            "id":           str(uuid.uuid4())[:8],
            "title":        title,
            "details":      details,
            "status":       "pending",
            "created_at":   time.time(),
            "completed_at": None,
        }
        with self._lock:
            self._prune()
            self._tasks.append(task)
            self._save()
        return task["id"]

    def current(self) -> dict | None:
        """Return the first pending task (the active one), or None."""
        with self._lock:
            for t in self._tasks:
                if t["status"] == "pending":
                    return dict(t)
        return None

    def complete(self, task_id: str | None = None) -> bool:
        """Mark a task done. If task_id is None, completes the current task.
        Returns True if a task was found and marked."""
        with self._lock:
            task = self._find(task_id)
            if task is None:
                return False
            task["status"] = "done"
            task["completed_at"] = time.time()
            self._save()
        return True

    def skip(self, task_id: str | None = None) -> bool:
        """Skip a task (move it out of the active slot without marking done).
        If task_id is None, skips the current task. Returns True if found."""
        with self._lock:
            task = self._find(task_id)
            if task is None:
                return False
            task["status"] = "skipped"
            task["completed_at"] = time.time()
            self._save()
        return True

    def list_pending(self) -> list[dict]:
        """Return all pending tasks in order, as copies."""
        with self._lock:
            return [dict(t) for t in self._tasks if t["status"] == "pending"]

    def list_all(self) -> list[dict]:
        """Return all tasks (all statuses) in order, as copies."""
        with self._lock:
            return [dict(t) for t in self._tasks]

    def clear_done(self) -> int:
        """Remove all done/skipped tasks. Returns count removed."""
        with self._lock:
            before = len(self._tasks)
            self._tasks = [t for t in self._tasks if t["status"] == "pending"]
            removed = before - len(self._tasks)
            if removed:
                self._save()
        return removed

    def context_snippet(self) -> str:
        """Compact string to inject into a system prompt.

        Returns an empty string when the queue is empty so callers can use
        a simple ``if snippet: system += snippet`` pattern.
        """
        pending = self.list_pending()
        if not pending:
            return ""
        lines = ["[Task queue — current focus first]"]
        for i, t in enumerate(pending[:10]):   # show at most 10
            marker = "▶" if i == 0 else f"{i+1}."
            line = f"  {marker} [{t['id']}] {t['title']}"
            if i == 0 and t["details"]:
                line += f"\n     {t['details']}"
            lines.append(line)
        if len(pending) > 10:
            lines.append(f"  … and {len(pending) - 10} more.")
        lines.append(
            "Work the current task (▶) to completion before moving on. "
            "Call task_complete when done."
        )
        return "\n".join(lines)

    # ── internal helpers ──────────────────────────────────────────────────────

    def _find(self, task_id: str | None) -> dict | None:
        """Return the mutable task dict for task_id, or the first pending task
        when task_id is None. Caller must hold self._lock."""
        if task_id is None:
            for t in self._tasks:
                if t["status"] == "pending":
                    return t
            return None
        for t in self._tasks:
            if t["id"] == task_id:
                return t
        return None

    def _prune(self) -> None:
        """Drop oldest done/skipped entries to stay under _MAX_TASKS.
        Caller must hold self._lock."""
        if len(self._tasks) < _MAX_TASKS:
            return
        finished = [t for t in self._tasks if t["status"] in ("done", "skipped")]
        to_drop  = len(self._tasks) - _MAX_TASKS + 1
        drop_ids = {t["id"] for t in finished[:to_drop]}
        self._tasks = [t for t in self._tasks if t["id"] not in drop_ids]

    def _load(self) -> None:
        """Load tasks from disk. Silently resets to empty on corruption."""
        try:
            with open(self._path) as f:
                raw = json.load(f)
            # Validate schema minimally
            if not isinstance(raw, list):
                return
            tasks = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                if not isinstance(item.get("id"), str):
                    continue
                if not isinstance(item.get("title"), str):
                    continue
                if item.get("status") not in ("pending", "done", "skipped"):
                    continue
                tasks.append({
                    "id":           str(item["id"])[:8],
                    "title":        str(item.get("title", ""))[:_MAX_TITLE],
                    "details":      str(item.get("details", ""))[:_MAX_DETAIL],
                    "status":       item["status"],
                    "created_at":   float(item.get("created_at", 0)),
                    "completed_at": (float(item["completed_at"])
                                     if item.get("completed_at") else None),
                })
            self._tasks = tasks
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            self._tasks = []

    def _save(self) -> None:
        """Persist tasks to disk atomically. Caller must hold self._lock."""
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(self._tasks, f, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass


# Module-level singleton so all importers share one queue per process.
_queue: TaskQueue | None = None

def get_queue(path: str = _DEFAULT_PATH) -> TaskQueue:
    """Return the process-level TaskQueue singleton."""
    global _queue
    if _queue is None:
        _queue = TaskQueue(path)
    return _queue
