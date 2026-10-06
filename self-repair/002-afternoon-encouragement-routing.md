# Fix 002 — Afternoon Encouragement Routing (wrong recipient)

**Date:** 2026-09-17 (early in the project)  
**Symptom:** Both Brian and Raketa were seeing each other's afternoon encouragement messages. Brian saw messages meant for Raketa and vice versa.  
**Affected tasks:** `afternoon_encouragement`, `afternoon_encouragement_raketa`

---

## What broke

The system prompt for all tasks ended with a hardcoded line: "reply with a note to Brian." When Raketa's encouragement task ran, the model followed that instruction and addressed the message to Brian — even though the task was meant for Raketa. Both tasks were essentially sending to Brian.

---

## Root cause

The `closing` instruction in `worker.py` was static — it always said "reply with a note to Brian" regardless of who the task was addressed to. The `task.get("to")` field existed but wasn't being used to customize the closing.

---

## The fix (step by step)

**File changed:** `worker.py`, in `_run_task()` where the system prompt is assembled

**Before:**
```python
closing = ("When finished, reply with a short, natural note to Brian: "
           "what you did and where you saved it.")
```

**After:**
```python
recipient = task.get("to")
if recipient:
    closing = (f"When finished, just return your message for {recipient} "
               "— plain text only, no explanation, no preamble, nothing else.")
else:
    closing = ("When finished, reply with a short, natural note to Brian: "
               "what you did and where you saved it.")
```

**How it works:**
1. Check if the task has a `"to"` field (e.g. `"to": "Raketa"`)
2. If yes, tell the model explicitly who the message is for and to return only the message text
3. If no `"to"` field, default to Brian as before

---

## What to watch for next time

- Any task with a `"to"` field routes to that recipient via `_recipient()` in worker.py
- The closing instruction must match the routing — if they're mismatched the message goes to the wrong person
- When adding new recipient-targeted tasks, always include the `"to"` field in tasks.json

---

## Pattern: Static prompts that ignore task context

**Generalizable lesson:** System prompt assembly must use task metadata. A static closing line that ignores `task.get("to")` will always default to Brian. Always check what task fields exist and incorporate them into the prompt.
