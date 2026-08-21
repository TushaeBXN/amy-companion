"""Text-to-speech for Ava — Piper (offline, free) + macOS afplay.

An LLM emits tokens, not audio, so voice-out stays external. This synthesizes
Ava's reply with a local Piper voice and plays it. Set AVA_VOICE to swap voices;
if the model file is missing it silently degrades to text-only.

speak()         — original single-shot synthesis (short replies, fallback)
speak_streamed() — sentence-pipelined: synthesizes next sentence while current
                   one plays. Significantly reduces perceived latency on long replies.
"""
import os
import queue
import re
import sys
import shutil
import tempfile
import subprocess
import threading
import time

# Spoken-only respellings for any names the TTS mangles (displayed text is
# unchanged). Amy pronounces "Amy" correctly, so this is empty; add tuples like
# (re.compile(r"\bName\b", re.I), "Phonetic") if a future name needs it.
_PRONUNCIATION = []


def _phonetic(text):
    for pat, repl in _PRONUNCIATION:
        text = pat.sub(repl, text)
    return text

_DEFAULT_VOICE = os.path.join(
    os.path.dirname(__file__), "voices", "en_US-amy-medium.onnx"
)
VOICE = os.environ.get("AVA_VOICE", _DEFAULT_VOICE)

# Optional speed control: >1.0 slower, <1.0 faster (Piper length_scale).
LENGTH_SCALE = os.environ.get("AVA_SPEED", "0.85")


def _player():
    if shutil.which("afplay"):
        return ["afplay"]
    if shutil.which("ffplay"):
        return ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet"]
    return None


def _synthesize_wav(text):
    """Synthesize one piece of text → temp WAV path, or None on failure."""
    if not text or not re.sub(r'[^\w]', '', text):
        return None
    if not os.path.exists(VOICE):
        return None
    try:
        wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        subprocess.run(
            [sys.executable, "-m", "piper", "-m", VOICE,
             "--length-scale", LENGTH_SCALE, "-f", wav],
            input=_phonetic(text).encode(), check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return wav
    except Exception as e:
        print(f"  [voice] synthesis failed: {e}")
        return None


def _play_wav(wav):
    """Play a WAV file and delete it."""
    player = _player()
    if player and wav and os.path.exists(wav):
        r = subprocess.run(player + [wav], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            time.sleep(0.3)
            subprocess.run(player + [wav], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if wav and os.path.exists(wav):
        os.remove(wav)


def speak(text, emotion="neutral"):
    """Single-shot synthesis — used for short replies and as a fallback."""
    if not text:
        return
    print(f"\nAmy ({emotion}): {text}")
    try:
        wav = _synthesize_wav(text)
        _play_wav(wav)
    except Exception as e:
        print(f"  [voice] tts failed: {e}")


# Sentence boundary — split after . ! ? followed by whitespace or end of string
_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')


def speak_streamed(text, emotion="neutral"):
    """Pipelined TTS: synthesize next sentence while current one plays.

    For responses longer than one sentence this starts speaking sooner and
    eliminates the gap between sentences. Falls back to speak() for short text.
    """
    if not text:
        return
    print(f"\nAmy ({emotion}): {text}")
    if not os.path.exists(VOICE):
        return

    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    sentences = [s for s in sentences if re.sub(r'[^\w]', '', s)]

    if len(sentences) <= 1:
        # Single sentence — no pipeline benefit, just synthesize and play
        try:
            _play_wav(_synthesize_wav(text))
        except Exception as e:
            print(f"  [voice] tts failed: {e}")
        return

    # Pipeline: synthesis thread fills wav_queue; main thread plays from it.
    wav_queue = queue.Queue(maxsize=2)  # stay at most 2 wavs ahead

    def _synth_worker():
        for sentence in sentences:
            wav = _synthesize_wav(sentence)
            wav_queue.put(wav)  # blocks if player is behind (backpressure)
        wav_queue.put(None)  # sentinel

    synth_thread = threading.Thread(target=_synth_worker, daemon=True)
    synth_thread.start()

    while True:
        wav = wav_queue.get()
        if wav is None:
            break
        try:
            _play_wav(wav)
        except Exception as e:
            print(f"  [voice] playback failed: {e}")

    synth_thread.join(timeout=5)
