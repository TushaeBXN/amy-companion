# Fix 001 — Truncated Tool Call JSON (write_file fails on long content)

**Date:** 2026-10-06  
**Symptom:** Task fails with `(task failed: llama-server returned invalid tool call arguments for "write_file": unexpected end of JSON input (status code: 500))`  
**Affected tasks:** Any task that writes long content in a single write_file call — reel_scripts, reports, research files, deliverables.

---

## What broke

When the model generates a tool call with very long arguments (e.g. writing three full Reel scripts at once), the JSON for those arguments gets so large that Ollama truncates it mid-generation. The response comes back malformed — the JSON is cut off before the closing braces.

The old worker code caught the `json.JSONDecodeError` but just silently set `args = {}` and continued. This meant:
- The tool was either called with empty args (writing nothing)
- Or the task reported a generic failure with no path to recovery
- Amy had no way to know what went wrong or how to fix it

---

## Root cause

Two things combined:
1. The model's context window is finite — large tool call arguments push past it
2. The error recovery was a silent no-op — it swallowed the error instead of feeding it back to the model

---

## The fix (step by step)

**File changed:** `worker.py`, in the tool call processing loop (~line 449)

**Before:**
```python
if isinstance(args, str):
    try:
        args = json.loads(args)
    except json.JSONDecodeError:
        args = {}
```

**After:**
```python
if isinstance(args, str):
    try:
        args = json.loads(args)
    except json.JSONDecodeError:
        # Model truncated JSON — nudge it to retry with shorter content
        messages.append({"role": "tool", "content":
            f"[ERROR] Your tool call arguments for '{name}' were cut off "
            "and could not be parsed. This usually means the content was "
            "too long. Break it into smaller pieces and try again — "
            "call write_file with shorter content per call if needed."})
        args = None
if args is None:
    used.append(name)
    continue
```

**How it works:**
1. When JSON parsing fails, instead of silently setting `args = {}`, a `tool` role message is injected into the conversation history
2. The message tells Amy exactly what went wrong and how to fix it: break the content into smaller pieces
3. `args` is set to `None` and the loop skips to the next step — no broken tool call is made
4. On the next step, the model reads the error message and retries with shorter content, splitting across multiple `write_file` calls if needed

---

## Why this works

The model is instruction-following — it will act on the error message in the conversation history just like it acts on any other feedback. It knows what `write_file` does and understands "break into smaller pieces." No special logic needed, just closing the feedback loop.

---

## What to watch for next time

- If a task repeatedly hits this error on the same content, the content itself may be too long even in smaller chunks. Consider splitting the task into two separate tasks.
- This fix works for any tool call, not just `write_file` — the `name` variable in the error message will name whichever tool truncated.
- If you see `args = {}` being passed to a tool and getting unexpected results, check whether the original call was truncated and this silent fallback is masking it.

---

## Pattern: Silent error recovery that swallows context

**Generalizable lesson:** Whenever an error is caught and silently ignored in the tool call loop, Amy loses the ability to self-correct. The fix pattern is always the same — inject the error back into the conversation as a `tool` role message so the model can read it and adapt. Never swallow errors silently in an agentic loop.
