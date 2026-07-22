"""Text-to-speech for Ava — Piper (offline, free) + macOS afplay.

An LLM emits tokens, not audio, so voice-out stays external. This synthesizes
Ava's reply with a local Piper voice and plays it. Set AVA_VOICE to swap voices;
if the model file is missing it silently degrades to text-only.
"""
import os
import re
import sys
import shutil
import tempfile
import subprocess

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


def speak(text, emotion="neutral"):
    if not text:
        return
    print(f"\nAmy ({emotion}): {text}")
    if not os.path.exists(VOICE):
        return  # no voice model -> text-only

    wav = None
    try:
        wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        subprocess.run(
            [sys.executable, "-m", "piper", "-m", VOICE,
             "--length-scale", LENGTH_SCALE, "-f", wav],
            input=_phonetic(text).encode(), check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        player = _player()
        if player:
            subprocess.run(player + [wav], check=False)
    except Exception as e:  # noqa: BLE001 - never crash the chat loop on TTS
        print(f"  [voice] tts failed: {e}")
    finally:
        if wav and os.path.exists(wav):
            os.remove(wav)
