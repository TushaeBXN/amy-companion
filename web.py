"""Web access for Amy — search and read articles."""
import re

from ddgs import DDGS
import requests
from bs4 import BeautifulSoup

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def search(query, max_results=5):
    """Run a DuckDuckGo search — returns list of {title, url, snippet}."""
    results = []
    for r in DDGS().text(query, max_results=max_results):
        results.append({
            "title": r.get("title", ""),
            "url": r.get("href", ""),
            "snippet": r.get("body", ""),
        })
    return results


_JS_HOSTS = {"tiktok.com", "www.tiktok.com", "instagram.com", "www.instagram.com"}


def _needs_js(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        return urlparse(url).hostname in _JS_HOSTS
    except Exception:
        return False


def _read_url_js(url: str, max_chars: int = 6000) -> str:
    """Fetch a JS-rendered page with headless Chromium. Returns visible text."""
    from playwright.sync_api import sync_playwright
    try:
        from playwright_stealth import stealth_sync as _stealth
    except ImportError:
        _stealth = None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = ctx.new_page()
        if _stealth:
            _stealth(page)
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        # Scroll to trigger lazy-loaded comments
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        page.wait_for_timeout(3000)
        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    article = soup.find("article") or soup.find("main") or soup.body
    if not article:
        return "(no readable content)"
    text = article.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...(truncated)"
    return text


def read_url(url, max_chars=4000):
    """Fetch a URL and extract the main text content."""
    if _needs_js(url):
        try:
            return _read_url_js(url, max_chars=max_chars)
        except Exception as e:
            print(f"  [web] JS fetch failed ({e}), falling back to plain HTTP")

    resp = _SESSION.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    article = soup.find("article") or soup.find("main") or soup.body
    if not article:
        return "(no readable content)"

    text = article.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...(truncated)"
    return text
