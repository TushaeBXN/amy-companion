"""Brian's original songs — parse and serve lyrics from the markdown file."""
import re

SONGS_FILE = "/Users/dadsmacpro/Documents/My Songs/my songs.md"


def _load():
    """Parse the songs file into a dict of {title: lyrics}."""
    try:
        with open(SONGS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return {}

    songs = {}
    current_title = None
    current_body = []

    def _save():
        if current_title and current_body:
            body = "\n".join(current_body).strip()
            body = re.sub(r"\\-", "-", body)
            body = re.sub(r"\\\n", "\n", body)
            songs[current_title] = body

    for line in lines:
        stripped = line.rstrip()

        # Format 1: **Track: Title**
        m = re.match(r"\*\*Track:\s*(.+?)\*\*", stripped)
        if m:
            _save()
            current_title = m.group(1).strip()
            current_body = []
            continue

        # Format 2: Track N: Title  (unbolded numbered tracks)
        m = re.match(r"Track\s+\d+:\s*(.+)", stripped)
        if m:
            _save()
            current_title = m.group(1).strip()
            current_body = []
            continue

        # Format 3: **This life** style — standalone bold title (not a Track: prefix)
        m = re.match(r"\*\*([^*]+)\*\*\s*$", stripped)
        if m and current_title is None or (m and not any(
            stripped.startswith(p) for p in ["**(", "**Chorus", "**Verse", "**Bridge", "**Outro", "**Intro"]
        )):
            candidate = m.group(1).strip()
            # Only treat as a new song if it's not a section label
            section_words = {"chorus", "verse", "bridge", "outro", "intro", "hook", "pre"}
            if not any(candidate.lower().startswith(w) for w in section_words):
                _save()
                current_title = candidate
                current_body = []
                continue

        if current_title is not None:
            current_body.append(stripped)

    _save()
    return songs


def list_songs():
    """Return a list of song titles."""
    return list(_load().keys())


def get_song(title):
    """Return full lyrics for a song by title (case-insensitive partial match)."""
    songs = _load()
    title_lower = title.lower()
    for name, lyrics in songs.items():
        if title_lower in name.lower():
            return {"title": name, "lyrics": lyrics}
    return None


def search_lyrics(query):
    """Search across all lyrics — returns list of {title, excerpt}."""
    songs = _load()
    q = query.lower()
    results = []
    for name, lyrics in songs.items():
        if q in lyrics.lower():
            for line in lyrics.splitlines():
                if q in line.lower() and line.strip():
                    results.append({"title": name, "excerpt": line.strip()})
                    break
    return results


def all_lyrics():
    """Return all songs as one block — for full context loading."""
    songs = _load()
    parts = []
    for title, lyrics in songs.items():
        parts.append(f"=== {title} ===\n{lyrics}")
    return "\n\n".join(parts)
