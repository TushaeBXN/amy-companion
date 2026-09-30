"""Video generation (LTX-Video via diffusers) and Telegram delivery for Amy.

Env vars: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (same as imagegen.py)
Optional: AMY_VIDEO_DIR  (default ~/amy-videos)

First run downloads ~2.4 GB of model weights to ~/.cache/huggingface.
Generation takes ~5-15 min on M4 Mac Mini. Watch the log:
  tail -f ~/amy-videos/videogen.log
"""
import gc
import os
import threading
import time
from pathlib import Path

import requests

OUT_DIR = Path(os.path.expanduser(os.environ.get("AMY_VIDEO_DIR", "~/amy-videos")))
LOG_FILE = OUT_DIR / "videogen.log"

_lock = threading.Lock()  # one generation at a time; 16 GB can't fit two
_pipe = None
_pipe_lock = threading.Lock()


def _tg_token() -> str:
    return os.environ["TELEGRAM_TOKEN"]


def _tg_chat() -> str:
    return os.environ["TELEGRAM_CHAT_ID"]


def _ltx_frames(seconds: int) -> int:
    # LTX-Video requires frame counts of the form (N * 8) + 1: 9, 17, 25 … 97
    raw = max(1, seconds) * 24
    n = max(1, round(raw / 8))
    return n * 8 + 1


def _load_pipeline():
    global _pipe
    if _pipe is not None:
        return _pipe
    with _pipe_lock:
        if _pipe is not None:
            return _pipe
        import torch
        from diffusers import LTXPipeline
        _pipe = LTXPipeline.from_pretrained(
            "Lightricks/LTX-Video",
            torch_dtype=torch.bfloat16,
        ).to("mps")
        return _pipe


def generate_video(prompt: str, seed: int | None = None, seconds: int = 3) -> Path:
    """Run LTX-Video on Apple Silicon MPS and return the .mp4 path."""
    if not _lock.acquire(blocking=False):
        raise RuntimeError("A video is already being generated.")
    try:
        import torch
        from diffusers.utils import export_to_video

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        seed = int(time.time()) if seed is None else seed
        out = OUT_DIR / f"amy_{seed}.mp4"
        num_frames = _ltx_frames(seconds)

        with open(LOG_FILE, "a") as log:
            log.write(f"\n=== {time.ctime()} :: {prompt!r} ({seconds}s, {num_frames} frames)\n")
            log.flush()

        pipe = _load_pipeline()
        generator = torch.Generator("mps").manual_seed(seed)

        result = pipe(
            prompt=prompt,
            negative_prompt="worst quality, inconsistent motion, blurry, jittery, distorted",
            width=512,
            height=288,
            num_frames=num_frames,
            num_inference_steps=40,
            generator=generator,
        )

        export_to_video(result.frames[0], str(out), fps=24)

        if not out.exists():
            raise RuntimeError(f"LTX-Video output missing at {out}")

        with open(LOG_FILE, "a") as log:
            log.write(f"  -> {out} ({out.stat().st_size // 1024} KB)\n")

        return out
    finally:
        _lock.release()


def send_telegram_video(path: Path, caption: str = "") -> None:
    try:
        with open(path, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{_tg_token()}/sendVideo",
                data={"chat_id": _tg_chat(), "caption": caption[:1000]},
                files={"video": f},
                timeout=120,
            )
        r.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Telegram send failed ({type(e).__name__})") from None


def _notify(text: str) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{_tg_token()}/sendMessage",
            json={"chat_id": _tg_chat(), "text": text[:4000]},
            timeout=15,
        )
    except Exception:
        pass


def generate_and_send(prompt: str, caption: str = "") -> str:
    """The tool Amy calls. Sends the video (or error) to Telegram; returns a status string."""
    try:
        path = generate_video(prompt)
        send_telegram_video(path, caption or prompt)
        return f"Done. Generated and sent {path.name}."
    except Exception as e:
        msg = f"Video generation failed: {e}"
        _notify(msg)
        return msg
