"""Image generation (mflux / FLUX.1-schnell) and Telegram delivery for Amy.

Env vars (put them in a git-ignored .env, never in the repo):
  TELEGRAM_TOKEN       token from @BotFather  (same var as the rest of the project)
  TELEGRAM_CHAT_ID     your chat id (Amy only ever sends to this one chat)
Optional:
  AMY_FLUX_PATH        pre-quantized model dir   (default: unset -> runs with -q 8)
  MFLUX_BIN            mflux-generate binary      (default ~/mflux-env/bin/mflux-generate)
  AMY_IMAGE_DIR        where images + log go     (default ~/amy-images)

Watch progress live in a second Terminal window:
  tail -f ~/amy-images/imagegen.log
"""
import os
import subprocess
import threading
import time
from pathlib import Path

import requests

MODEL_PATH = os.path.expanduser(os.environ.get("AMY_FLUX_PATH", "")) if os.environ.get("AMY_FLUX_PATH") else ""
MFLUX_BIN = os.path.expanduser(os.environ.get("MFLUX_BIN", "~/mflux-env/bin/mflux-generate"))
OUT_DIR = Path(os.path.expanduser(os.environ.get("AMY_IMAGE_DIR", "~/amy-images")))
LOG_FILE = OUT_DIR / "imagegen.log"
TIMEOUT_S = 900

_lock = threading.Lock()  # one generation at a time; 16GB can't fit two


def generate_image(prompt: str, seed: int | None = None, steps: int = 4) -> Path:
    """Run mflux as a subprocess (no shell) and return the image path."""
    if not _lock.acquire(blocking=False):
        raise RuntimeError("An image is already being generated.")
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        seed = int(time.time()) if seed is None else seed
        out = OUT_DIR / f"amy_{seed}.png"
        cmd = [MFLUX_BIN, "--model", "schnell"]
        cmd += ["--path", MODEL_PATH] if MODEL_PATH else ["-q", "8"]
        cmd += ["--prompt", prompt, "--steps", str(steps),
                "--seed", str(seed), "--output", str(out)]

        with open(LOG_FILE, "a") as log:
            log.write(f"\n=== {time.ctime()} :: {prompt!r}\n")
            log.flush()
            result = subprocess.run(
                cmd, stdout=log, stderr=subprocess.STDOUT, timeout=TIMEOUT_S
            )
        if result.returncode != 0 or not out.exists():
            raise RuntimeError(f"mflux failed (exit {result.returncode}); see {LOG_FILE}")
        return out
    finally:
        _lock.release()


def _tg_token() -> str:
    return os.environ["TELEGRAM_TOKEN"]   # matches the rest of the project


def _tg_chat() -> str:
    return os.environ["TELEGRAM_CHAT_ID"]


def send_telegram_photo(path: Path, caption: str = "") -> None:
    """Send one image to the configured chat. Errors never include the bot token."""
    try:
        with open(path, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{_tg_token()}/sendPhoto",
                data={"chat_id": _tg_chat(), "caption": caption[:1000]},
                files={"photo": f},
                timeout=60,
            )
        r.raise_for_status()
    except requests.RequestException as e:
        # The default message contains the URL (and so the token) — don't leak it.
        raise RuntimeError(f"Telegram send failed ({type(e).__name__})") from None


def _notify(text: str) -> None:
    """Send a plain text message to the owner chat (used for failure reporting)."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{_tg_token()}/sendMessage",
            json={"chat_id": _tg_chat(), "text": text[:4000]},
            timeout=15,
        )
    except Exception:
        pass


def create_slideshow(prompts: list, duration_per_image: int = 3) -> Path:
    """Generate one FLUX image per prompt and stitch into an mp4 with ffmpeg."""
    if not _lock.acquire(blocking=False):
        raise RuntimeError("An image is already being generated.")
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        paths = []
        for i, prompt in enumerate(prompts):
            seed = int(time.time()) + i
            out = OUT_DIR / f"amy_{seed}.png"
            cmd = [MFLUX_BIN, "--model", "schnell"]
            cmd += ["--path", MODEL_PATH] if MODEL_PATH else ["-q", "8"]
            cmd += ["--prompt", prompt, "--steps", "4",
                    "--seed", str(seed), "--output", str(out)]
            with open(LOG_FILE, "a") as log:
                log.write(f"\n=== {time.ctime()} [slide {i+1}/{len(prompts)}] :: {prompt!r}\n")
                log.flush()
                result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=TIMEOUT_S)
            if result.returncode != 0 or not out.exists():
                raise RuntimeError(f"mflux failed on slide {i+1} (exit {result.returncode})")
            paths.append(out)

        # write ffmpeg concat list
        ts = int(time.time())
        list_file = OUT_DIR / f"slideshow_{ts}.txt"
        with open(list_file, "w") as f:
            for path in paths:
                f.write(f"file '{path}'\nduration {duration_per_image}\n")
            f.write(f"file '{paths[-1]}'\n")  # ffmpeg needs final frame repeated

        out_mp4 = OUT_DIR / f"amy_slideshow_{ts}.mp4"
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                   "pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            "-c:v", "libx264", "-r", "30", str(out_mp4),
        ]
        subprocess.run(ffmpeg_cmd, capture_output=True, check=True, timeout=120)
        list_file.unlink(missing_ok=True)
        return out_mp4
    finally:
        _lock.release()


def generate_and_send(prompt: str, caption: str = "") -> str:
    """The tool Amy calls. Sends the image (or error) to Telegram; returns a status string."""
    try:
        path = generate_image(prompt)
        send_telegram_photo(path, caption or prompt)
        return f"Done. Generated and sent {path.name}."
    except Exception as e:
        msg = f"Image generation failed: {e}"
        _notify(msg)
        return msg
