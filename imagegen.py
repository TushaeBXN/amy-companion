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

import heavy_job

MODEL_PATH = os.path.expanduser(os.environ.get("AMY_FLUX_PATH", "")) if os.environ.get("AMY_FLUX_PATH") else ""
MFLUX_BIN = os.path.expanduser(os.environ.get("MFLUX_BIN", "~/mflux-env/bin/mflux-generate"))
OUT_DIR = Path(os.path.expanduser(os.environ.get("AMY_IMAGE_DIR", "~/amy-images")))
LOG_FILE = OUT_DIR / "imagegen.log"
TIMEOUT_S = 900

# Paused (Brian's call, 2026-10-23): FLUX peaks at ~21.8 GB of GPU memory on a 16 GB Mac, which
# froze the machine and aborted mflux with "Insufficient Memory" while Amy was running. Set
# AMY_IMAGEGEN_PAUSED=0 to resume. Run it alone (about 3 min, 178 s measured) or not at all.
PAUSED = os.environ.get("AMY_IMAGEGEN_PAUSED", "1") != "0"

# Lower-bit FLUX weights use less GPU memory than the 8-bit she ran at (peak 21.8 GB). mflux
# accepts 3, 4, 5, 6 or 8. Default 4; set AMY_FLUX_QUANT=8 for the old behaviour.
_Q = os.environ.get("AMY_FLUX_QUANT", "4")
FLUX_QUANT = _Q if _Q in {"3", "4", "5", "6", "8"} else "4"

_lock = threading.Lock()  # one generation at a time; 16GB can't fit two


def _loaded_models() -> list:
    import ollama
    names = []
    for m in ollama.ps().get("models", []):
        name = m.get("model") if hasattr(m, "get") else getattr(m, "model", None)
        if name and "embed" not in name:   # the embedding model is tiny and her memory needs it
            names.append(name)
    return names


def _unload_chat_models(wait_s: float = 90, settle_checks: int = 3) -> None:
    """Evict Amy's chat/vision models and confirm they STAY gone before FLUX starts.

    A ~9.6 GB chat model plus FLUX (peak ~21.8 GB) doesn't fit in 16 GB. Raises JobBusy if a
    model is still loaded after wait_s: starting FLUX anyway is what froze the machine."""
    try:
        import ollama
        _loaded_models()
    except Exception:
        return                                   # Ollama isn't running: nothing is loaded
    deadline, quiet = time.time() + wait_s, 0
    while time.time() < deadline:
        loaded = _loaded_models()
        if not loaded:
            quiet += 1
            if quiet >= settle_checks:
                return
        else:
            quiet = 0
            for name in loaded:
                try:
                    ollama.generate(model=name, prompt="", keep_alive=0)
                except Exception:
                    pass
        time.sleep(2)
    raise heavy_job.JobBusy(f"couldn't unload the chat model ({_loaded_models()}); not starting FLUX")


def _exclusive():
    """Hold the machine for FLUX: close the gate, drain model calls, unload, then run."""
    from contextlib import contextmanager

    @contextmanager
    def cm():
        with heavy_job.exclusive("image generation"):
            _unload_chat_models()
            yield
    return cm()


def generate_image(prompt: str, seed: int | None = None, steps: int = 4) -> Path:
    """Run mflux as a subprocess (no shell) and return the image path."""
    if PAUSED:
        raise RuntimeError("Image generation is paused (set AMY_IMAGEGEN_PAUSED=0 to resume).")
    if not _lock.acquire(blocking=False):
        raise RuntimeError("An image is already being generated.")
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        seed = int(time.time()) if seed is None else seed
        out = OUT_DIR / f"amy_{seed}.png"
        cmd = [MFLUX_BIN, "--model", "schnell"]
        cmd += ["--path", MODEL_PATH] if MODEL_PATH else ["-q", FLUX_QUANT]
        cmd += ["--prompt", prompt, "--steps", str(steps),
                "--seed", str(seed), "--output", str(out)]

        with _exclusive():
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
    if PAUSED:
        raise RuntimeError("Image generation is paused (set AMY_IMAGEGEN_PAUSED=0 to resume).")
    if not _lock.acquire(blocking=False):
        raise RuntimeError("An image is already being generated.")
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        paths = []
        with _exclusive():   # only the FLUX runs hold the machine; ffmpeg below is light
            for i, prompt in enumerate(prompts):
                seed = int(time.time()) + i
                out = OUT_DIR / f"amy_{seed}.png"
                cmd = [MFLUX_BIN, "--model", "schnell"]
                cmd += ["--path", MODEL_PATH] if MODEL_PATH else ["-q", FLUX_QUANT]
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
