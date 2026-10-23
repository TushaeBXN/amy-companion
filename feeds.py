"""Live intelligence feeds for Amy — HN, ArXiv, GitHub Trending, Reddit.

No API keys needed. All feeds are public.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

_S = requests.Session()
_S.headers["User-Agent"] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ── Hacker News ───────────────────────────────────────────────────────────────

_HN_KINDS = {"top", "best", "new", "show", "ask"}


def hacker_news(limit: int = 10, kind: str = "top") -> str:
    """Stories from Hacker News right now. kind: top (front page), best, new, show (Show HN), ask."""
    if kind not in _HN_KINDS:
        raise ValueError(f"unknown Hacker News list '{kind}'; use one of {sorted(_HN_KINDS)}")
    r = _S.get(f"https://hacker-news.firebaseio.com/v0/{kind}stories.json", timeout=10)
    ids = r.json()[:limit]
    stories = []
    for sid in ids:
        try:
            s = _S.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=5).json()
            if s and s.get("type") == "story":
                stories.append(f"- {s.get('title', '')} ({s.get('score', 0)} pts) {s.get('url', '')}")
        except Exception:
            continue
    return "\n".join(stories) or "No stories found."


# ── ArXiv ─────────────────────────────────────────────────────────────────────

_ARXIV_CATS = {
    "ai": "cs.AI",
    "ml": "cs.LG",
    "cv": "cs.CV",
    "nlp": "cs.CL",
    "robotics": "cs.RO",
}


def arxiv_papers(category: str = "ai", limit: int = 8) -> str:
    """Latest ArXiv papers in a category (ai, ml, cv, nlp, robotics)."""
    cat = _ARXIV_CATS.get(category.lower(), category)
    url = (
        f"http://export.arxiv.org/api/query"
        f"?search_query=cat:{cat}&sortBy=submittedDate&sortOrder=descending&max_results={limit}"
    )
    r = _S.get(url, timeout=15)
    root = ET.fromstring(r.text)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    papers = []
    for entry in root.findall("a:entry", ns):
        title = entry.findtext("a:title", "", ns).strip().replace("\n", " ")
        summary = entry.findtext("a:summary", "", ns).strip()[:200].replace("\n", " ")
        link = entry.findtext("a:id", "", ns).strip()
        papers.append(f"- {title}\n  {summary}...\n  {link}")
    return "\n\n".join(papers) or "No papers found."


# ── GitHub Trending ───────────────────────────────────────────────────────────

def github_trending(language: str = "", since: str = "daily") -> str:
    """Trending repos on GitHub today."""
    url = "https://github.com/trending"
    if language:
        url += f"/{language.lower()}"
    url += f"?since={since}"
    r = _S.get(url, timeout=15)
    # Match repo links in the trending page
    repos = re.findall(r'href="/([a-zA-Z0-9_.\-]+/[a-zA-Z0-9_.\-]+)"', r.text)
    descs = re.findall(r'<p[^>]*col-9[^>]*>\s*(.*?)\s*</p>', r.text, re.DOTALL)
    seen: set = set()
    # Filter out navigation/meta links (single-segment paths already excluded by regex)
    skip = {"features", "enterprise", "explore", "marketplace", "pricing", "login", "join"}
    unique = [x for x in repos if x.split("/")[0].lower() not in skip
              and not (x in seen or seen.add(x))][:10]
    lines = []
    for i, repo in enumerate(unique):
        desc = re.sub(r"\s+", " ", descs[i]).strip() if i < len(descs) else ""
        lines.append(f"- https://github.com/{repo}  {desc[:100]}")
    return "\n".join(lines) or "No trending repos found."


# ── Reddit ────────────────────────────────────────────────────────────────────

_SUBS = {
    "localllama": "LocalLLaMA",
    "machinelearning": "MachineLearning",
    "artificial": "artificial",
    "singularity": "singularity",
    "programming": "programming",
    "python": "Python",
    "indiehackers": "indiehackers",
    "entrepreneur": "Entrepreneur",
    "sidehustle": "sidehustle",
}


def reddit_hot(subreddit: str = "localllama", limit: int = 8) -> str:
    """Hot posts from a subreddit via RSS."""
    sub = _SUBS.get(subreddit.lower(), subreddit)
    r = _S.get(
        f"https://www.reddit.com/r/{sub}/hot.rss?limit={limit}",
        headers={"User-Agent": "Amy-companion/1.0 (learning bot)"},
        timeout=10,
    )
    r.raise_for_status()
    root = ET.fromstring(r.text)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall("a:entry", ns)
    lines = []
    for entry in entries[:limit]:
        title = entry.findtext("a:title", "", ns).strip()
        link_el = entry.find("a:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        lines.append(f"- {title}\n  {link}")
    return "\n".join(lines) or "No posts found."


# ── Product Hunt ──────────────────────────────────────────────────────────────

_ATOM = "{http://www.w3.org/2005/Atom}"


def product_hunt_today() -> str:
    """Today's top products on Product Hunt. The feed is Atom (<entry>); RSS (<item>) still works.

    It used to read only <item>, found nothing in the Atom feed and returned 'No products found.'"""
    r = _S.get("https://www.producthunt.com/feed", timeout=10)
    root = ET.fromstring(r.text)
    lines = []
    for entry in root.findall(f".//{_ATOM}entry")[:8]:
        title = (entry.findtext(f"{_ATOM}title") or "").strip()
        link_el = entry.find(f"{_ATOM}link[@rel='alternate']")
        link = (link_el.get("href") if link_el is not None else "").strip()
        html = entry.findtext(f"{_ATOM}content") or entry.findtext(f"{_ATOM}summary") or ""
        desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
        desc = re.sub(r"\s*Discussion \| Link$", "", desc)[:120]   # footer links in the feed's HTML
        if title:
            lines.append(f"- {title}: {desc}  {link}")
    for item in root.findall(".//item")[:8]:
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        desc = item.findtext("description", "").strip()[:120]
        lines.append(f"- {title}: {desc}  {link}")
    return "\n".join(lines[:8]) or "No products found."
