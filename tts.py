"""Text-to-speech: macOS say → OGG/Opus for Telegram sendVoice."""
import subprocess
import tempfile
from pathlib import Path

VOICES = ["Samantha", "Kathy", "Sandy (English (US))", "Shelley (English (US))"]


def generate_ogg(text: str) -> Path:
    """Synthesize text to an OGG/Opus file. Returns the path (caller deletes it)."""
    aiff = Path(tempfile.mktemp(suffix=".aiff"))
    ogg = aiff.with_suffix(".ogg")
    try:
        spoken = False
        for voice in VOICES:
            result = subprocess.run(
                ["say", "-v", voice, "-o", str(aiff), text],
                capture_output=True, timeout=60
            )
            if result.returncode == 0:
                spoken = True
                break
        if not spoken:
            raise RuntimeError("No available TTS voice succeeded")
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(aiff),
             "-c:a", "libopus", "-b:a", "32k", str(ogg)],
            check=True, capture_output=True, timeout=60
        )
        return ogg
    finally:
        aiff.unlink(missing_ok=True)
