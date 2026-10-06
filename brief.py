"""Daily brief — gathers the day's facts first, then Amy writes them up.

Fetching is done in code (not left to tool calls) so the brief always has real,
current data. The model only summarizes what was actually found.
"""
from datetime import datetime

import ollama
import requests

import web

WEATHER_CITY = "Winston-Salem+NC"

SECTIONS = [
    ("United States",           "United States top news"),
    ("Burkina Faso",            "Burkina Faso news"),
    ("Department of Education", "US Department of Education announcement"),
    ("Arts",                    "arts culture news"),
    ("Literature",              "books literature news"),
    ("Technology",              "technology news"),
]


def _news(query, n=4):
    """Recent headlines. DuckDuckGo news first (has dates), plain search as fallback."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    try:
        items = list(DDGS().news(query, timelimit="d", max_results=n)) or \
                list(DDGS().news(query, timelimit="w", max_results=n))
        return [f"- {r.get('title','')} ({r.get('source','')}): {r.get('body','')[:220]}"
                for r in items]
    except Exception:
        try:
            return [f"- {r['title']}: {r['snippet'][:220]}" for r in web.search(query, n)]
        except Exception as e:
            return [f"(search failed: {e})"]


def gather():
    today = datetime.now().strftime("%A, %B %d, %Y")
    parts = [f"[BRIEF DATA — {today}]"]
    try:
        w = requests.get(f"https://wttr.in/{WEATHER_CITY}?format=%l:+%c+%t+(feels+%f),+%w,+rain+%p",
                         timeout=10).text.strip()
    except Exception as e:
        w = f"(unavailable: {e})"
    parts.append(f"\n[WEATHER]\n{w}")
    for label, query in SECTIONS:
        lines = _news(query) or ["(nothing found)"]
        parts.append(f"\n[{label.upper()}]\n" + "\n".join(lines))
    return "\n".join(parts)


def write(model, name, system_prompt=""):
    """Return the finished brief text for `name`."""
    data = gather()
    if name == "Brian":
        instructions = (
            "Write Brian's morning brief from ONLY the data below — no outside facts, "
            "and never invent a headline. "
            "Format: plain text for Telegram. Each section is a bold header followed by "
            "1-2 bullet points (use • not -) with the top story only — one line per bullet, "
            "tight and scannable. Sections in order: Weather, United States, Burkina Faso, "
            "Department of Education, Arts, Literature, Technology. "
            "Translate every French headline into English. "
            "If a section has nothing useful, skip it entirely. "
            "Open with one warm line to Brian. No closing line."
        )
    else:
        instructions = (
            f"Write {name}'s morning brief from ONLY the data below — no outside facts, "
            "and never invent a headline. Sections in this order: Weather, United States, "
            "Burkina Faso, Department of Education, Arts, Literature, Technology. "
            "2-3 short bullets per section; translate every French headline into English (never leave French text). "
            "Burkina Faso ALWAYS gets its own section — if there is no new news, write '• Quiet today.' under it. "
            "When a story connects across sections (e.g. a protest in Burkina tied to a broader regional trend), "
            "note the connection in one phrase so the reader sees why it matters. "
            "Only include items that truly fit their section; if any other section has nothing useful, write '• Quiet today.' under it. "
            f"Open with one warm line to {name}; end with one thing worth paying attention to today. "
            "Plain text for Telegram, readable in about 90 seconds."
        )
    msgs = [{"role": "system", "content": (system_prompt + "\n\n" if system_prompt else "") + instructions},
            {"role": "user", "content": data}]
    kw = dict(model=model, messages=msgs, keep_alive="30m",
              options={"num_ctx": 32768, "temperature": 0.4})
    try:
        from worker import ollama_chat
        r = ollama_chat(think=False, **kw)
    except TypeError:
        from worker import ollama_chat
        r = ollama_chat(**kw)
    return (r["message"].get("content") or "").strip()
