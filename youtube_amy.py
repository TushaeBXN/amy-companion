"""YouTube learning module for Amy.

Fetches transcripts from YouTube videos and channel pages so Amy can
learn from tech content and surface actionable improvements.

No API key needed — uses youtube-transcript-api + light HTML scraping.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests
from youtube_transcript_api import YouTubeTranscriptApi

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_WATCHED_PATH = Path(__file__).parent / "youtube_watched.json"
_CHANNELS_PATH = Path(__file__).parent / "youtube_channels.json"

_YT_API = YouTubeTranscriptApi()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _video_id(url_or_id: str) -> str:
    """Extract an 11-char video ID from a URL or return it as-is."""
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{11})", url_or_id)
    return m.group(1) if m else url_or_id.strip()


def _load_watched() -> set:
    if _WATCHED_PATH.exists():
        return set(json.loads(_WATCHED_PATH.read_text()).get("ids", []))
    return set()


def _mark_watched(video_id: str):
    ids = _load_watched()
    ids.add(video_id)
    _WATCHED_PATH.write_text(json.dumps({"ids": sorted(ids)}, indent=2))


def _load_channels() -> list[dict]:
    if _CHANNELS_PATH.exists():
        return json.loads(_CHANNELS_PATH.read_text()).get("channels", [])
    return []


def _save_channels(channels: list[dict]):
    _CHANNELS_PATH.write_text(json.dumps({"channels": channels}, indent=2))


# ── Core functions ────────────────────────────────────────────────────────────

def get_transcript(url_or_id: str, max_chars: int = 12000) -> str:
    """Fetch the transcript of a YouTube video. Returns plain text."""
    vid = _video_id(url_or_id)
    try:
        snippets = list(_YT_API.fetch(vid, languages=["en"]))
    except Exception:
        # Fall back to any available language
        try:
            snippets = list(_YT_API.fetch(vid))
        except Exception as e:
            raise RuntimeError(f"No transcript available: {e}")

    text = " ".join(s.text for s in snippets)
    text = re.sub(r"\[.*?\]", "", text)  # strip [Music], [Applause] etc.
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "...(truncated)"
    return text


def get_video_title(url_or_id: str) -> str:
    """Fetch the title of a YouTube video."""
    vid = _video_id(url_or_id)
    try:
        r = _SESSION.get(f"https://www.youtube.com/watch?v={vid}", timeout=10)
        m = re.search(r'"title":"([^"]+)"', r.text)
        if m:
            return m.group(1).replace("\\u0026", "&")
    except Exception:
        pass
    return vid


def get_channel_videos(channel_url: str, limit: int = 10) -> list[dict]:
    """Return recent video IDs + titles from a YouTube channel page."""
    r = _SESSION.get(channel_url, timeout=15)
    r.raise_for_status()

    # Extract all unique video IDs in order of appearance
    ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', r.text)
    seen: set = set()
    unique_ids = [x for x in ids if not (x in seen or seen.add(x))][:limit]

    # Extract titles from richVideoRenderer or videoRenderer title runs
    title_matches = re.findall(
        r'"videoId":"([a-zA-Z0-9_-]{11})"[^}]*?"text":"([^"]{5,100})"', r.text
    )
    title_map = {}
    for vid_id, title in title_matches:
        if vid_id not in title_map:
            title_map[vid_id] = title

    results = []
    for vid_id in unique_ids:
        title = title_map.get(vid_id)
        if not title or title == "(unknown title)":
            try:
                title = get_video_title(vid_id)
            except Exception:
                title = vid_id
        results.append({"id": vid_id, "title": title})
    return results


def new_videos_from_channels() -> list[dict]:
    """Check all tracked channels for videos Amy hasn't watched yet."""
    watched = _load_watched()
    channels = _load_channels()
    new = []
    for ch in channels:
        try:
            videos = get_channel_videos(ch["url"], limit=15)
            for v in videos:
                if v["id"] not in watched:
                    new.append({**v, "channel": ch.get("name", ch["url"])})
        except Exception as e:
            print(f"  [youtube] failed to scan {ch.get('name', ch['url'])}: {e}")
        time.sleep(1)
    return new


def add_channel(url: str, name: str = "") -> str:
    """Add a channel to Amy's watch list."""
    channels = _load_channels()
    # Normalize to /videos page
    url = re.sub(r"/featured|/about|/community|/playlists", "", url.rstrip("/"))
    if not url.endswith("/videos"):
        url = url.rstrip("/") + "/videos"
    for ch in channels:
        if ch["url"] == url:
            return f"Already tracking: {name or url}"
    channels.append({"url": url, "name": name or url})
    _save_channels(channels)
    return f"Now tracking: {name or url}"


def remove_channel(name_or_url: str) -> str:
    """Remove a channel from Amy's watch list."""
    channels = _load_channels()
    before = len(channels)
    channels = [c for c in channels if name_or_url.lower() not in c["url"].lower()
                and name_or_url.lower() not in c.get("name", "").lower()]
    _save_channels(channels)
    removed = before - len(channels)
    return f"Removed {removed} channel(s)." if removed else "No matching channel found."


def list_channels() -> str:
    channels = _load_channels()
    if not channels:
        return "No channels tracked yet."
    return "\n".join(f"- {c.get('name', c['url'])}: {c['url']}" for c in channels)


def mark_watched(url_or_id: str):
    _mark_watched(_video_id(url_or_id))
