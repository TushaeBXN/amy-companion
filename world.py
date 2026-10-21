"""world.py — Amy's continuous world observation subsystem.

Passively watches a small set of interest areas (tech, AI, news, startups) on
a background cadence so Amy has a "current world model" to draw on in
conversation — not just memories of past interactions.

Called by worker.py every ~12 ticks (~12 min). Also runnable standalone:
    python3 world.py            # fetch all areas (force-refresh)
    python3 world.py --status   # show last-fetch times and snippet preview
    python3 world.py --area ai  # fetch one area only
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime

HERE       = os.path.dirname(os.path.abspath(__file__))
WORLD_DIR  = os.path.expanduser("~/ai-knowledge/world")
MANIFEST   = os.path.join(WORLD_DIR, ".world_manifest.json")
MAX_AGE    = 3600   # seconds — don't re-fetch an area more often than 1 hour
MAX_ITEMS  = 5      # items per area stored in the summary file
SNIPPET_N  = 3      # bullet items shown per area in context_snippet()


# ── interest areas ─────────────────────────────────────────────────────────────

def _fetch_tech() -> list[dict]:
    items: list[dict] = []
    try:
        import feeds
        raw = feeds.hacker_news(limit=10)
        items.extend(_parse_bullets(raw, limit=3))
    except Exception as e:
        print(f"  [world] tech/HN: {e}", flush=True)
    try:
        import feeds
        raw = feeds.github_trending()
        items.extend(_parse_bullets(raw, limit=2))
    except Exception as e:
        print(f"  [world] tech/GitHub: {e}", flush=True)
    return items[:MAX_ITEMS]


def _fetch_ai() -> list[dict]:
    items: list[dict] = []
    try:
        import feeds
        raw = feeds.arxiv_papers(category="ai", limit=5)
        # ArXiv format: blocks separated by blank lines, first line is "- Title"
        for block in raw.split("\n\n"):
            for line in block.splitlines():
                line = line.strip()
                if line.startswith("- "):
                    items.append({"title": line[2:].strip(), "url": ""})
                    break
            if len(items) >= 3:
                break
    except Exception as e:
        print(f"  [world] ai/arxiv: {e}", flush=True)
    try:
        import feeds
        raw = feeds.reddit_hot(subreddit="localllama", limit=6)
        items.extend(_parse_bullets(raw, limit=2))
    except Exception as e:
        print(f"  [world] ai/reddit: {e}", flush=True)
    return items[:MAX_ITEMS]


def _fetch_news() -> list[dict]:
    items: list[dict] = []
    try:
        import web
        results = web.news("technology AI current events", timelimit="w", max_results=MAX_ITEMS)
        for r in results:
            title = (r.get("title") or "").strip()
            if title:
                items.append({"title": title, "url": r.get("url", "")})
        items = items[:MAX_ITEMS]
    except Exception as e:
        print(f"  [world] news: {e}", flush=True)
    return items


def _fetch_startups() -> list[dict]:
    items: list[dict] = []
    try:
        import feeds
        raw = feeds.product_hunt_today()
        items.extend(_parse_bullets(raw, limit=3))
    except Exception as e:
        print(f"  [world] startups/PH: {e}", flush=True)
    try:
        import feeds
        raw = feeds.reddit_hot(subreddit="indiehackers", limit=5)
        items.extend(_parse_bullets(raw, limit=2))
    except Exception as e:
        print(f"  [world] startups/IH: {e}", flush=True)
    return items[:MAX_ITEMS]


AREAS: list[dict] = [
    {"name": "tech",     "label": "Tech & Software",    "fetch": _fetch_tech},
    {"name": "ai",       "label": "AI & Research",      "fetch": _fetch_ai},
    {"name": "news",     "label": "Current Events",     "fetch": _fetch_news},
    {"name": "startups", "label": "Startups & Indie",   "fetch": _fetch_startups},
]


# ── helpers ────────────────────────────────────────────────────────────────────

def _parse_bullets(raw: str, limit: int = MAX_ITEMS) -> list[dict]:
    """Parse bullet-list text from feeds.py into [{title, url}] dicts."""
    items: list[dict] = []
    for line in raw.splitlines():
        line = line.strip().lstrip("- ").strip()
        if not line:
            continue
        parts = line.rsplit("http", 1)
        title = re.sub(r"\(\d+ pts?\)", "", parts[0]).strip().rstrip(" ()")
        url = ("http" + parts[1].strip()) if len(parts) > 1 else ""
        if len(title) > 10:
            items.append({"title": title, "url": url})
        if len(items) >= limit:
            break
    return items


def _item_hash(item: dict) -> str:
    key = (item.get("title", "") + item.get("url", "")).lower().strip()
    return hashlib.md5(key.encode()).hexdigest()[:12]


# ── manifest ───────────────────────────────────────────────────────────────────

def _load_manifest() -> dict:
    try:
        with open(MANIFEST) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_manifest(m: dict) -> None:
    os.makedirs(WORLD_DIR, exist_ok=True)
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w") as f:
        json.dump(m, f, indent=2)
    os.replace(tmp, MANIFEST)


# ── file writer ────────────────────────────────────────────────────────────────

def _write_area(name: str, label: str, items: list[dict]) -> None:
    """Overwrite ~/ai-knowledge/world/{name}.md — knowledge_watcher picks it up."""
    os.makedirs(WORLD_DIR, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# {label} — {now}", ""]
    for item in items:
        title = item["title"]
        url = item.get("url", "")
        lines.append(f"- [{title}]({url})" if url else f"- {title}")
    lines.append("")
    with open(os.path.join(WORLD_DIR, f"{name}.md"), "w") as f:
        f.write("\n".join(lines))


# ── WorldObserver ──────────────────────────────────────────────────────────────

class WorldObserver:
    """Fetches world headlines and writes them into ~/ai-knowledge/world/."""

    def tick(self) -> tuple[int, int]:
        """Check each area; fetch and write those that are stale.

        Returns (fetched_count, skipped_count).
        """
        now = time.time()
        manifest = _load_manifest()
        fetched = skipped = 0

        for area in AREAS:
            name = area["name"]
            last = manifest.get(name, {}).get("last_fetch", 0)
            if now - last < MAX_AGE:
                skipped += 1
                continue

            try:
                items = area["fetch"]()
            except Exception as e:
                print(f"  [world] {name}: fetch failed: {e}", flush=True)
                continue

            if not items:
                print(f"  [world] {name}: no items returned", flush=True)
                continue

            seen = set(manifest.get(name, {}).get("seen", []))
            new_count = sum(1 for i in items if _item_hash(i) not in seen)

            _write_area(name, area["label"], items)

            manifest[name] = {
                "last_fetch": now,
                "seen": list(seen | {_item_hash(i) for i in items}),
            }
            fetched += 1
            print(f"  [world] {name}: wrote {len(items)} items ({new_count} new)", flush=True)

        _save_manifest(manifest)
        return fetched, skipped

    def context_snippet(self) -> str:
        """Compact 'what's happening right now' block for system prompt injection.

        Returns empty string when no observations exist yet so callers can use
        the simple ``if snippet: system += snippet`` pattern.
        """
        manifest = _load_manifest()
        if not manifest:
            return ""

        lines: list[str] = ["[World — what's happening right now]"]
        any_content = False

        for area in AREAS:
            name = area["name"]
            if name not in manifest:
                continue
            path = os.path.join(WORLD_DIR, f"{name}.md")
            if not os.path.exists(path):
                continue
            try:
                bullets = [
                    l.strip()
                    for l in open(path).read().splitlines()
                    if l.strip().startswith("- ")
                ][:SNIPPET_N]
                if bullets:
                    lines.append(f"\n{area['label']}:")
                    lines.extend(bullets)
                    any_content = True
            except Exception:
                continue

        if not any_content:
            return ""

        last_times = [
            v.get("last_fetch", 0)
            for v in manifest.values()
            if isinstance(v, dict)
        ]
        if last_times:
            ts = datetime.fromtimestamp(max(last_times)).strftime("%H:%M")
            lines.append(f"\n(last updated {ts})")

        return "\n".join(lines)


# ── module-level singleton and shims ───────────────────────────────────────────

_observer: WorldObserver | None = None


def _get() -> WorldObserver:
    global _observer
    if _observer is None:
        _observer = WorldObserver()
    return _observer


def tick() -> tuple[int, int]:
    """Called by worker.py every ~12 ticks."""
    return _get().tick()


def context_snippet() -> str:
    """Called by companion.py build_context."""
    return _get().context_snippet()


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Amy's world observer — fetch headlines.")
    p.add_argument("--status", action="store_true", help="Show last-fetch times")
    p.add_argument("--area", metavar="NAME",
                   help=f"Fetch one area only: {[a['name'] for a in AREAS]}")
    args = p.parse_args()

    if args.status:
        manifest = _load_manifest()
        if not manifest:
            print("No observations yet — run without flags to fetch.")
            return
        print(f"\n{'Area':<12} {'Last fetch':<20} Age")
        print("-" * 45)
        for name, meta in manifest.items():
            if not isinstance(meta, dict):
                continue
            ts = meta.get("last_fetch", 0)
            age_min = (time.time() - ts) / 60 if ts else None
            last = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "never"
            age_str = f"{age_min:.0f} min ago" if age_min is not None else ""
            print(f"{name:<12} {last:<20} {age_str}")
        print()
        snippet = context_snippet()
        if snippet:
            print("--- context_snippet ---")
            print(snippet)
        return

    if args.area:
        area = next((a for a in AREAS if a["name"] == args.area), None)
        if not area:
            print(f"Unknown area '{args.area}'. Choices: {[a['name'] for a in AREAS]}")
            return
        items = area["fetch"]()
        _write_area(area["name"], area["label"], items)
        print(f"Fetched {len(items)} items for '{args.area}':")
        for item in items:
            print(f"  - {item['title']}")
        return

    # Force-refresh all areas
    manifest = _load_manifest()
    for name in [a["name"] for a in AREAS]:
        if name in manifest and isinstance(manifest[name], dict):
            manifest[name]["last_fetch"] = 0
    _save_manifest(manifest)

    observer = WorldObserver()
    fetched, skipped = observer.tick()
    print(f"\nDone — {fetched} areas fetched, {skipped} skipped.")

    snippet = observer.context_snippet()
    if snippet:
        print("\n--- context_snippet preview ---")
        print(snippet)


if __name__ == "__main__":
    main()
