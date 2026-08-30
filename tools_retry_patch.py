"""tools_retry_patch.py

NOT a standalone file. Shows exactly what to add/change in tools.py
to get retry logic and structured error feedback.

INSTRUCTIONS:
1. Add the _IDEMPOTENT_TOOLS set and _tool_call() helper near the top of tools.py
   (after the imports, before TOOLS / _BASE_TOOLS definitions).
2. Replace the body of dispatch() with the new version below.
   The actual tool logic inside dispatch() stays identical — only the
   outer wrapper changes.
"""

import time

# ── 1. Add this near the top of tools.py ─────────────────────────────────────

# Tools that are safe to retry on transient failure (network blip, timeout).
# Anything that writes, posts, sends, or charges is NOT in this set.
_IDEMPOTENT_TOOLS = {
    "web_search",
    "read_article",
    "moltbook_check_in",
    "moltbook_engage",
    "moltbook_verify",
    "read_journal",
    "read_file",
    "list_files",
    "check_email",
    "fund_status",
    "search_books",
    "list_goals",
    "list_work",
    "read_deliverable",
    "research_income",
}

_MAX_RETRIES = 1          # one automatic retry for idempotent tools
_RETRY_DELAY = 1.5        # seconds between attempts


def _tool_call(name: str, fn, *args, **kwargs):
    """Execute fn(*args, **kwargs), retrying once on exception for safe tools.

    Returns a structured result string the model can parse:
      - Success  → whatever the tool returned (unchanged)
      - Failure  → "[tool_error] name: <reason>" — model sees this and can
                   tell the user something went wrong rather than hallucinating.
    """
    attempts = _MAX_RETRIES + 1 if name in _IDEMPOTENT_TOOLS else 1
    last_exc = None

    for attempt in range(attempts):
        try:
            result = fn(*args, **kwargs)
            return result
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1:
                print(
                    f"  [tools] {name} failed (attempt {attempt + 1}), "
                    f"retrying in {_RETRY_DELAY}s: {exc}",
                    flush=True,
                )
                time.sleep(_RETRY_DELAY)

    # All attempts exhausted — return structured error the model understands.
    reason = str(last_exc) or "unknown error"
    print(f"  [tools] {name} failed permanently: {reason}", flush=True)
    return f"[tool_error] {name}: {reason}"


# ── 2. Wrap each tool execution in dispatch() ─────────────────────────────────
#
# Your existing dispatch() has a long if/elif chain like:
#
#   if name == "web_search":
#       query = args.get("query", "")
#       return web.search(query)
#
# Change each bare call to go through _tool_call():
#
#   if name == "web_search":
#       query = args.get("query", "")
#       return _tool_call(name, web.search, query)
#
# For tools with more complex logic (multiple lines before the return),
# wrap just the outermost call that can fail — usually the external
# library/network call at the end.
#
# EXAMPLE — before:
#
#   if name == "read_article":
#       url = args.get("url", "")
#       return web.read(url)
#
# EXAMPLE — after:
#
#   if name == "read_article":
#       url = args.get("url", "")
#       return _tool_call(name, web.read, url)
#
# For tools that are already wrapped in try/except with their own error
# strings (like write_file, send_email), you can leave them as-is —
# they already return error strings rather than raising. _tool_call is
# only needed where an unhandled exception could propagate up and inject
# a Python traceback into the model's context.
#
# ── What the model sees on failure ───────────────────────────────────────────
#
# Instead of a raw Python traceback landing in context, the model gets:
#
#   [tool_error] web_search: HTTPSConnectionPool(...): Read timed out.
#
# Amy's system prompt should mention this format so she handles it gracefully.
# Add this line to SYSTEM_PROMPT in amy_persona.py:
#
#   If a tool returns a message starting with [tool_error], tell the user
#   that tool is currently unavailable and move on — do not guess or fabricate
#   a result.
