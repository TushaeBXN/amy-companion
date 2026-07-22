"""Webcam capture for Ava (macOS / AVFoundation via ffmpeg).

Grabs a single still from the FaceTime HD Camera. The frame is saved locally
and only ever handed to the LOCAL Ollama model — nothing leaves the machine.

macOS will require Camera permission for the terminal app running this the
first time (System Settings > Privacy & Security > Camera).
"""
import subprocess
import os

DEVICE = os.environ.get("AVA_CAM", "0")          # ffmpeg avfoundation index
FRAME_PATH = os.environ.get("AVA_FRAME", "/tmp/ava_frame.jpg")


def capture(path=FRAME_PATH, device=DEVICE, warmup=8):
    """Capture one webcam frame to `path`. Returns the path, or None on failure.

    `-update 1` keeps overwriting the same file, so after `warmup` frames the
    saved image is a warmed-up (non-black) frame.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "avfoundation", "-framerate", "30", "-video_size", "1280x720",
        "-pixel_format", "nv12", "-i", device,
        "-frames:v", str(warmup), "-update", "1", "-y", path,
    ]
    try:
        subprocess.run(cmd, check=True, timeout=20)
    except subprocess.CalledProcessError as e:
        print(f"  [vision] capture failed (camera permission?): {e}")
        return None
    except FileNotFoundError:
        print("  [vision] ffmpeg not found")
        return None
    except subprocess.TimeoutExpired:
        print("  [vision] capture timed out")
        return None
    return path if os.path.exists(path) else None
