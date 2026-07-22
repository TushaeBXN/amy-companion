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


def read_url(url, max_chars=4000):
    """Fetch a URL and extract the main text content."""
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
