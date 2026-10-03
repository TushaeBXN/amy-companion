"""
Creator study pipeline: download → transcribe → analyze → profile.
Writes to ~/ai-knowledge/creators/{platform}/{username}/
"""
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

KNOWLEDGE_BASE = Path.home() / "ai-knowledge"
CREATORS_DIR = KNOWLEDGE_BASE / "creators"
INDEX_FILE = KNOWLEDGE_BASE / "creators" / "index.md"

PLATFORMS = {"tiktok", "youtube", "podcast"}

# Pass --cookies-from-browser safari so yt-dlp reads the logged-in Safari session
TIKTOK_COOKIE_ARGS = ["--cookies-from-browser", "safari"]


def _slug(text: str) -> str:
    return re.sub(r"[^\w\-]", "_", text.lower().strip("@"))


def _creator_dir(platform: str, username: str) -> Path:
    d = CREATORS_DIR / platform / _slug(username)
    d.mkdir(parents=True, exist_ok=True)
    (d / "transcripts").mkdir(exist_ok=True)
    return d


def _download_videos(url: str, out_dir: Path, max_videos: int = 5, use_cookies: bool = True) -> list[Path]:
    """Download audio tracks from a creator's page using yt-dlp."""
    template = str(out_dir / "%(id)s.%(ext)s")
    cmd = ["yt-dlp"]
    if use_cookies and "tiktok.com" in url:
        cmd += TIKTOK_COOKIE_ARGS
    cmd += [
        "--no-playlist" if "watch?v=" in url else f"--playlist-end={max_videos}",
        "-x", "--audio-format", "mp3",
        "--audio-quality", "5",
        "--write-info-json",
        "--no-warnings",
        "-o", template,
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr[:400]}")
    return sorted(out_dir.glob("*.mp3"))


def _transcribe(audio_path: Path) -> str:
    """Transcribe audio file using openai-whisper (base model, runs locally)."""
    import whisper
    model = whisper.load_model("base")
    result = model.transcribe(str(audio_path), fp16=False)
    return result["text"].strip()


def _load_metadata(info_dir: Path) -> list[dict]:
    metas = []
    for f in info_dir.glob("*.info.json"):
        try:
            with open(f) as fh:
                data = json.load(fh)
                metas.append({
                    "id": data.get("id", ""),
                    "title": data.get("title", ""),
                    "views": data.get("view_count", 0),
                    "likes": data.get("like_count", 0),
                    "duration": data.get("duration", 0),
                    "description": (data.get("description", "") or "")[:300],
                })
        except Exception:
            pass
    return metas


def _update_index(platform: str, username: str, n_videos: int, studied_by: str):
    lines = INDEX_FILE.read_text().splitlines()
    today = datetime.now().strftime("%Y-%m-%d")
    new_row = f"| {platform} | {username} | {studied_by} | {today} | {n_videos} |"
    # Remove existing row for this creator if present
    lines = [l for l in lines if not (f"| {platform} |" in l and f"| {username} |" in l)]
    # Insert after header rows
    for i, line in enumerate(lines):
        if line.startswith("|---"):
            lines.insert(i + 1, new_row)
            break
    INDEX_FILE.write_text("\n".join(lines) + "\n")


def study_creator(
    url: str,
    platform: str = "tiktok",
    username: str = "",
    max_videos: int = 5,
    studied_by: str = "Amy",
) -> str:
    """
    Full pipeline: download → transcribe → save transcripts → return raw data
    for Amy to analyze and write profile.md.

    Returns a summary dict as JSON string for Amy to process.
    """
    platform = platform.lower()
    if platform not in PLATFORMS:
        return f"Unknown platform '{platform}'. Use: tiktok, youtube, podcast"

    if not username:
        # Try to extract from URL
        m = re.search(r"@([\w.]+)", url)
        username = m.group(1) if m else url.split("/")[-1].strip("/")

    creator_dir = _creator_dir(platform, username)
    transcript_dir = creator_dir / "transcripts"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        print(f"  [study] downloading {platform}/@{username} (up to {max_videos} videos)…")
        try:
            audio_files = _download_videos(url, tmp_path, max_videos, use_cookies=(platform == "tiktok"))
        except RuntimeError as e:
            return f"Download failed: {e}"

        if not audio_files:
            return f"No audio found at {url} — check the URL or the creator's privacy settings"

        metas = _load_metadata(tmp_path)
        transcripts = []
        for af in audio_files:
            print(f"  [study] transcribing {af.name}…")
            try:
                text = _transcribe(af)
                tid = af.stem
                out = transcript_dir / f"{tid}.txt"
                out.write_text(text)
                transcripts.append({"id": tid, "text": text[:2000]})
            except Exception as e:
                transcripts.append({"id": af.stem, "error": str(e)})

    _update_index(platform, username, len(audio_files), studied_by)

    summary = {
        "platform": platform,
        "username": username,
        "videos_studied": len(audio_files),
        "metadata": metas[:10],
        "transcripts": transcripts,
        "profile_path": str(creator_dir / "profile.md"),
        "transcript_dir": str(transcript_dir),
        "instruction": (
            "Analyze the transcripts and metadata above. Write a creator profile to "
            f"{creator_dir / 'profile.md'} with these sections:\n"
            "## Voice & Tone\n## Hook Patterns (first 5 seconds)\n"
            "## Recurring Topics\n## Vocabulary & Phrases\n"
            "## What Gets Engagement\n## How to Write Like Them (style guide)\n"
            "## Direct Quote Examples\n\n"
            f"End with: *Studied by {studied_by} on {datetime.now().strftime('%Y-%m-%d')}*"
        ),
    }
    return json.dumps(summary, indent=2)


def tiktok_search(
    hashtag: str,
    max_videos: int = 8,
    studied_by: str = "Amy",
) -> str:
    """
    Search TikTok by hashtag, download top videos, transcribe, and save to
    ~/ai-knowledge/creators/tiktok/{hashtag}/.
    Uses Safari cookies so results match the logged-in For You feed.
    """
    tag = hashtag.lstrip("#")
    url = f"https://www.tiktok.com/tag/{tag}"
    creator_dir = _creator_dir("tiktok", f"tag_{tag}")
    transcript_dir = creator_dir / "transcripts"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        print(f"  [study] searching TikTok #{tag} (up to {max_videos} videos)…")
        cmd = [
            "yt-dlp",
            *TIKTOK_COOKIE_ARGS,
            f"--playlist-end={max_videos}",
            "-x", "--audio-format", "mp3",
            "--audio-quality", "5",
            "--write-info-json",
            "--no-warnings",
            "-o", str(tmp_path / "%(id)s.%(ext)s"),
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return f"TikTok search failed for #{tag}: {result.stderr[:400]}"

        audio_files = sorted(tmp_path.glob("*.mp3"))
        if not audio_files:
            return f"No videos found for #{tag} — TikTok may require login or the tag is empty."

        metas = _load_metadata(tmp_path)
        transcripts = []
        for af in audio_files:
            print(f"  [study] transcribing {af.name}…")
            try:
                text = _transcribe(af)
                tid = af.stem
                (transcript_dir / f"{tid}.txt").write_text(text)
                transcripts.append({"id": tid, "text": text[:2000]})
            except Exception as e:
                transcripts.append({"id": af.stem, "error": str(e)})

    _update_index("tiktok", f"#{tag}", len(audio_files), studied_by)

    summary = {
        "hashtag": tag,
        "url": url,
        "videos_studied": len(audio_files),
        "metadata": metas[:10],
        "transcripts": transcripts,
        "profile_path": str(creator_dir / "profile.md"),
        "instruction": (
            f"Analyze the top TikTok videos for #{tag}. Write a trend report to "
            f"{creator_dir / 'profile.md'} with:\n"
            "## What's Working (hooks, formats, tone)\n"
            "## Recurring Ideas & Angles\n"
            "## Income Opportunities Mentioned\n"
            "## How to Create Content in This Space\n"
            "## Top Takeaways for Amy\n\n"
            f"*Studied by {studied_by} on {datetime.now().strftime('%Y-%m-%d')}*"
        ),
    }
    return json.dumps(summary, indent=2)
