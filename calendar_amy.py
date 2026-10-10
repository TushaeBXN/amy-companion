"""Apple Calendar reader for Amy — uses AppleScript, no extra installs needed."""
import subprocess
from datetime import datetime


def _run(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
    if r.returncode != 0 and r.stderr:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


def get_events(days_ahead: int = 7) -> list[dict]:
    """Return upcoming calendar events as a list of dicts."""
    script = f"""
tell application "Calendar"
    set today to current date
    set cutoff to today + ({days_ahead} * days)
    set output to ""
    repeat with cal in every calendar
        try
            set evts to (every event of cal whose start date >= today and start date <= cutoff)
            repeat with e in evts
                try
                    set eName to summary of e
                    set eStart to start date of e as string
                    set eEnd to end date of e as string
                    set calName to name of cal
                    set output to output & calName & "||" & eName & "||" & eStart & "||" & eEnd & "\n"
                end try
            end repeat
        end try
    end repeat
    return output
end tell
"""
    raw = _run(script)
    events = []
    for line in raw.splitlines():
        parts = line.split("||")
        if len(parts) == 4:
            events.append({
                "calendar": parts[0].strip(),
                "title": parts[1].strip(),
                "start": parts[2].strip(),
                "end": parts[3].strip(),
            })
    events.sort(key=lambda e: e["start"])
    return events


def get_today() -> list[dict]:
    """Return only today's events."""
    script = """
tell application "Calendar"
    set today to current date
    set startOfDay to today - (time of today) * seconds
    set endOfDay to startOfDay + (1 * days) - 1
    set output to ""
    repeat with cal in every calendar
        try
            set evts to (every event of cal whose start date >= startOfDay and start date <= endOfDay)
            repeat with e in evts
                try
                    set eName to summary of e
                    set eStart to start date of e as string
                    set eEnd to end date of e as string
                    set calName to name of cal
                    set output to output & calName & "||" & eName & "||" & eStart & "||" & eEnd & "\n"
                end try
            end repeat
        end try
    end repeat
    return output
end tell
"""
    raw = _run(script)
    events = []
    for line in raw.splitlines():
        parts = line.split("||")
        if len(parts) == 4:
            events.append({
                "calendar": parts[0].strip(),
                "title": parts[1].strip(),
                "start": parts[2].strip(),
                "end": parts[3].strip(),
            })
    events.sort(key=lambda e: e["start"])
    return events


def format_events(events: list[dict]) -> str:
    if not events:
        return "No events found."
    lines = []
    for e in events:
        lines.append(f"- {e['title']} ({e['calendar']}): {e['start']} → {e['end']}")
    return "\n".join(lines)
