"""macOS Music app interface for Amy — via AppleScript."""
import subprocess
import json


def _run(script):
    result = subprocess.run(
        ["osascript", "-e", script],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, timeout=10
    )
    return result.stdout.strip()


def now_playing():
    """Return info about the currently playing track, or None."""
    script = """
    tell application "Music"
        if player state is playing then
            set t to current track
            return (name of t) & "|" & (artist of t) & "|" & (album of t) & "|" & (get player position as integer)
        else if player state is paused then
            set t to current track
            return "PAUSED|" & (name of t) & "|" & (artist of t) & "|" & (album of t)
        else
            return "STOPPED"
        end if
    end tell
    """
    out = _run(script)
    if not out or out == "STOPPED":
        return {"state": "stopped"}
    if out.startswith("PAUSED|"):
        parts = out[7:].split("|")
        return {"state": "paused", "name": parts[0], "artist": parts[1] if len(parts) > 1 else "",
                "album": parts[2] if len(parts) > 2 else ""}
    parts = out.split("|")
    return {
        "state": "playing",
        "name": parts[0],
        "artist": parts[1] if len(parts) > 1 else "",
        "album": parts[2] if len(parts) > 2 else "",
        "position_sec": int(parts[3]) if len(parts) > 3 else 0,
    }


def get_albums(limit=50):
    """Return a list of unique albums in the library."""
    script = f"""
    tell application "Music"
        set albumSet to {{}}
        set trackList to every track of library playlist 1
        repeat with t in trackList
            set a to album of t
            if a is not in albumSet then
                set end of albumSet to a
            end if
            if (count of albumSet) >= {limit} then exit repeat
        end repeat
        set output to ""
        repeat with a in albumSet
            set output to output & a & linefeed
        end repeat
        return output
    end tell
    """
    out = _run(script)
    return [line for line in out.splitlines() if line.strip()]


def get_artists(limit=50):
    """Return a list of unique artists in the library."""
    script = f"""
    tell application "Music"
        set artistSet to {{}}
        set trackList to every track of library playlist 1
        repeat with t in trackList
            set a to artist of t
            if a is not "" and a is not in artistSet then
                set end of artistSet to a
            end if
            if (count of artistSet) >= {limit} then exit repeat
        end repeat
        set output to ""
        repeat with a in artistSet
            set output to output & a & linefeed
        end repeat
        return output
    end tell
    """
    out = _run(script)
    return [line for line in out.splitlines() if line.strip()]


def get_playlists():
    """Return list of playlist names."""
    script = """
    tell application "Music"
        set output to ""
        repeat with p in every playlist
            set output to output & (name of p) & linefeed
        end repeat
        return output
    end tell
    """
    out = _run(script)
    return [line for line in out.splitlines() if line.strip()]


def search_tracks(query, limit=10):
    """Search tracks by name, artist, or album."""
    q = query.lower().replace('"', '')
    script = f"""
    tell application "Music"
        set results to {{}}
        set q to "{q}"
        repeat with t in every track of library playlist 1
            set n to (name of t as string)
            set ar to (artist of t as string)
            set al to (album of t as string)
            if (n contains q) or (ar contains q) or (al contains q) then
                set end of results to (n & "|" & ar & "|" & al)
            end if
            if (count of results) >= {limit} then exit repeat
        end repeat
        set output to ""
        repeat with r in results
            set output to output & r & linefeed
        end repeat
        return output
    end tell
    """
    out = _run(script)
    tracks = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        tracks.append({
            "name": parts[0],
            "artist": parts[1] if len(parts) > 1 else "",
            "album": parts[2] if len(parts) > 2 else "",
        })
    return tracks


def play_track(name="", artist="", album=""):
    """Play a specific track by name, artist, or album match."""
    # Build a search script that finds and plays the best match
    q = (name or artist or album).lower().replace('"', '')
    script = f"""
    tell application "Music"
        set q to "{q}"
        set found to false
        repeat with t in every track of library playlist 1
            set n to (name of t as string)
            set ar to (artist of t as string)
            set al to (album of t as string)
            if (n contains q) or (ar contains q) or (al contains q) then
                play t
                set found to true
                return "playing: " & (name of t) & " by " & (artist of t)
            end if
        end repeat
        if not found then return "not found: " & q
    end tell
    """
    return _run(script)


def play_playlist(name):
    """Play a playlist by name."""
    safe = name.replace('"', '')
    script = f"""
    tell application "Music"
        try
            set p to playlist "{safe}"
            play p
            return "playing playlist: {safe}"
        on error
            return "playlist not found: {safe}"
        end try
    end tell
    """
    return _run(script)


def control(action):
    """Playback control: play, pause, stop, next, previous, shuffle."""
    actions = {
        "play":     "play",
        "pause":    "pause",
        "stop":     "stop",
        "next":     "next track",
        "previous": "previous track",
        "shuffle":  "set shuffle enabled to not shuffle enabled",
    }
    cmd = actions.get(action)
    if not cmd:
        return f"unknown action: {action}"
    script = f'tell application "Music" to {cmd}'
    _run(script)
    return f"done: {action}"


def set_volume(level):
    """Set volume 0–100."""
    level = max(0, min(100, int(level)))
    script = f'tell application "Music" to set sound volume to {level}'
    _run(script)
    return f"volume set to {level}"
