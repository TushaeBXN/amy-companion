"""Offline checks: each trending repo keeps its own description; page chrome is never a repo."""
import feeds
import world

_PAGE = """
<nav><a href="/sponsors/explore">Sponsors</a> <a href="/trending/developers">Developers</a>
 <a href="/apps/github-actions">Actions</a> <a href="/features/copilot">Copilot</a></nav>
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a data-hydro-click="{&quot;a&quot;:1}" href="/php/php-src"> php / <span>php-src</span></a></h2>
  <p class="col-9 color-fg-muted my-1 pr-4">The PHP Interpreter</p>
  <svg><path d="M8 .25a.75.75"></path></svg>
</article>
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/nodejs/node"> nodejs / node</a></h2>
  <p class="col-9 color-fg-muted my-1 pr-4">Node.js JavaScript runtime
     <a href="/x">emoji</a></p>
</article>
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/owner/no-description"> owner / no-description</a></h2>
</article>
<footer><a href="/apps/dependabot">Dependabot</a></footer>
"""


class _Resp:
    text = _PAGE


class _S:
    def get(self, url, timeout=0):
        return _Resp()


def test_repos_keep_their_own_descriptions_and_chrome_is_ignored(monkeypatch):
    monkeypatch.setattr(feeds, "_S", _S())
    assert feeds.github_trending().splitlines() == [
        "- https://github.com/php/php-src  The PHP Interpreter",
        "- https://github.com/nodejs/node  Node.js JavaScript runtime emoji",
        "- https://github.com/owner/no-description  ",
    ]


def test_empty_page_gives_the_not_found_message(monkeypatch):
    class Empty:
        text = "<html>nothing</html>"
    monkeypatch.setattr(feeds, "_S", type("S", (), {"get": lambda self, u, timeout=0: Empty()})())
    assert feeds.github_trending() == "No trending repos found."


def test_world_keeps_trending_repos_with_their_descriptions():
    raw = ("- https://github.com/php/php-src  The PHP Interpreter\n"
           "- https://github.com/owner/bare  \n"
           "No trending repos found.")
    assert world._parse_repos(raw) == [
        {"title": "php/php-src: The PHP Interpreter", "url": "https://github.com/php/php-src"},
        {"title": "owner/bare", "url": "https://github.com/owner/bare"},
    ]
    assert world._parse_repos("No trending repos found.") == []


def test_tech_area_includes_github_repos(monkeypatch):
    monkeypatch.setattr(feeds, "hacker_news", lambda limit=10, kind="top": "\n".join(
        f"- Front page story number {n} here ({50 - n} pts) https://hn{n}.example" for n in range(1, 11)))
    monkeypatch.setattr(feeds, "github_trending", lambda *a, **k:
                        "- https://github.com/php/php-src  The PHP Interpreter\n- https://github.com/nodejs/node  Node.js runtime")
    titles = [i["title"] for i in world._fetch_tech()]
    assert "php/php-src: The PHP Interpreter" in titles and "nodejs/node: Node.js runtime" in titles
    assert len(titles) == 5          # 3 HN + 2 GitHub
